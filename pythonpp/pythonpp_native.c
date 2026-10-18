/* ============================================================================
 * pythonpp_native.c —— Python++ 的 C 加速内核
 * ----------------------------------------------------------------------------
 * 设计目标
 *   1. 轻量    ：一个文件、一个 .pyd，无第三方依赖，不链 libm、不引 STL。
 *   2. 兼容    ：只用 CPython 稳定 ABI（Py_LIMITED_API 3.8）。
 *                同一个 .pyd 可在 CPython 3.8 / 3.9 / ... / 3.14 上直接加载，
 *                不必为每个 Python 版本重新编译。
 *   3. 提速    ：把原来在 Python 层"逐对象、逐整数"跑的循环搬到 C 里。
 *
 * 提供的函数
 *   seed(v)                  重置随机数种子
 *   random_int(lo, hi)       闭区间随机整数
 *   random_float()           [0, 1) 随机浮点数
 *   random_bytes(n)          n 字节随机数据
 *   shuffle(list)            原地洗牌
 *   sum_int(iterable)        整数快速求和（自动提升为 Python 大整数）
 *   dot(a, b)                点积
 *   sum_range(a, b, step)    等差数列求和，O(1)
 *   deep_sizeof(obj)         深度内存估算
 *   hash64(data)             64 位哈希
 *   loop_run(code, args)     运行整数循环字节码（JIT 的执行后端）
 *   info()                   内核信息
 *
 * 编译：python build_native.py
 * ==========================================================================*/

#ifndef Py_LIMITED_API
#define Py_LIMITED_API 0x030800
#endif
#include <Python.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

#define PP_NATIVE_VERSION "1.0.0"

/* 说明：这里刻意不定义 PY_SSIZE_T_CLEAN。
 * 它只影响 "s#" / "y#" 这类带 # 的格式符，而我们一个都没用；
 * 不定义反而能避开某些发行版里 _PyArg_ParseTuple_SizeT 未导出的坑。 */

/* ==========================================================================
 * 一、随机数：xoshiro256**  + splitmix64 播种
 * 纯整数运算，比 Python 层的 xorshift 少几十次对象创建与拆箱。
 * ==========================================================================*/

static uint64_t pp_state[4] = {
    0x9E3779B97F4A7C15ULL,
    0xBF58476D1CE4E5B9ULL,
    0x94D049BB133111EBULL,
    0x2545F4914F6CDD1DULL
};

static uint64_t pp_rotl(const uint64_t x, int k) {
    return (x << k) | (x >> (64 - k));
}

static uint64_t pp_splitmix64(uint64_t *x) {
    uint64_t z = (*x += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

static void pp_seed_u64(uint64_t seed) {
    uint64_t x = seed ? seed : 0x123456789ABCDEFULL;
    int i;
    for (i = 0; i < 4; i++) {
        pp_state[i] = pp_splitmix64(&x);
    }
    /* 全零是 xoshiro 的退化状态，必须避免 */
    if ((pp_state[0] | pp_state[1] | pp_state[2] | pp_state[3]) == 0) {
        pp_state[0] = 1;
    }
}

static uint64_t pp_next(void) {
    const uint64_t result = pp_rotl(pp_state[1] * 5, 7) * 9;
    const uint64_t t = pp_state[1] << 17;

    pp_state[2] ^= pp_state[0];
    pp_state[3] ^= pp_state[1];
    pp_state[1] ^= pp_state[2];
    pp_state[0] ^= pp_state[3];
    pp_state[2] ^= t;
    pp_state[3] = pp_rotl(pp_state[3], 45);
    return result;
}

/* 均匀分布在 [0, range) 的 64 位整数（拒绝采样，无偏） */
static uint64_t pp_below(uint64_t range) {
    uint64_t r, threshold;
    if (range <= 1) {
        pp_next();
        return 0;
    }
    threshold = (uint64_t)(0 - range) % range;   /* 2^64 % range */
    do {
        r = pp_next();
    } while (r < threshold);
    return r % range;
}

static PyObject *pp_seed(PyObject *self, PyObject *args) {
    PyObject *value = Py_None;
    if (!PyArg_ParseTuple(args, "|O", &value)) {
        return NULL;
    }
    if (value == Py_None) {
        uint64_t entropy = (uint64_t)time(NULL);
        entropy ^= (uint64_t)(uintptr_t)&entropy << 21;
        entropy ^= (uint64_t)(uintptr_t)self << 7;
        pp_seed_u64(entropy);
    } else {
        long long s = PyLong_AsLongLong(value);
        if (s == -1 && PyErr_Occurred()) {
            return NULL;
        }
        pp_seed_u64((uint64_t)s);
    }
    Py_RETURN_NONE;
}

static PyObject *pp_random_int(PyObject *self, PyObject *args) {
    PyObject *pa, *pb;
    long long lo, hi;
    uint64_t span;

    if (!PyArg_ParseTuple(args, "OO", &pa, &pb)) {
        return NULL;
    }
    lo = PyLong_AsLongLong(pa);
    if (lo == -1 && PyErr_Occurred()) {
        return NULL;
    }
    hi = PyLong_AsLongLong(pb);
    if (hi == -1 && PyErr_Occurred()) {
        return NULL;
    }
    if (hi < lo) {
        long long tmp = lo;
        lo = hi;
        hi = tmp;
    }
    /* 闭区间长度：hi - lo + 1。用补码直接算，避免溢出 */
    span = (uint64_t)hi - (uint64_t)lo;
    if (span == UINT64_MAX) {             /* 覆盖整个 64 位区间 */
        return PyLong_FromLongLong((long long)((uint64_t)lo + pp_next()));
    }
    return PyLong_FromLongLong((long long)((uint64_t)lo + pp_below(span + 1)));
}

static PyObject *pp_random_float(PyObject *self, PyObject *args) {
    /* 取高 53 位，得到 [0, 1) 上均匀分布的 double */
    double value = (double)(pp_next() >> 11) * (1.0 / 9007199254740992.0);
    return PyFloat_FromDouble(value);
}

static PyObject *pp_random_bytes(PyObject *self, PyObject *args) {
    Py_ssize_t n, i;
    PyObject *result;
    unsigned char *buf;

    if (!PyArg_ParseTuple(args, "n", &n)) {
        return NULL;
    }
    if (n < 0) {
        PyErr_SetString(PyExc_ValueError, "字节数不能为负数");
        return NULL;
    }
    result = PyBytes_FromStringAndSize(NULL, n);
    if (!result) {
        return NULL;
    }
    buf = (unsigned char *)PyBytes_AsString(result);
    if (!buf) {
        Py_DECREF(result);
        return NULL;
    }
    i = 0;
    while (i < n) {
        uint64_t r = pp_next();
        int k;
        for (k = 0; k < 8 && i < n; k++, i++) {
            buf[i] = (unsigned char)(r & 0xFF);
            r >>= 8;
        }
    }
    return result;
}

static PyObject *pp_shuffle(PyObject *self, PyObject *args) {
    PyObject *seq;
    Py_ssize_t n, i;

    if (!PyArg_ParseTuple(args, "O!", &PyList_Type, &seq)) {
        return NULL;
    }
    n = PyList_Size(seq);
    if (n < 0) {
        return NULL;
    }
    for (i = n - 1; i > 0; i--) {
        Py_ssize_t j = (Py_ssize_t)pp_below((uint64_t)i + 1);
        PyObject *a, *b;
        if (j == i) {
            continue;
        }
        a = PyList_GetItem(seq, i);
        b = PyList_GetItem(seq, j);
        if (!a || !b) {
            return NULL;
        }
        Py_INCREF(a);
        Py_INCREF(b);
        /* PyList_SetItem 会"偷走"引用 */
        if (PyList_SetItem(seq, i, b) < 0) {
            Py_DECREF(a);
            return NULL;
        }
        if (PyList_SetItem(seq, j, a) < 0) {
            return NULL;
        }
    }
    Py_RETURN_NONE;
}

/* ==========================================================================
 * 二、数值：整数求和 / 点积 / 等差数列
 * 关键点：全部在 int64 上累加，只有真正溢出时才切换到 Python 大整数，
 *         既快又不会算错。
 * ==========================================================================*/

#if defined(__GNUC__) || defined(__clang__)
#define PP_ADD_OVERFLOW(a, b, out) __builtin_add_overflow((a), (b), (out))
#define PP_MUL_OVERFLOW(a, b, out) __builtin_mul_overflow((a), (b), (out))
#else
#define PP_ADD_OVERFLOW(a, b, out) \
    (*(out) = (long long)((unsigned long long)(a) + (unsigned long long)(b)), 0)
#define PP_MUL_OVERFLOW(a, b, out) \
    (*(out) = (long long)((unsigned long long)(a) * (unsigned long long)(b)), 0)
#endif

static PyObject *pp_sum_int(PyObject *self, PyObject *args) {
    PyObject *seq, *iter, *item, *acc_obj = NULL;
    long long acc = 0;
    PyObject *result = NULL;

    if (!PyArg_ParseTuple(args, "O", &seq)) {
        return NULL;
    }

    /* 快速路径：list / tuple 直接按下标取，省掉迭代器协议的开销。
     * 内置 sum() 也是这么做的，所以我们要比它更快就必须走这条路。
     * 注意：稳定 ABI 下用不了 PyList_GET_ITEM 这类宏，只能用函数版。 */
    if (PyList_Check(seq) || PyTuple_Check(seq)) {
        Py_ssize_t i, n = PySequence_Size(seq);
        int all_int = (n >= 0);
        for (i = 0; all_int && i < n; i++) {
            PyObject *it = PySequence_GetItem(seq, i);   /* 新引用 */
            long long v, nv;
            if (!it) {
                return NULL;
            }
            if (!PyLong_CheckExact(it)) {
                Py_DECREF(it);
                all_int = 0;
                break;
            }
            v = PyLong_AsLongLong(it);
            Py_DECREF(it);
            if (v == -1 && PyErr_Occurred()) {
                PyErr_Clear();
                all_int = 0;
                break;
            }
            if (PP_ADD_OVERFLOW(acc, v, &nv)) {
                all_int = 0;
                acc = 0;
                break;
            }
            acc = nv;
        }
        if (all_int) {
            return PyLong_FromLongLong(acc);
        }
        acc = 0;
    }

    iter = PyObject_GetIter(seq);
    if (!iter) {
        return NULL;
    }
    while ((item = PyIter_Next(iter)) != NULL) {
        int done = 0;
        if (acc_obj == NULL && PyLong_CheckExact(item)) {
            long long v = PyLong_AsLongLong(item);
            if (!(v == -1 && PyErr_Occurred())) {
                long long nv;
                if (!PP_ADD_OVERFLOW(acc, v, &nv)) {
                    acc = nv;
                    Py_DECREF(item);
                    done = 1;
                }
            } else {
                PyErr_Clear();   /* 超大整数：交给下面的通用路径 */
            }
        }
        if (!done) {
            PyObject *nv;
            if (acc_obj == NULL) {
                acc_obj = PyLong_FromLongLong(acc);
                acc = 0;
                if (!acc_obj) {
                    Py_DECREF(item);
                    goto done;
                }
            }
            nv = PyNumber_Add(acc_obj, item);
            Py_DECREF(item);
            if (!nv) {
                goto done;
            }
            Py_DECREF(acc_obj);
            acc_obj = nv;
        }
    }
    if (PyErr_Occurred()) {
        goto done;
    }
    if (acc_obj != NULL) {
        result = acc_obj;
        acc_obj = NULL;
    } else {
        result = PyLong_FromLongLong(acc);
    }

done:
    Py_XDECREF(acc_obj);
    Py_DECREF(iter);
    return result;
}

static PyObject *pp_dot(PyObject *self, PyObject *args) {
    PyObject *pa, *pb, *ia = NULL, *ib = NULL;
    PyObject *acc_obj = NULL;
    long long acc = 0;
    PyObject *result = NULL;

    if (!PyArg_ParseTuple(args, "OO", &pa, &pb)) {
        return NULL;
    }
    ia = PyObject_GetIter(pa);
    if (!ia) {
        return NULL;
    }
    ib = PyObject_GetIter(pb);
    if (!ib) {
        Py_DECREF(ia);
        return NULL;
    }
    for (;;) {
        PyObject *va = PyIter_Next(ia);
        PyObject *vb;
        if (va == NULL) {
            if (PyErr_Occurred()) {
                goto done;
            }
            break;
        }
        vb = PyIter_Next(ib);
        if (vb == NULL) {
            Py_DECREF(va);
            if (!PyErr_Occurred()) {
                PyErr_SetString(PyExc_ValueError, "dot() 两个序列长度不一致");
            }
            goto done;
        }
        if (acc_obj == NULL && PyLong_CheckExact(va) && PyLong_CheckExact(vb)) {
            long long a = PyLong_AsLongLong(va);
            long long b = PyLong_AsLongLong(vb);
            int ok = !((a == -1 || b == -1) && PyErr_Occurred());
            if (ok) {
                long long prod, nv;
                if (!PP_MUL_OVERFLOW(a, b, &prod) && !PP_ADD_OVERFLOW(acc, prod, &nv)) {
                    acc = nv;
                    Py_DECREF(va);
                    Py_DECREF(vb);
                    continue;
                }
            } else {
                PyErr_Clear();
            }
        }
        {
            PyObject *prod = PyNumber_Multiply(va, vb);
            PyObject *nv;
            Py_DECREF(va);
            Py_DECREF(vb);
            if (!prod) {
                goto done;
            }
            if (acc_obj == NULL) {
                acc_obj = PyLong_FromLongLong(acc);
                acc = 0;
                if (!acc_obj) {
                    Py_DECREF(prod);
                    goto done;
                }
            }
            nv = PyNumber_Add(acc_obj, prod);
            Py_DECREF(prod);
            if (!nv) {
                goto done;
            }
            Py_DECREF(acc_obj);
            acc_obj = nv;
        }
    }
    /* 检查 b 是否还有多余元素 */
    {
        PyObject *extra = PyIter_Next(ib);
        if (extra != NULL) {
            Py_DECREF(extra);
            PyErr_SetString(PyExc_ValueError, "dot() 两个序列长度不一致");
            goto done;
        }
        if (PyErr_Occurred()) {
            goto done;
        }
    }
    if (acc_obj != NULL) {
        result = acc_obj;
        acc_obj = NULL;
    } else {
        result = PyLong_FromLongLong(acc);
    }

done:
    Py_XDECREF(acc_obj);
    Py_XDECREF(ia);
    Py_XDECREF(ib);
    return result;
}

static PyObject *pp_sum_range(PyObject *self, PyObject *args) {
    long long a, b, step = 1;
    long long n;

    /* 注意：必须用 "L"（long long）而不是 "l"（C long）。
     * Windows 上 long 只有 32 位，用 "l" 只写低 4 字节，会留下垃圾高位。 */
    if (!PyArg_ParseTuple(args, "LL|L", &a, &b, &step)) {
        return NULL;
    }
    if (step == 0) {
        PyErr_SetString(PyExc_ValueError, "range() 的步长不能为 0");
        return NULL;
    }
    /* 项数 n = max(0, ceil((b - a) / step))，用整数运算避免浮点误差 */
    if (step > 0) {
        if (b <= a) {
            n = 0;
        } else {
            unsigned long long diff = (unsigned long long)b - (unsigned long long)a;
            unsigned long long s = (unsigned long long)step;
            n = (long long)((diff + s - 1) / s);
        }
    } else {
        if (b >= a) {
            n = 0;
        } else {
            unsigned long long diff = (unsigned long long)a - (unsigned long long)b;
            unsigned long long s = (unsigned long long)(-step);
            n = (long long)((diff + s - 1) / s);
        }
    }
    if (n <= 0) {
        return PyLong_FromLongLong(0);
    }
#if defined(__SIZEOF_INT128__)
    {
        /* 用 __int128 顶住中间结果，超出 int64 就抛 OverflowError 让 Python 兜底 */
        __int128 nn = (__int128)n;
        __int128 ss = (__int128)step;
        __int128 aa = (__int128)a;
        __int128 tot = nn * (aa + (aa + (nn - 1) * ss)) / 2;
        if (tot > (__int128)INT64_MAX || tot < (__int128)INT64_MIN) {
            PyErr_SetString(PyExc_OverflowError, "sum_range 结果超出 int64，请用 Python 版本");
            return NULL;
        }
        return PyLong_FromLongLong((long long)tot);
    }
#else
    {
        long long last, half, total;
        if (PP_MUL_OVERFLOW(n - 1, step, &last) || PP_ADD_OVERFLOW(a, last, &last)) {
            PyErr_SetString(PyExc_OverflowError, "sum_range 结果超出 int64，请用 Python 版本");
            return NULL;
        }
        if (PP_ADD_OVERFLOW(a, last, &half)) {
            PyErr_SetString(PyExc_OverflowError, "sum_range 结果超出 int64，请用 Python 版本");
            return NULL;
        }
        half = half / 2;
        if (PP_MUL_OVERFLOW(n, half, &total)) {
            PyErr_SetString(PyExc_OverflowError, "sum_range 结果超出 int64，请用 Python 版本");
            return NULL;
        }
        return PyLong_FromLongLong(total);
    }
#endif
}

/* ==========================================================================
 * 三、内存：深度 sizeof
 * 用显式栈代替递归，避免深嵌套容器把 C 栈撑爆；
 * 用 dict 当"已访问集合"，靠对象地址去重（环状结构安全）。
 * ==========================================================================*/

static PyObject *pp_getsizeof = NULL;   /* 缓存的 sys.getsizeof */

static PyObject *pp_deep_sizeof(PyObject *self, PyObject *args) {
    PyObject *root;
    PyObject *stack = NULL;
    PyObject *seen = NULL;
    PyObject *result = NULL;
    long long total = 0;

    if (!PyArg_ParseTuple(args, "O", &root)) {
        return NULL;
    }
    if (!pp_getsizeof) {
        PyErr_SetString(PyExc_RuntimeError, "内核还没有初始化 sys.getsizeof");
        return NULL;
    }
    stack = PyList_New(0);
    if (!stack) {
        return NULL;
    }
    seen = PyDict_New();
    if (!seen) {
        Py_DECREF(stack);
        return NULL;
    }
    if (PyList_Append(stack, root) < 0) {
        goto done;
    }

    while (PyList_Size(stack) > 0) {
        Py_ssize_t top = PyList_Size(stack) - 1;
        PyObject *cur = PyList_GetItem(stack, top);   /* 借用引用 */
        PyObject *key, *size_obj;
        long long size;

        if (!cur) {
            goto done;
        }
        Py_INCREF(cur);
        if (PyList_SetSlice(stack, top, top + 1, NULL) < 0) {
            Py_DECREF(cur);
            goto done;
        }

        key = PyLong_FromVoidPtr(cur);
        if (!key) {
            Py_DECREF(cur);
            goto done;
        }
        if (PyDict_Contains(seen, key)) {
            Py_DECREF(key);
            Py_DECREF(cur);
            continue;             /* 见过了：环或共享子对象 */
        }
        if (PyDict_SetItem(seen, key, Py_True) < 0) {
            Py_DECREF(key);
            Py_DECREF(cur);
            goto done;
        }
        Py_DECREF(key);

        size_obj = PyObject_CallFunctionObjArgs(pp_getsizeof, cur, NULL);
        if (size_obj == NULL) {
            PyErr_Clear();        /* 极少数对象不支持 getsizeof，跳过即可 */
        } else {
            size = PyLong_AsLongLong(size_obj);
            Py_DECREF(size_obj);
            if (size == -1 && PyErr_Occurred()) {
                PyErr_Clear();
            } else {
                total += size;
            }
        }

        /* 展开子对象 */
        if (PyDict_Check(cur)) {
            Py_ssize_t pos = 0;
            PyObject *k, *v;
            while (PyDict_Next(cur, &pos, &k, &v)) {
                if (PyList_Append(stack, k) < 0 || PyList_Append(stack, v) < 0) {
                    Py_DECREF(cur);
                    goto done;
                }
            }
        } else if (PyList_Check(cur) || PyTuple_Check(cur) || PyAnySet_Check(cur)) {
            PyObject *iter = PyObject_GetIter(cur);
            PyObject *child;
            if (!iter) {
                Py_DECREF(cur);
                goto done;
            }
            while ((child = PyIter_Next(iter)) != NULL) {
                if (PyList_Append(stack, child) < 0) {
                    Py_DECREF(child);
                    Py_DECREF(iter);
                    Py_DECREF(cur);
                    goto done;
                }
                Py_DECREF(child);
            }
            Py_DECREF(iter);
            if (PyErr_Occurred()) {
                Py_DECREF(cur);
                goto done;
            }
        }
        Py_DECREF(cur);
    }
    result = PyLong_FromLongLong(total);

done:
    Py_XDECREF(stack);
    Py_XDECREF(seen);
    return result;
}

/* ==========================================================================
 * 四、杂项：64 位哈希
 * ==========================================================================*/

static uint64_t pp_fnv1a(const unsigned char *data, Py_ssize_t n) {
    uint64_t h = 0xCBF29CE484222325ULL;
    Py_ssize_t i;
    for (i = 0; i < n; i++) {
        h ^= (uint64_t)data[i];
        h *= 0x100000001B3ULL;
    }
    return h;
}

static PyObject *pp_hash64(PyObject *self, PyObject *args) {
    PyObject *obj, *enc;
    uint64_t h;

    if (!PyArg_ParseTuple(args, "O", &obj)) {
        return NULL;
    }
    if (PyBytes_Check(obj)) {
        return PyLong_FromUnsignedLongLong(
            (unsigned long long)pp_fnv1a((const unsigned char *)PyBytes_AsString(obj),
                                         PyBytes_Size(obj)));
    }
    if (PyUnicode_Check(obj)) {
        enc = PyUnicode_AsUTF8String(obj);
    } else {
        PyObject *s = PyObject_Str(obj);
        if (!s) {
            return NULL;
        }
        enc = PyUnicode_AsUTF8String(s);
        Py_DECREF(s);
    }
    if (!enc) {
        return NULL;
    }
    h = pp_fnv1a((const unsigned char *)PyBytes_AsString(enc), PyBytes_Size(enc));
    Py_DECREF(enc);
    return PyLong_FromUnsignedLongLong((unsigned long long)h);
}

/* ==========================================================================
 * 五、整数循环虚拟机（Python++ JIT 的执行后端）
 * ----------------------------------------------------------------------------
 * Python 里最慢的循环长这样：
 *
 *     total = 0
 *     for i in range(1000000):
 *         if i % 3 == 0:
 *             total += i
 *
 * 每次迭代都要几十次对象创建/销毁。这里把循环体编译成一串栈式指令，
 * 用 int64 直接跑，完全不碰 Python 对象；只有出结果时才装箱。
 *
 * 指令固定 24 字节，字段：op / a / b / c / imm
 *   PP_PUSH_IMM   imm      压入 64 位立即数
 *   PP_PUSH_VAR   a        压入变量 a
 *   PP_STORE      a        弹出栈顶写入变量 a
 *   PP_ADD/SUB/MUL/DIV/MOD/AND/OR/XOR/SHL/SHR
 *   PP_NEG                 取负
 *   PP_CMP        a=cond imm=target   弹出 b、a；cond 成立就跳
 *   PP_JMP        imm=target
 *   PP_LOOP_CHECK a=i b=stop c=step imm=exit
 *                          按 range 语义判断是否还要继续；结束就跳到 exit
 *   PP_OUT                 把栈顶推入输出列表
 *   PP_HALT                结束
 *   cond: 0 ==, 1 !=, 2 <, 3 <=, 4 >, 5 >=
 *
 * 除零抛 ZeroDivisionError；溢出/移位越界/步数超限抛 OverflowError，
 * 由 Python 侧自动回退到大整数实现，所以"提速"永远不会让结果变错。
 * ==========================================================================*/

enum {
    PP_PUSH_IMM = 1,
    PP_PUSH_VAR,
    PP_STORE,
    PP_ADD,
    PP_SUB,
    PP_MUL,
    PP_DIV,
    PP_MOD,
    PP_AND,
    PP_OR,
    PP_XOR,
    PP_SHL,
    PP_SHR,
    PP_NEG,
    PP_CMP,
    PP_JMP,
    PP_LOOP_CHECK,
    PP_OUT,
    PP_HALT
};

enum { COND_EQ = 0, COND_NE, COND_LT, COND_LE, COND_GT, COND_GE };

#pragma pack(push, 1)
typedef struct {
    uint8_t op;      /* 偏移 0  */
    int16_t a;       /* 偏移 1  */
    int16_t b;       /* 偏移 3  */
    int32_t c;       /* 偏移 5  */
    int64_t imm;     /* 偏移 9  */
    uint8_t pad[7];  /* 偏移 17，补齐到 24 */
} pp_ins;
#pragma pack(pop)

#define PP_INS_SIZE 24
#define PP_IMM_OFFSET 9
#define PP_STACK_MAX 64
#define PP_VAR_MAX 16
#define PP_STEP_LIMIT 400000000LL   /* 安全阀：单次运行最多 4 亿步 */

/* Python 的 % 跟 C 不一样：结果符号跟随除数。这里补齐语义 */
static long long pp_py_mod(long long x, long long y) {
    long long r = x % y;
    if (r != 0 && ((r ^ y) < 0)) {
        r += y;
    }
    return r;
}

/* Python 的 // 是向下取整，C 的 / 是向零取整 */
static long long pp_py_floordiv(long long x, long long y) {
    long long q = x / y;
    long long r = x % y;
    if (r != 0 && ((r ^ y) < 0)) {
        q -= 1;
    }
    return q;
}

static PyObject *pp_loop_run(PyObject *self, PyObject *args) {
    PyObject *code_obj, *args_obj;
    PyObject *code_bytes = NULL, *args_tuple = NULL, *results = NULL;
    const uint8_t *raw;
    long long vars[PP_VAR_MAX];
    long long stack[PP_STACK_MAX];
    unsigned char var_used[PP_VAR_MAX];
    Py_ssize_t ncode, nargs, sp = 0, pc = 0;
    long long steps = 0;
    PyObject *result = NULL;

    if (!PyArg_ParseTuple(args, "OO", &code_obj, &args_obj)) {
        return NULL;
    }
    code_bytes = PyBytes_FromObject(code_obj);
    if (!code_bytes) {
        return NULL;
    }
    ncode = PyBytes_Size(code_bytes);
    if (ncode % PP_INS_SIZE != 0) {
        PyErr_SetString(PyExc_ValueError, "jit 指令流长度不是 24 的倍数");
        goto done;
    }
    ncode /= PP_INS_SIZE;
    raw = (const uint8_t *)PyBytes_AsString(code_bytes);
    if (!raw) {
        goto done;
    }

    args_tuple = PySequence_Tuple(args_obj);
    if (!args_tuple) {
        goto done;
    }
    nargs = PyTuple_Size(args_tuple);
    if (nargs < 0) {
        goto done;
    }
    if (nargs > PP_VAR_MAX) {
        PyErr_SetString(PyExc_ValueError, "jit 参数个数超过上限 16");
        goto done;
    }
    memset(var_used, 0, sizeof(var_used));
    memset(vars, 0, sizeof(vars));
    {
        Py_ssize_t i;
        for (i = 0; i < nargs; i++) {
            PyObject *item = PyTuple_GetItem(args_tuple, i);
            long long v;
            if (!item) {
                goto done;
            }
            if (!PyLong_Check(item)) {
                PyErr_SetString(PyExc_TypeError, "jit 循环的入参必须是整数");
                goto done;
            }
            v = PyLong_AsLongLong(item);
            if (v == -1 && PyErr_Occurred()) {
                PyErr_Clear();
                PyErr_SetString(PyExc_OverflowError, "jit 入参超出 int64");
                goto done;
            }
            vars[i] = v;
            var_used[i] = 1;
        }
    }
    results = PyList_New(0);
    if (!results) {
        goto done;
    }

    while (pc >= 0 && pc < ncode) {
        const uint8_t *ip = raw + pc * PP_INS_SIZE;
        pp_ins in;
        long long x, y, z;

        memcpy(&in, ip, sizeof(in));
        if (++steps > PP_STEP_LIMIT) {
            PyErr_SetString(PyExc_OverflowError, "jit 循环步数超过安全上限");
            goto done;
        }
        switch (in.op) {
        case PP_PUSH_IMM:
            if (sp >= PP_STACK_MAX) {
                PyErr_SetString(PyExc_OverflowError, "表达式嵌套过深");
                goto done;
            }
            stack[sp++] = in.imm;
            pc++;
            break;
        case PP_PUSH_VAR:
            if (in.a < 0 || in.a >= PP_VAR_MAX || !var_used[in.a]) {
                PyErr_Format(PyExc_NameError, "jit 里没有编号为 %d 的变量", (int)in.a);
                goto done;
            }
            if (sp >= PP_STACK_MAX) {
                PyErr_SetString(PyExc_OverflowError, "表达式嵌套过深");
                goto done;
            }
            stack[sp++] = vars[in.a];
            pc++;
            break;
        case PP_STORE:
            if (sp <= 0 || in.a < 0 || in.a >= PP_VAR_MAX) {
                PyErr_SetString(PyExc_ValueError, "STORE 指令非法");
                goto done;
            }
            vars[in.a] = stack[--sp];
            var_used[in.a] = 1;
            pc++;
            break;
        case PP_ADD:
        case PP_SUB:
        case PP_MUL:
            if (sp < 2) {
                PyErr_SetString(PyExc_ValueError, "栈下溢");
                goto done;
            }
            y = stack[--sp];
            x = stack[--sp];
            if (in.op == PP_ADD) {
                if (PP_ADD_OVERFLOW(x, y, &z)) goto overflow;
            } else if (in.op == PP_SUB) {
                if (PP_ADD_OVERFLOW(x, (long long)(0ULL - (unsigned long long)y), &z)) goto overflow;
            } else {
                if (PP_MUL_OVERFLOW(x, y, &z)) goto overflow;
            }
            stack[sp++] = z;
            pc++;
            break;
        case PP_DIV:
        case PP_MOD:
            if (sp < 2) {
                PyErr_SetString(PyExc_ValueError, "栈下溢");
                goto done;
            }
            y = stack[--sp];
            x = stack[--sp];
            if (y == 0) {
                PyErr_SetString(PyExc_ZeroDivisionError, "整数除以零");
                goto done;
            }
            if (x == INT64_MIN && y == -1) goto overflow;
            stack[sp++] = (in.op == PP_DIV) ? pp_py_floordiv(x, y) : pp_py_mod(x, y);
            pc++;
            break;
        case PP_AND:
        case PP_OR:
        case PP_XOR:
            if (sp < 2) {
                PyErr_SetString(PyExc_ValueError, "栈下溢");
                goto done;
            }
            y = stack[--sp];
            x = stack[--sp];
            stack[sp++] = (in.op == PP_AND) ? (x & y) : (in.op == PP_OR) ? (x | y) : (x ^ y);
            pc++;
            break;
        case PP_SHL:
        case PP_SHR:
            if (sp < 2) {
                PyErr_SetString(PyExc_ValueError, "栈下溢");
                goto done;
            }
            y = stack[--sp];
            x = stack[--sp];
            if (y < 0 || y > 63) goto overflow;
            stack[sp++] = (in.op == PP_SHL) ? (long long)((unsigned long long)x << y) : (x >> y);
            pc++;
            break;
        case PP_NEG:
            if (sp < 1) {
                PyErr_SetString(PyExc_ValueError, "栈下溢");
                goto done;
            }
            x = stack[sp - 1];
            if (x == INT64_MIN) goto overflow;
            stack[sp - 1] = -x;
            pc++;
            break;
        case PP_CMP: {
            int take = 0;
            if (sp < 2) {
                PyErr_SetString(PyExc_ValueError, "栈下溢");
                goto done;
            }
            y = stack[--sp];
            x = stack[--sp];
            switch (in.a) {
            case COND_EQ: take = (x == y); break;
            case COND_NE: take = (x != y); break;
            case COND_LT: take = (x < y); break;
            case COND_LE: take = (x <= y); break;
            case COND_GT: take = (x > y); break;
            case COND_GE: take = (x >= y); break;
            default:
                PyErr_SetString(PyExc_ValueError, "未知的比较条件");
                goto done;
            }
            if (take) {
                if (in.imm < 0 || in.imm >= ncode) {
                    PyErr_SetString(PyExc_ValueError, "跳转目标越界");
                    goto done;
                }
                pc = (Py_ssize_t)in.imm;
            } else {
                pc++;
            }
            break;
        }
        case PP_JMP:
            if (in.imm < 0 || in.imm >= ncode) {
                PyErr_SetString(PyExc_ValueError, "跳转目标越界");
                goto done;
            }
            pc = (Py_ssize_t)in.imm;
            break;
        case PP_LOOP_CHECK: {
            long long iv, sv, tv;
            if (in.a < 0 || in.a >= PP_VAR_MAX || in.b < 0 || in.b >= PP_VAR_MAX ||
                in.c < 0 || in.c >= PP_VAR_MAX) {
                PyErr_SetString(PyExc_ValueError, "LOOP_CHECK 槽位非法");
                goto done;
            }
            iv = vars[in.a];
            sv = vars[in.b];
            tv = vars[in.c];
            if (tv == 0) {
                PyErr_SetString(PyExc_ValueError, "range() 的步长不能为 0");
                goto done;
            }
            if ((tv > 0) ? (iv < sv) : (iv > sv)) {
                pc++;
            } else {
                if (in.imm < 0 || in.imm >= ncode) {
                    PyErr_SetString(PyExc_ValueError, "跳转目标越界");
                    goto done;
                }
                pc = (Py_ssize_t)in.imm;
            }
            break;
        }
        case PP_OUT: {
            PyObject *v;
            if (sp < 1) {
                PyErr_SetString(PyExc_ValueError, "栈下溢");
                goto done;
            }
            v = PyLong_FromLongLong(stack[--sp]);
            if (!v) {
                goto done;
            }
            if (PyList_Append(results, v) < 0) {
                Py_DECREF(v);
                goto done;
            }
            Py_DECREF(v);
            pc++;
            break;
        }
        case PP_HALT:
            result = results;
            results = NULL;
            goto done;
        default:
            PyErr_Format(PyExc_ValueError, "未知的 jit 指令 %d", (int)in.op);
            goto done;
        }
    }
    /* 跑到末尾没有 HALT：当作正常结束 */
    result = results;
    results = NULL;
    goto done;

overflow:
    PyErr_Clear();
    PyErr_SetString(PyExc_OverflowError, "jit 整型运算溢出，回退到 Python 大整数");
done:
    Py_XDECREF(results);
    Py_XDECREF(code_bytes);
    Py_XDECREF(args_tuple);
    return result;
}

/* ==========================================================================
 * 六、模块定义
 * ==========================================================================*/

static PyObject *pp_info(PyObject *self, PyObject *args) {
    return Py_BuildValue(
        "{s:s, s:s, s:i, s:l}",
        "name", "_pythonpp_native",
        "version", PP_NATIVE_VERSION,
        "limited_api", 0x030800,
        "built_against", (long)PY_VERSION_HEX);
}

static PyMethodDef pp_methods[] = {
    {"seed", pp_seed, METH_VARARGS, "seed(v=None) 重置随机数状态"},
    {"random_int", pp_random_int, METH_VARARGS, "random_int(lo, hi) 闭区间随机整数"},
    {"random_float", pp_random_float, METH_NOARGS, "random_float() -> [0,1)"},
    {"random_bytes", pp_random_bytes, METH_VARARGS, "random_bytes(n) -> bytes"},
    {"shuffle", pp_shuffle, METH_VARARGS, "shuffle(list) 原地洗牌"},
    {"sum_int", pp_sum_int, METH_VARARGS, "sum_int(iterable) 整数快速求和"},
    {"dot", pp_dot, METH_VARARGS, "dot(a, b) 点积"},
    {"sum_range", pp_sum_range, METH_VARARGS, "sum_range(a, b, step=1) 等差数列求和"},
    {"deep_sizeof", pp_deep_sizeof, METH_VARARGS, "deep_sizeof(obj) 深度内存估算"},
    {"hash64", pp_hash64, METH_VARARGS, "hash64(obj) -> int"},
    {"loop_run", pp_loop_run, METH_VARARGS, "loop_run(code, args) 运行整数循环字节码"},
    {"info", pp_info, METH_NOARGS, "info() -> dict"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef pp_module = {
    PyModuleDef_HEAD_INIT,
    "_pythonpp_native",
    "Python++ 的 C 加速内核（稳定 ABI，单文件跨版本）",
    -1,
    pp_methods,
    NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC PyInit__pythonpp_native(void) {
    PyObject *module;
    PyObject *sys;

    module = PyModule_Create(&pp_module);
    if (!module) {
        return NULL;
    }

    /* 缓存 sys.getsizeof，供 deep_sizeof 使用 */
    sys = PyImport_ImportModule("sys");
    if (sys) {
        pp_getsizeof = PyObject_GetAttrString(sys, "getsizeof");
        Py_DECREF(sys);
    }
    if (!pp_getsizeof) {
        PyErr_Clear();
    }

    /* 自动播种：时间 + 模块地址，避免每次启动都是同一串随机数 */
    {
        uint64_t entropy = (uint64_t)time(NULL);
        entropy ^= (uint64_t)(uintptr_t)module << 17;
        entropy ^= (uint64_t)(uintptr_t)&pp_state << 3;
        pp_seed_u64(entropy);
    }

    if (PyModule_AddStringConstant(module, "__version__", PP_NATIVE_VERSION) < 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}
