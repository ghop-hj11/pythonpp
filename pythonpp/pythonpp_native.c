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
 * 1.2 新增（GIL=False 无锁运行时）
 *   atomic_*                 64 位原子操作 + 内存屏障（C11 原子 / MSVC 内建）
 *   channel_*                原生消息通道（环形队列 + 条件变量，阻塞时释放 GIL）
 *   set_option / options     运行时开关（release_gil 等）
 *
 * 编译：python build_native.py
 * ==========================================================================*/

/* 自由线程构建（CPython 3.13t/3.14t）不能用稳定 ABI：
 * 这类解释器里 PyObject 布局不同，abi3 模块会被直接拒绝加载。
 * build_native.py --free-threaded 会定义 PP_FREE_THREADED 走完整 API。 */
#if !defined(PP_FREE_THREADED) && !defined(Py_LIMITED_API)
#define Py_LIMITED_API 0x030800
#endif
#include <Python.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#if defined(_WIN32)
#include <windows.h>
#else
#include <pthread.h>
#include <sched.h>
#endif

#define PP_NATIVE_VERSION "1.2.0"

/* 自由线程（--disable-gil）构建检测：
 * 只有这类解释器才有 Py_GIL_DISABLED 与 Py_MOD_GIL_NOT_USED 两个宏。
 * 只有它们同时存在时，才敢用多阶段初始化 + Py_mod_gil 槽，
 * 否则 3.8 之类的老解释器会把未知槽当作错误。 */
#if defined(Py_GIL_DISABLED) && defined(Py_MOD_GIL_NOT_USED)
#define PP_FREE_THREADED_BUILD 1
#else
#define PP_FREE_THREADED_BUILD 0
#endif

/* 线程局部存储：随机数状态必须每线程一份，否则多线程下就是数据竞争 */
#if defined(_MSC_VER) && !defined(__clang__)
#define PP_THREAD_LOCAL __declspec(thread)
#else
#define PP_THREAD_LOCAL _Thread_local
#endif

/* 编译期确定的目标平台 / 架构，供 info() 自报家门，方便排查 ABI 兼容问题 */
#if defined(_WIN32)
#define PP_PLATFORM "windows"
#elif defined(__APPLE__)
#define PP_PLATFORM "macos"
#else
#define PP_PLATFORM "linux"
#endif

#if defined(__x86_64__) || defined(_M_X64)
#define PP_ARCH "x86_64"
#elif defined(__aarch64__) || defined(_M_ARM64)
#define PP_ARCH "aarch64"
#else
#define PP_ARCH "unknown"
#endif

/* 说明：这里刻意不定义 PY_SSIZE_T_CLEAN。
 * 它只影响 "s#" / "y#" 这类带 # 的格式符，而我们一个都没用；
 * 不定义反而能避开某些发行版里 _PyArg_ParseTuple_SizeT 未导出的坑。 */

/* ==========================================================================
 * 〇、原子操作与内存屏障（GIL 之外的数据一致性基础）
 * ----------------------------------------------------------------------------
 * 没有 GIL 之后，"读-改-写"不再天然互斥。这里用 CPU 的原子指令
 * （x86 的 lock cmpxchg / ARM 的 LDXR-STRX 循环）保证：
 *
 *     1. 加法/交换/CAS 不会丢更新（修掉数据竞争 Data Race）；
 *     2. 显式的 acquire / release / seq_cst 语义给出内存屏障，
 *        阻止 CPU 与编译器把指令重排（修掉 Memory Reordering）。
 *
 * GCC/Clang 走 __atomic_* 内建；MSVC 走 _Interlocked*（只提供 seq_cst）。
 * ==========================================================================*/

enum {
    PP_MO_RELAXED = 0,
    PP_MO_ACQUIRE = 1,
    PP_MO_RELEASE = 2,
    PP_MO_ACQ_REL = 3,
    PP_MO_SEQ_CST = 4
};

#if defined(_MSC_VER) && !defined(__clang__)
/* MSVC 没有 C11 原子，用内建函数；忽略 order，一律顺序一致 */
static int64_t pp_at_load(const int64_t *p, int order) {
    (void)order;
    return (int64_t)InterlockedCompareExchange64((volatile LONG64 *)p, 0, 0);
}
static void pp_at_store(int64_t *p, int64_t v, int order) {
    (void)order;
    InterlockedExchange64((volatile LONG64 *)p, (LONG64)v);
}
static int64_t pp_at_add(int64_t *p, int64_t d) {
    return (int64_t)InterlockedExchangeAdd64((volatile LONG64 *)p, (LONG64)d);
}
static int64_t pp_at_xchg(int64_t *p, int64_t v) {
    return (int64_t)InterlockedExchange64((volatile LONG64 *)p, (LONG64)v);
}
static int pp_at_cas(int64_t *p, int64_t *expected, int64_t desired) {
    int64_t prev = (int64_t)InterlockedCompareExchange64(
        (volatile LONG64 *)p, (LONG64)desired, (LONG64)*expected);
    if (prev == *expected) {
        return 1;
    }
    *expected = prev;
    return 0;
}
static int64_t pp_at_or(int64_t *p, int64_t v) {
    return (int64_t)InterlockedOr64((volatile LONG64 *)p, (LONG64)v);
}
static int64_t pp_at_and(int64_t *p, int64_t v) {
    return (int64_t)InterlockedAnd64((volatile LONG64 *)p, (LONG64)v);
}
static int64_t pp_at_xor(int64_t *p, int64_t v) {
    return (int64_t)InterlockedXor64((volatile LONG64 *)p, (LONG64)v);
}
static void pp_at_fence(int order) {
    (void)order;
    MemoryBarrier();
}
#else
static int pp_at_mo(int order) {
    switch (order) {
    case PP_MO_RELAXED: return __ATOMIC_RELAXED;
    case PP_MO_ACQUIRE: return __ATOMIC_ACQUIRE;
    case PP_MO_RELEASE: return __ATOMIC_RELEASE;
    case PP_MO_ACQ_REL: return __ATOMIC_ACQ_REL;
    default: return __ATOMIC_SEQ_CST;
    }
}
static int64_t pp_at_load(const int64_t *p, int order) {
    return __atomic_load_n(p, pp_at_mo(order));
}
static void pp_at_store(int64_t *p, int64_t v, int order) {
    __atomic_store_n(p, v, pp_at_mo(order));
}
static int64_t pp_at_add(int64_t *p, int64_t d) {
    return __atomic_fetch_add(p, d, __ATOMIC_SEQ_CST);
}
static int64_t pp_at_xchg(int64_t *p, int64_t v) {
    return __atomic_exchange_n(p, v, __ATOMIC_SEQ_CST);
}
static int pp_at_cas(int64_t *p, int64_t *expected, int64_t desired) {
    return __atomic_compare_exchange_n(p, expected, desired, 0,
                                       __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}
static int64_t pp_at_or(int64_t *p, int64_t v) {
    return __atomic_fetch_or(p, v, __ATOMIC_SEQ_CST);
}
static int64_t pp_at_and(int64_t *p, int64_t v) {
    return __atomic_fetch_and(p, v, __ATOMIC_SEQ_CST);
}
static int64_t pp_at_xor(int64_t *p, int64_t v) {
    return __atomic_fetch_xor(p, v, __ATOMIC_SEQ_CST);
}
static void pp_at_fence(int order) {
    __atomic_thread_fence(pp_at_mo(order));
}
#endif

/* GIL=False 时打开：JIT 循环执行期间释放 GIL。
 * 放在这里是因为 pp_loop_run 要用到它；用 int64 + 原子访问，
 * 避免"一个线程改开关、另一个线程正在读"的数据竞争。 */
static int64_t pp_opt_release_gil = 0;

/* ==========================================================================
 * 一、随机数：xoshiro256**  + splitmix64 播种（每线程独立状态）
 * ----------------------------------------------------------------------------
 * 旧版把 4 个 uint64 放在全局变量里。带 GIL 时靠"每次操作之间不换线程"
 * 侥幸安全，GIL=False 之后就是实打实的数据竞争：两个线程同时读写同一组
 * 状态，会生成重复/错乱的随机数，甚至破坏 xoshiro 的退化状态检查。
 *
 * 现在：
 *   * 全局只保留"种子 + 代数 + 线程序号"，全部用原子操作访问；
 *   * 每个线程第一次用随机数时，从全局种子派生自己的 4 个状态字；
 *   * seed() 改全局种子并 +1 代数，所有线程下一次调用会重新派生。
 * ==========================================================================*/

static int64_t pp_global_seed = (int64_t)0x9E3779B97F4A7C15ULL;
static int64_t pp_seed_gen = 0;
static int64_t pp_thread_seq = 0;

typedef struct {
    int ready;
    uint64_t gen;
    uint64_t index;
    uint64_t state[4];
} pp_rng_tls_t;

static PP_THREAD_LOCAL pp_rng_tls_t pp_rng = {0, 0, UINT64_MAX, {0, 0, 0, 0}};

static uint64_t pp_rotl(const uint64_t x, int k) {
    return (x << k) | (x >> (64 - k));
}

static uint64_t pp_splitmix64(uint64_t *x) {
    uint64_t z = (*x += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

static void pp_seed_state(uint64_t *state, uint64_t seed) {
    uint64_t x = seed ? seed : 0x123456789ABCDEFULL;
    int i;
    for (i = 0; i < 4; i++) {
        state[i] = pp_splitmix64(&x);
    }
    /* 全零是 xoshiro 的退化状态，必须避免 */
    if ((state[0] | state[1] | state[2] | state[3]) == 0) {
        state[0] = 1;
    }
}

/* 线程第一次用随机数 / 全局种子变过：派生本线程的状态 */
static void pp_rng_ensure(void) {
    uint64_t gen;
    if (pp_rng.index == UINT64_MAX) {
        /* 线程序号只分配一次，保证同一线程反复 seed 的结果可复现 */
        pp_rng.index = (uint64_t)pp_at_add(&pp_thread_seq, 1);
    }
    gen = (uint64_t)pp_at_load(&pp_seed_gen, PP_MO_ACQUIRE);
    if (!pp_rng.ready || pp_rng.gen != gen) {
        uint64_t base = (uint64_t)pp_at_load(&pp_global_seed, PP_MO_ACQUIRE);
        base += pp_rng.index * 0x9E3779B97F4A7C15ULL;
        pp_seed_state(pp_rng.state, base);
        pp_rng.gen = gen;
        pp_rng.ready = 1;
    }
}

static uint64_t pp_next(void) {
    uint64_t *s = pp_rng.state;
    const uint64_t result = pp_rotl(s[1] * 5, 7) * 9;
    const uint64_t t = s[1] << 17;

    s[2] ^= s[0];
    s[3] ^= s[1];
    s[1] ^= s[2];
    s[0] ^= s[3];
    s[2] ^= t;
    s[3] = pp_rotl(s[3], 45);
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
    uint64_t seed;
    if (!PyArg_ParseTuple(args, "|O", &value)) {
        return NULL;
    }
    if (value == Py_None) {
        uint64_t entropy = (uint64_t)time(NULL);
        entropy ^= (uint64_t)(uintptr_t)&entropy << 21;
        entropy ^= (uint64_t)(uintptr_t)self << 7;
        seed = entropy;
    } else {
        long long s = PyLong_AsLongLong(value);
        if (s == -1 && PyErr_Occurred()) {
            return NULL;
        }
        seed = (uint64_t)s;
    }
    /* release 语义：先把种子写好，再递增代数，其他线程看到新代数时
     * 一定也能看到新种子（这就是内存屏障要解决的问题）。 */
    pp_at_store(&pp_global_seed, (int64_t)seed, PP_MO_RELEASE);
    pp_at_add(&pp_seed_gen, 1);
    pp_rng_ensure();
    Py_RETURN_NONE;
}

static PyObject *pp_random_int(PyObject *self, PyObject *args) {
    PyObject *pa, *pb;
    long long lo, hi;
    uint64_t span;

    if (!PyArg_ParseTuple(args, "OO", &pa, &pb)) {
        return NULL;
    }
    pp_rng_ensure();
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
    double value;
    pp_rng_ensure();
    value = (double)(pp_next() >> 11) * (1.0 / 9007199254740992.0);
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
    pp_rng_ensure();
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
    pp_rng_ensure();
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

/* --------------------------------------------------------------------------
 * VM 执行核心：纯 C，不调用任何 Python API。
 * --------------------------------------------------------------------------
 * 这是 GIL=False 的关键一环：循环体只碰 int64 和 malloc 出来的输出缓冲，
 * 所以可以把整段执行放在 PyEval_SaveThread() 之后 —— 其他 Python 线程
 * 在这期间能真正跑起来。错误不能在这里调 PyErr_*，先记成错误码，
 * 回到有 GIL 的一侧再翻译。
 * ------------------------------------------------------------------------*/

enum {
    PP_VM_OK = 0,
    PP_VM_STACK_UNDER,
    PP_VM_STACK_DEEP,
    PP_VM_DIV0,
    PP_VM_OVERFLOW,
    PP_VM_JUMP,
    PP_VM_STEP,
    PP_VM_BADVAR,
    PP_VM_BADSTORE,
    PP_VM_BADCOND,
    PP_VM_BADOP,
    PP_VM_BADLOOP,
    PP_VM_ZERO_STEP,
    PP_VM_NOMEM
};

typedef struct {
    long long *out;
    Py_ssize_t count;
    Py_ssize_t cap;
    int err;
    int err_arg;
} pp_vm_out;

static int pp_vm_push(pp_vm_out *o, long long v) {
    if (o->count >= o->cap) {
        Py_ssize_t next = o->cap ? o->cap * 2 : 64;
        long long *grown;
        if (o->count >= (Py_ssize_t)1 << 24) {   /* 输出上限：16M 条，防内存爆炸 */
            o->err = PP_VM_OVERFLOW;
            return -1;
        }
        grown = (long long *)realloc(o->out, (size_t)next * sizeof(long long));
        if (!grown) {
            o->err = PP_VM_NOMEM;
            return -1;
        }
        o->out = grown;
        o->cap = next;
    }
    o->out[o->count++] = v;
    return 0;
}

static void pp_vm_exec(const uint8_t *raw, Py_ssize_t ncode,
                       long long vars[PP_VAR_MAX], unsigned char var_used[PP_VAR_MAX],
                       pp_vm_out *o) {
    long long stack[PP_STACK_MAX];
    Py_ssize_t sp = 0, pc = 0;
    long long steps = 0;

#define PP_VM_FAIL(code) \
    do { o->err = (code); return; } while (0)
#define PP_VM_FAIL_ARG(code, arg) \
    do { o->err = (code); o->err_arg = (int)(arg); return; } while (0)

    while (pc >= 0 && pc < ncode) {
        pp_ins in;
        long long x, y, z;

        memcpy(&in, raw + pc * PP_INS_SIZE, sizeof(in));
        if (++steps > PP_STEP_LIMIT) {
            PP_VM_FAIL(PP_VM_STEP);
        }
        switch (in.op) {
        case PP_PUSH_IMM:
            if (sp >= PP_STACK_MAX) {
                PP_VM_FAIL(PP_VM_STACK_DEEP);
            }
            stack[sp++] = in.imm;
            pc++;
            break;
        case PP_PUSH_VAR:
            if (in.a < 0 || in.a >= PP_VAR_MAX || !var_used[in.a]) {
                PP_VM_FAIL_ARG(PP_VM_BADVAR, in.a);
            }
            if (sp >= PP_STACK_MAX) {
                PP_VM_FAIL(PP_VM_STACK_DEEP);
            }
            stack[sp++] = vars[in.a];
            pc++;
            break;
        case PP_STORE:
            if (sp <= 0 || in.a < 0 || in.a >= PP_VAR_MAX) {
                PP_VM_FAIL(PP_VM_BADSTORE);
            }
            vars[in.a] = stack[--sp];
            var_used[in.a] = 1;
            pc++;
            break;
        case PP_ADD:
        case PP_SUB:
        case PP_MUL:
            if (sp < 2) {
                PP_VM_FAIL(PP_VM_STACK_UNDER);
            }
            y = stack[--sp];
            x = stack[--sp];
            if (in.op == PP_ADD) {
                if (PP_ADD_OVERFLOW(x, y, &z)) PP_VM_FAIL(PP_VM_OVERFLOW);
            } else if (in.op == PP_SUB) {
                if (PP_ADD_OVERFLOW(x, (long long)(0ULL - (unsigned long long)y), &z)) {
                    PP_VM_FAIL(PP_VM_OVERFLOW);
                }
            } else {
                if (PP_MUL_OVERFLOW(x, y, &z)) PP_VM_FAIL(PP_VM_OVERFLOW);
            }
            stack[sp++] = z;
            pc++;
            break;
        case PP_DIV:
        case PP_MOD:
            if (sp < 2) {
                PP_VM_FAIL(PP_VM_STACK_UNDER);
            }
            y = stack[--sp];
            x = stack[--sp];
            if (y == 0) {
                PP_VM_FAIL(PP_VM_DIV0);
            }
            if (x == INT64_MIN && y == -1) PP_VM_FAIL(PP_VM_OVERFLOW);
            stack[sp++] = (in.op == PP_DIV) ? pp_py_floordiv(x, y) : pp_py_mod(x, y);
            pc++;
            break;
        case PP_AND:
        case PP_OR:
        case PP_XOR:
            if (sp < 2) {
                PP_VM_FAIL(PP_VM_STACK_UNDER);
            }
            y = stack[--sp];
            x = stack[--sp];
            stack[sp++] = (in.op == PP_AND) ? (x & y) : (in.op == PP_OR) ? (x | y) : (x ^ y);
            pc++;
            break;
        case PP_SHL:
        case PP_SHR:
            if (sp < 2) {
                PP_VM_FAIL(PP_VM_STACK_UNDER);
            }
            y = stack[--sp];
            x = stack[--sp];
            if (y < 0 || y > 63) PP_VM_FAIL(PP_VM_OVERFLOW);
            stack[sp++] = (in.op == PP_SHL) ? (long long)((unsigned long long)x << y) : (x >> y);
            pc++;
            break;
        case PP_NEG:
            if (sp < 1) {
                PP_VM_FAIL(PP_VM_STACK_UNDER);
            }
            x = stack[sp - 1];
            if (x == INT64_MIN) PP_VM_FAIL(PP_VM_OVERFLOW);
            stack[sp - 1] = -x;
            pc++;
            break;
        case PP_CMP: {
            int take = 0;
            if (sp < 2) {
                PP_VM_FAIL(PP_VM_STACK_UNDER);
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
                PP_VM_FAIL(PP_VM_BADCOND);
            }
            if (take) {
                if (in.imm < 0 || in.imm >= ncode) {
                    PP_VM_FAIL(PP_VM_JUMP);
                }
                pc = (Py_ssize_t)in.imm;
            } else {
                pc++;
            }
            break;
        }
        case PP_JMP:
            if (in.imm < 0 || in.imm >= ncode) {
                PP_VM_FAIL(PP_VM_JUMP);
            }
            pc = (Py_ssize_t)in.imm;
            break;
        case PP_LOOP_CHECK: {
            long long iv, sv, tv;
            if (in.a < 0 || in.a >= PP_VAR_MAX || in.b < 0 || in.b >= PP_VAR_MAX ||
                in.c < 0 || in.c >= PP_VAR_MAX) {
                PP_VM_FAIL(PP_VM_BADLOOP);
            }
            iv = vars[in.a];
            sv = vars[in.b];
            tv = vars[in.c];
            if (tv == 0) {
                PP_VM_FAIL(PP_VM_ZERO_STEP);
            }
            if ((tv > 0) ? (iv < sv) : (iv > sv)) {
                pc++;
            } else {
                if (in.imm < 0 || in.imm >= ncode) {
                    PP_VM_FAIL(PP_VM_JUMP);
                }
                pc = (Py_ssize_t)in.imm;
            }
            break;
        }
        case PP_OUT:
            if (sp < 1) {
                PP_VM_FAIL(PP_VM_STACK_UNDER);
            }
            if (pp_vm_push(o, stack[--sp]) < 0) {
                return;
            }
            pc++;
            break;
        case PP_HALT:
            return;
        default:
            PP_VM_FAIL_ARG(PP_VM_BADOP, in.op);
        }
    }
#undef PP_VM_FAIL
#undef PP_VM_FAIL_ARG
}

/* 把 VM 错误码翻译成中文异常（这里已经重新持有 GIL） */
static void pp_vm_raise(const pp_vm_out *o) {
    switch (o->err) {
    case PP_VM_STACK_UNDER:
        PyErr_SetString(PyExc_ValueError, "栈下溢");
        break;
    case PP_VM_STACK_DEEP:
        PyErr_SetString(PyExc_OverflowError, "表达式嵌套过深");
        break;
    case PP_VM_DIV0:
        PyErr_SetString(PyExc_ZeroDivisionError, "整数除以零");
        break;
    case PP_VM_OVERFLOW:
        PyErr_SetString(PyExc_OverflowError, "jit 整型运算溢出，回退到 Python 大整数");
        break;
    case PP_VM_JUMP:
        PyErr_SetString(PyExc_ValueError, "跳转目标越界");
        break;
    case PP_VM_STEP:
        PyErr_SetString(PyExc_OverflowError, "jit 循环步数超过安全上限");
        break;
    case PP_VM_BADVAR:
        PyErr_Format(PyExc_NameError, "jit 里没有编号为 %d 的变量", o->err_arg);
        break;
    case PP_VM_BADSTORE:
        PyErr_SetString(PyExc_ValueError, "STORE 指令非法");
        break;
    case PP_VM_BADCOND:
        PyErr_SetString(PyExc_ValueError, "未知的比较条件");
        break;
    case PP_VM_BADOP:
        PyErr_Format(PyExc_ValueError, "未知的 jit 指令 %d", o->err_arg);
        break;
    case PP_VM_BADLOOP:
        PyErr_SetString(PyExc_ValueError, "LOOP_CHECK 槽位非法");
        break;
    case PP_VM_ZERO_STEP:
        PyErr_SetString(PyExc_ValueError, "range() 的步长不能为 0");
        break;
    case PP_VM_NOMEM:
        PyErr_NoMemory();
        break;
    default:
        PyErr_SetString(PyExc_RuntimeError, "jit 虚拟机内部错误");
        break;
    }
}

static PyObject *pp_loop_run(PyObject *self, PyObject *args) {
    PyObject *code_obj, *args_obj;
    PyObject *code_bytes = NULL, *args_tuple = NULL, *results = NULL;
    const uint8_t *raw;
    long long vars[PP_VAR_MAX];
    unsigned char var_used[PP_VAR_MAX];
    Py_ssize_t ncode, nargs;
    pp_vm_out out = {NULL, 0, 0, PP_VM_OK, 0};
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

    if (pp_at_load(&pp_opt_release_gil, PP_MO_ACQUIRE)) {
        /* GIL=False 模式：整段整数循环在 C 里跑，期间不持有 GIL。
         * 同一进程里其他 Python 线程（包括另一个 jit 调用）能并行推进。 */
        PyThreadState *saved = PyEval_SaveThread();
        pp_vm_exec(raw, ncode, vars, var_used, &out);
        PyEval_RestoreThread(saved);
    } else {
        pp_vm_exec(raw, ncode, vars, var_used, &out);
    }

    if (out.err != PP_VM_OK) {
        pp_vm_raise(&out);
        goto done;
    }

    results = PyList_New(out.count);
    if (!results) {
        goto done;
    }
    {
        Py_ssize_t i;
        for (i = 0; i < out.count; i++) {
            PyObject *v = PyLong_FromLongLong(out.out[i]);
            if (!v) {
                goto done;
            }
            /* PyList_SetItem 会偷走引用 */
            if (PyList_SetItem(results, i, v) < 0) {
                goto done;
            }
        }
    }
    result = results;
    results = NULL;

done:
    free(out.out);
    Py_XDECREF(results);
    Py_XDECREF(code_bytes);
    Py_XDECREF(args_tuple);
    return result;
}

/* ==========================================================================
 * 六、原子操作（对外的 Python 接口）
 * ----------------------------------------------------------------------------
 * 单元格是 C 里 malloc 出来的一块 8 字节内存，用 capsule 交给 Python 持有，
 * 所以多个线程拿到的就是同一块物理内存 —— 这正是"共享状态"该有的样子，
 * 区别只在于所有读写都走原子指令，不会丢更新。
 * ==========================================================================*/

#define PP_ATOMIC_CAPSULE "pythonpp.atomic64"

typedef struct {
    int64_t value;
} pp_atomic_cell;

static void pp_atomic_free(PyObject *capsule) {
    void *p = PyCapsule_GetPointer(capsule, PP_ATOMIC_CAPSULE);
    if (p) {
        free(p);
    } else {
        PyErr_Clear();
    }
}

static pp_atomic_cell *pp_atomic_get(PyObject *obj) {
    pp_atomic_cell *cell;
    if (!PyCapsule_CheckExact(obj)) {
        PyErr_SetString(PyExc_TypeError, "原子操作需要一个原子单元格对象");
        return NULL;
    }
    cell = (pp_atomic_cell *)PyCapsule_GetPointer(obj, PP_ATOMIC_CAPSULE);
    if (!cell) {
        PyErr_Clear();
        PyErr_SetString(PyExc_TypeError, "原子单元格类型不匹配");
        return NULL;
    }
    return cell;
}

static int pp_order_arg(int order) {
    if (order < PP_MO_RELAXED || order > PP_MO_SEQ_CST) {
        return PP_MO_SEQ_CST;
    }
    return order;
}

static PyObject *pp_atomic_new(PyObject *self, PyObject *args) {
    PyObject *initial = Py_None;
    long long v = 0;
    pp_atomic_cell *cell;

    if (!PyArg_ParseTuple(args, "|O", &initial)) {
        return NULL;
    }
    if (initial != Py_None) {
        v = PyLong_AsLongLong(initial);
        if (v == -1 && PyErr_Occurred()) {
            return NULL;
        }
    }
    cell = (pp_atomic_cell *)malloc(sizeof(pp_atomic_cell));
    if (!cell) {
        return PyErr_NoMemory();
    }
    cell->value = (int64_t)v;
    return PyCapsule_New(cell, PP_ATOMIC_CAPSULE, pp_atomic_free);
}

static PyObject *pp_atomic_load(PyObject *self, PyObject *args) {
    PyObject *obj;
    int order = PP_MO_SEQ_CST;
    pp_atomic_cell *cell;

    if (!PyArg_ParseTuple(args, "O|i", &obj, &order)) {
        return NULL;
    }
    cell = pp_atomic_get(obj);
    if (!cell) {
        return NULL;
    }
    return PyLong_FromLongLong((long long)pp_at_load(&cell->value, pp_order_arg(order)));
}

static PyObject *pp_atomic_store(PyObject *self, PyObject *args) {
    PyObject *obj;
    long long v;
    int order = PP_MO_SEQ_CST;
    pp_atomic_cell *cell;

    if (!PyArg_ParseTuple(args, "OL|i", &obj, &v, &order)) {
        return NULL;
    }
    cell = pp_atomic_get(obj);
    if (!cell) {
        return NULL;
    }
    pp_at_store(&cell->value, (int64_t)v, pp_order_arg(order));
    Py_RETURN_NONE;
}

static PyObject *pp_atomic_add(PyObject *self, PyObject *args) {
    PyObject *obj;
    long long delta = 1;
    pp_atomic_cell *cell;

    if (!PyArg_ParseTuple(args, "O|L", &obj, &delta)) {
        return NULL;
    }
    cell = pp_atomic_get(obj);
    if (!cell) {
        return NULL;
    }
    return PyLong_FromLongLong((long long)pp_at_add(&cell->value, (int64_t)delta));
}

static PyObject *pp_atomic_exchange(PyObject *self, PyObject *args) {
    PyObject *obj;
    long long v;
    pp_atomic_cell *cell;

    if (!PyArg_ParseTuple(args, "OL", &obj, &v)) {
        return NULL;
    }
    cell = pp_atomic_get(obj);
    if (!cell) {
        return NULL;
    }
    return PyLong_FromLongLong((long long)pp_at_xchg(&cell->value, (int64_t)v));
}

static PyObject *pp_atomic_cas(PyObject *self, PyObject *args) {
    PyObject *obj;
    long long expected, desired;
    int64_t exp;
    int ok;
    pp_atomic_cell *cell;

    if (!PyArg_ParseTuple(args, "OLL", &obj, &expected, &desired)) {
        return NULL;
    }
    cell = pp_atomic_get(obj);
    if (!cell) {
        return NULL;
    }
    exp = (int64_t)expected;
    pp_at_fence(PP_MO_SEQ_CST);
    ok = pp_at_cas(&cell->value, &exp, (int64_t)desired);
    pp_at_fence(PP_MO_SEQ_CST);
    return Py_BuildValue("(Li)", (long long)exp, ok);
}

static PyObject *pp_atomic_bits(PyObject *self, PyObject *args, int op) {
    PyObject *obj;
    long long v;
    pp_atomic_cell *cell;
    int64_t old;

    if (!PyArg_ParseTuple(args, "OL", &obj, &v)) {
        return NULL;
    }
    cell = pp_atomic_get(obj);
    if (!cell) {
        return NULL;
    }
    if (op == 0) {
        old = pp_at_or(&cell->value, (int64_t)v);
    } else if (op == 1) {
        old = pp_at_and(&cell->value, (int64_t)v);
    } else {
        old = pp_at_xor(&cell->value, (int64_t)v);
    }
    return PyLong_FromLongLong((long long)old);
}

static PyObject *pp_atomic_or(PyObject *self, PyObject *args) {
    return pp_atomic_bits(self, args, 0);
}

static PyObject *pp_atomic_and(PyObject *self, PyObject *args) {
    return pp_atomic_bits(self, args, 1);
}

static PyObject *pp_atomic_xor(PyObject *self, PyObject *args) {
    return pp_atomic_bits(self, args, 2);
}

static PyObject *pp_atomic_fence(PyObject *self, PyObject *args) {
    int order = PP_MO_SEQ_CST;
    if (!PyArg_ParseTuple(args, "|i", &order)) {
        return NULL;
    }
    pp_at_fence(pp_order_arg(order));
    Py_RETURN_NONE;
}

/* ==========================================================================
 * 七、消息通道（Go 风格 channel 的 C 实现）
 * ----------------------------------------------------------------------------
 * 线程之间不共享可变状态：一方把对象"交出去"，另一方"接过来"，
 * 队列内部只有指针 + 原子化的计数变化。真正阻塞等待时释放 GIL，
 * 所以在标准 CPython 上也不会把整个解释器卡死。
 *
 * 关闭语义：close() 之后仍可取走剩余消息；取空了再取，返回一个哨兵对象，
 * 由 Python 层翻译成 ChannelClosed。
 * ==========================================================================*/

#define PP_CHANNEL_CAPSULE "pythonpp.channel"

static PyObject *pp_closed_marker = NULL;

/* 稳定 ABI（Py_LIMITED_API）没有导出 PyExc_TimeoutError 符号，
 * 所以构建期从 builtins 里取一次 TimeoutError，运行期直接用。 */
static PyObject *pp_exc_timeout = NULL;

static void pp_set_timeout(const char *message) {
    if (pp_exc_timeout) {
        PyErr_SetString(pp_exc_timeout, message);
    } else {
        PyErr_SetString(PyExc_RuntimeError, message);
    }
}

typedef struct {
#if defined(_WIN32)
    CRITICAL_SECTION mu;
    CONDITION_VARIABLE not_empty;
    CONDITION_VARIABLE not_full;
#else
    pthread_mutex_t mu;
    pthread_cond_t not_empty;
    pthread_cond_t not_full;
#endif
    PyObject **items;
    Py_ssize_t arr_cap;     /* 已分配的槽位 */
    Py_ssize_t head;        /* 队首下标 */
    Py_ssize_t count;       /* 队列内消息数 */
    Py_ssize_t capacity;    /* 0 = 无界 */
    int closed;
} pp_channel;

static double pp_now_seconds(void) {
#if defined(_WIN32)
    LARGE_INTEGER freq, counter;
    QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&counter);
    return (double)counter.QuadPart / (double)freq.QuadPart;
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
#endif
}

/* 返回 0 = 被唤醒，1 = 超时/出错 */
static int pp_channel_wait(pp_channel *ch, int for_empty, double remaining) {
#if defined(_WIN32)
    CONDITION_VARIABLE *cond = for_empty ? &ch->not_empty : &ch->not_full;
    DWORD ms = INFINITE;
    if (remaining >= 0) {
        ms = (DWORD)(remaining * 1000.0);
        if (ms == 0) {
            ms = 1;
        }
    }
    return SleepConditionVariableCS(cond, &ch->mu, ms) ? 0 : 1;
#else
    pthread_cond_t *cond = for_empty ? &ch->not_empty : &ch->not_full;
    if (remaining < 0) {
        return pthread_cond_wait(cond, &ch->mu) == 0 ? 0 : 1;
    }
    {
        struct timespec ts;
        double total;
        clock_gettime(CLOCK_REALTIME, &ts);
        total = (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9 + remaining;
        ts.tv_sec = (time_t)total;
        ts.tv_nsec = (long)((total - (double)ts.tv_sec) * 1e9);
        if (ts.tv_nsec >= 1000000000L) {
            ts.tv_sec += 1;
            ts.tv_nsec -= 1000000000L;
        }
        return pthread_cond_timedwait(cond, &ch->mu, &ts) == 0 ? 0 : 1;
    }
#endif
}

static void pp_channel_mutex_init(pp_channel *ch) {
#if defined(_WIN32)
    InitializeCriticalSection(&ch->mu);
    InitializeConditionVariable(&ch->not_empty);
    InitializeConditionVariable(&ch->not_full);
#else
    pthread_mutex_init(&ch->mu, NULL);
    pthread_cond_init(&ch->not_empty, NULL);
    pthread_cond_init(&ch->not_full, NULL);
#endif
}

static void pp_channel_mutex_done(pp_channel *ch) {
#if defined(_WIN32)
    DeleteCriticalSection(&ch->mu);
#else
    pthread_cond_destroy(&ch->not_empty);
    pthread_cond_destroy(&ch->not_full);
    pthread_mutex_destroy(&ch->mu);
#endif
}

static void pp_channel_lock(pp_channel *ch) {
#if defined(_WIN32)
    EnterCriticalSection(&ch->mu);
#else
    pthread_mutex_lock(&ch->mu);
#endif
}

static void pp_channel_unlock(pp_channel *ch) {
#if defined(_WIN32)
    LeaveCriticalSection(&ch->mu);
#else
    pthread_mutex_unlock(&ch->mu);
#endif
}

static void pp_channel_wake_all(pp_channel *ch, int for_empty) {
#if defined(_WIN32)
    if (for_empty) {
        WakeAllConditionVariable(&ch->not_empty);
    } else {
        WakeAllConditionVariable(&ch->not_full);
    }
#else
    if (for_empty) {
        pthread_cond_broadcast(&ch->not_empty);
    } else {
        pthread_cond_broadcast(&ch->not_full);
    }
#endif
}

/* 只在持锁时调用 */
static int pp_channel_reserve(pp_channel *ch, Py_ssize_t need) {
    PyObject **grown;
    Py_ssize_t newcap;
    if (need <= ch->arr_cap) {
        return 0;
    }
    newcap = ch->arr_cap ? ch->arr_cap : 8;
    while (newcap < need) {
        newcap *= 2;
    }
    grown = (PyObject **)realloc(ch->items, (size_t)newcap * sizeof(PyObject *));
    if (!grown) {
        return -1;
    }
    ch->items = grown;
    ch->arr_cap = newcap;
    return 0;
}

/* 只在持锁时调用：把队首前移，必要时compact */
static void pp_channel_compact(pp_channel *ch) {
    if (ch->count == 0) {
        ch->head = 0;
        return;
    }
    if (ch->head > 0 && (ch->head + ch->count >= ch->arr_cap || ch->head * 2 >= ch->arr_cap)) {
        memmove(ch->items, ch->items + ch->head,
                (size_t)ch->count * sizeof(PyObject *));
        ch->head = 0;
    }
}

static pp_channel *pp_channel_handle(PyObject *obj) {
    pp_channel *ch;
    if (!PyCapsule_CheckExact(obj)) {
        PyErr_SetString(PyExc_TypeError, "通道对象无效");
        return NULL;
    }
    ch = (pp_channel *)PyCapsule_GetPointer(obj, PP_CHANNEL_CAPSULE);
    if (!ch) {
        PyErr_Clear();
        PyErr_SetString(PyExc_TypeError, "通道对象类型不匹配");
        return NULL;
    }
    return ch;
}

static void pp_channel_free(PyObject *capsule) {
    pp_channel *ch = (pp_channel *)PyCapsule_GetPointer(capsule, PP_CHANNEL_CAPSULE);
    Py_ssize_t i;
    if (!ch) {
        PyErr_Clear();
        return;
    }
    pp_channel_lock(ch);
    ch->closed = 1;
    for (i = 0; i < ch->count; i++) {
        Py_XDECREF(ch->items[ch->head + i]);
    }
    ch->count = 0;
    pp_channel_wake_all(ch, 1);
    pp_channel_wake_all(ch, 0);
    pp_channel_unlock(ch);
    free(ch->items);
    pp_channel_mutex_done(ch);
    free(ch);
}

static PyObject *pp_channel_new(PyObject *self, PyObject *args) {
    long long capacity = 0;
    pp_channel *ch;

    if (!PyArg_ParseTuple(args, "|L", &capacity)) {
        return NULL;
    }
    if (capacity < 0) {
        PyErr_SetString(PyExc_ValueError, "通道容量不能为负数（0 表示无界）");
        return NULL;
    }
    ch = (pp_channel *)calloc(1, sizeof(pp_channel));
    if (!ch) {
        return PyErr_NoMemory();
    }
    ch->capacity = (Py_ssize_t)capacity;
    pp_channel_mutex_init(ch);
    return PyCapsule_New(ch, PP_CHANNEL_CAPSULE, pp_channel_free);
}

static int pp_parse_timeout(PyObject *obj, double *out) {
    if (!obj || obj == Py_None) {
        *out = -1.0;
        return 0;
    }
    *out = PyFloat_AsDouble(obj);
    if (PyErr_Occurred()) {
        return -1;
    }
    if (*out < 0) {
        *out = -1.0;    /* 负数同样表示一直等 */
    }
    return 0;
}

static PyObject *pp_channel_put(PyObject *self, PyObject *args) {
    PyObject *cap, *item, *timeout_obj = Py_None;
    pp_channel *ch;
    double timeout = -1.0, deadline = -1.0;
    int closed = 0, failed = 0;
    PyThreadState *saved;

    if (!PyArg_ParseTuple(args, "OO|O", &cap, &item, &timeout_obj)) {
        return NULL;
    }
    ch = pp_channel_handle(cap);
    if (!ch) {
        return NULL;
    }
    if (pp_parse_timeout(timeout_obj, &timeout) < 0) {
        return NULL;
    }
    if (timeout >= 0) {
        deadline = pp_now_seconds() + timeout;
    }

    /* 先把引用攥在自己手里，释放 GIL 期间不会有别人 DECREF 它 */
    Py_INCREF(item);

    saved = PyEval_SaveThread();
    pp_channel_lock(ch);
    for (;;) {
        double remaining;
        if (ch->closed) {
            closed = 1;
            break;
        }
        if (ch->capacity <= 0 || ch->count < ch->capacity) {
            break;
        }
        remaining = deadline < 0 ? -1.0 : deadline - pp_now_seconds();
        if (deadline >= 0 && remaining <= 0) {
            failed = 1;
            break;
        }
        pp_channel_wait(ch, 0, remaining);
    }
    if (!closed && !failed) {
        if (pp_channel_reserve(ch, ch->head + ch->count + 1) < 0) {
            failed = 2;
        } else {
            ch->items[ch->head + ch->count] = item;
            ch->count += 1;
            pp_channel_wake_all(ch, 1);
        }
    }
    pp_channel_unlock(ch);
    PyEval_RestoreThread(saved);

    if (closed || failed) {
        Py_DECREF(item);
        if (closed) {
            PyErr_SetString(PyExc_RuntimeError, "通道已关闭，不能再发送消息");
        } else if (failed == 2) {
            PyErr_NoMemory();
        } else {
            pp_set_timeout("发送超时：通道已满");
        }
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *pp_channel_get(PyObject *self, PyObject *args) {
    PyObject *cap, *item = NULL, *timeout_obj = Py_None;
    pp_channel *ch;
    double timeout = -1.0, deadline = -1.0;
    int closed = 0, timedout = 0;
    PyThreadState *saved;

    if (!PyArg_ParseTuple(args, "O|O", &cap, &timeout_obj)) {
        return NULL;
    }
    ch = pp_channel_handle(cap);
    if (!ch) {
        return NULL;
    }
    if (pp_parse_timeout(timeout_obj, &timeout) < 0) {
        return NULL;
    }
    if (timeout >= 0) {
        deadline = pp_now_seconds() + timeout;
    }

    saved = PyEval_SaveThread();
    pp_channel_lock(ch);
    for (;;) {
        double remaining;
        if (ch->count > 0) {
            item = ch->items[ch->head];
            ch->items[ch->head] = NULL;
            ch->head += 1;
            ch->count -= 1;
            pp_channel_compact(ch);
            pp_channel_wake_all(ch, 0);
            break;
        }
        if (ch->closed) {
            closed = 1;
            break;
        }
        remaining = deadline < 0 ? -1.0 : deadline - pp_now_seconds();
        if (deadline >= 0 && remaining <= 0) {
            timedout = 1;
            break;
        }
        pp_channel_wait(ch, 1, remaining);
    }
    pp_channel_unlock(ch);
    PyEval_RestoreThread(saved);

    if (item) {
        return item;          /* 引用所有权转给调用方 */
    }
    if (timedout) {
        pp_set_timeout("接收超时：通道里暂时没有消息");
        return NULL;
    }
    if (closed) {
        if (!pp_closed_marker) {
            Py_RETURN_NONE;
        }
        Py_INCREF(pp_closed_marker);
        return pp_closed_marker;
    }
    Py_RETURN_NONE;
}

static PyObject *pp_channel_close(PyObject *self, PyObject *args) {
    PyObject *cap;
    pp_channel *ch;
    PyThreadState *saved;

    if (!PyArg_ParseTuple(args, "O", &cap)) {
        return NULL;
    }
    ch = pp_channel_handle(cap);
    if (!ch) {
        return NULL;
    }
    saved = PyEval_SaveThread();
    pp_channel_lock(ch);
    ch->closed = 1;
    pp_channel_wake_all(ch, 1);
    pp_channel_wake_all(ch, 0);
    pp_channel_unlock(ch);
    PyEval_RestoreThread(saved);
    Py_RETURN_NONE;
}

static PyObject *pp_channel_size(PyObject *self, PyObject *args) {
    PyObject *cap;
    pp_channel *ch;
    Py_ssize_t n;
    PyThreadState *saved;

    if (!PyArg_ParseTuple(args, "O", &cap)) {
        return NULL;
    }
    ch = pp_channel_handle(cap);
    if (!ch) {
        return NULL;
    }
    saved = PyEval_SaveThread();
    pp_channel_lock(ch);
    n = ch->count;
    pp_channel_unlock(ch);
    PyEval_RestoreThread(saved);
    return PyLong_FromSsize_t(n);
}

static PyObject *pp_channel_is_closed(PyObject *self, PyObject *args) {
    PyObject *cap;
    pp_channel *ch;
    int closed;
    PyThreadState *saved;

    if (!PyArg_ParseTuple(args, "O", &cap)) {
        return NULL;
    }
    ch = pp_channel_handle(cap);
    if (!ch) {
        return NULL;
    }
    saved = PyEval_SaveThread();
    pp_channel_lock(ch);
    closed = ch->closed;
    pp_channel_unlock(ch);
    PyEval_RestoreThread(saved);
    if (closed) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

static PyObject *pp_channel_marker(PyObject *self, PyObject *args) {
    if (!pp_closed_marker) {
        Py_RETURN_NONE;
    }
    Py_INCREF(pp_closed_marker);
    return pp_closed_marker;
}

/* ==========================================================================
 * 八、运行时开关 / 模块定义
 * ==========================================================================*/

static PyObject *pp_info(PyObject *self, PyObject *args) {
    return Py_BuildValue(
        "{s:s, s:s, s:i, s:l, s:s, s:s, s:i, s:i, s:i, s:i}",
        "name", "_pythonpp_native",
        "version", PP_NATIVE_VERSION,
        "limited_api", 0x030800,
        "built_against", (long)PY_VERSION_HEX,
        "platform", PP_PLATFORM,
        "arch", PP_ARCH,
        "free_threaded", PP_FREE_THREADED_BUILD,
        "atomics", 1,
        "channels", 1,
        "gil_release", (int)pp_at_load(&pp_opt_release_gil, PP_MO_ACQUIRE));
}

static PyObject *pp_options(PyObject *self, PyObject *args) {
    int release = (int)pp_at_load(&pp_opt_release_gil, PP_MO_ACQUIRE);
    return Py_BuildValue(
        "{s:O, s:O, s:O, s:O}",
        "release_gil", release ? Py_True : Py_False,
        "free_threaded", PP_FREE_THREADED_BUILD ? Py_True : Py_False,
        "atomics", Py_True,
        "channels", Py_True);
}

static PyObject *pp_set_option(PyObject *self, PyObject *args) {
    const char *name;
    PyObject *value;
    int flag, old;

    if (!PyArg_ParseTuple(args, "sO", &name, &value)) {
        return NULL;
    }
    flag = PyObject_IsTrue(value);
    if (flag < 0) {
        return NULL;
    }
    if (strcmp(name, "release_gil") == 0) {
        old = (int)pp_at_load(&pp_opt_release_gil, PP_MO_ACQUIRE);
        pp_at_store(&pp_opt_release_gil, flag ? 1 : 0, PP_MO_RELEASE);
        return PyBool_FromLong(old);
    }
    PyErr_Format(PyExc_KeyError, "未知的内核选项：%s", name);
    return NULL;
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

    /* 原子操作 / 内存屏障 */
    {"atomic_new", pp_atomic_new, METH_VARARGS, "atomic_new(initial=0) -> capsule"},
    {"atomic_load", pp_atomic_load, METH_VARARGS, "atomic_load(cell, order)"},
    {"atomic_store", pp_atomic_store, METH_VARARGS, "atomic_store(cell, value, order)"},
    {"atomic_add", pp_atomic_add, METH_VARARGS, "atomic_add(cell, delta) -> 旧值"},
    {"atomic_exchange", pp_atomic_exchange, METH_VARARGS, "atomic_exchange(cell, value) -> 旧值"},
    {"atomic_cas", pp_atomic_cas, METH_VARARGS, "atomic_cas(cell, expected, desired) -> bool"},
    {"atomic_or", pp_atomic_or, METH_VARARGS, "atomic_or(cell, mask) -> 旧值"},
    {"atomic_and", pp_atomic_and, METH_VARARGS, "atomic_and(cell, mask) -> 旧值"},
    {"atomic_xor", pp_atomic_xor, METH_VARARGS, "atomic_xor(cell, mask) -> 旧值"},
    {"atomic_fence", pp_atomic_fence, METH_VARARGS, "atomic_fence(order) 内存屏障"},

    /* 消息通道 */
    {"channel_new", pp_channel_new, METH_VARARGS, "channel_new(capacity=0) -> capsule"},
    {"channel_put", pp_channel_put, METH_VARARGS, "channel_put(ch, item, timeout=None)"},
    {"channel_get", pp_channel_get, METH_VARARGS, "channel_get(ch, timeout=None)"},
    {"channel_close", pp_channel_close, METH_VARARGS, "channel_close(ch)"},
    {"channel_size", pp_channel_size, METH_VARARGS, "channel_size(ch) -> int"},
    {"channel_is_closed", pp_channel_is_closed, METH_VARARGS, "channel_is_closed(ch) -> bool"},
    {"channel_marker", pp_channel_marker, METH_NOARGS, "channel_marker() -> 关闭哨兵"},

    {"options", pp_options, METH_NOARGS, "options() -> dict"},
    {"set_option", pp_set_option, METH_VARARGS, "set_option(name, value) -> 旧值"},
    {"info", pp_info, METH_NOARGS, "info() -> dict"},
    {NULL, NULL, 0, NULL}
};

/* 模块初始化（两种构建方式共用）：
 *   * 缓存 sys.getsizeof，供 deep_sizeof 使用；
 *   * 建立"通道已关闭"哨兵对象；
 *   * 用时间 + 模块地址播种全局 RNG，再派生本线程状态。 */
static int pp_module_init(PyObject *module) {
    PyObject *sys;
    uint64_t entropy;

    sys = PyImport_ImportModule("sys");
    if (sys) {
        pp_getsizeof = PyObject_GetAttrString(sys, "getsizeof");
        Py_DECREF(sys);
    }
    if (!pp_getsizeof) {
        PyErr_Clear();
    }

    if (!pp_closed_marker) {
        static const char closed_sentinel = 0;
        pp_closed_marker = PyCapsule_New((void *)&closed_sentinel,
                                         "pythonpp.channel.closed", NULL);
        if (!pp_closed_marker) {
            return -1;
        }
    }

    if (!pp_exc_timeout) {
        PyObject *builtins = PyImport_ImportModule("builtins");
        if (builtins) {
            pp_exc_timeout = PyObject_GetAttrString(builtins, "TimeoutError");
            Py_DECREF(builtins);
        }
        if (!pp_exc_timeout) {
            PyErr_Clear();
        }
    }

    entropy = (uint64_t)time(NULL);
    entropy ^= (uint64_t)(uintptr_t)module << 17;
    entropy ^= (uint64_t)(uintptr_t)&entropy << 39;
    pp_at_store(&pp_global_seed, (int64_t)entropy, PP_MO_RELEASE);
    pp_at_add(&pp_seed_gen, 1);
    pp_rng_ensure();

    if (PyModule_AddStringConstant(module, "__version__", PP_NATIVE_VERSION) < 0) {
        return -1;
    }
    return 0;
}

#if PP_FREE_THREADED_BUILD
/* 自由线程构建：多阶段初始化 + Py_mod_gil 槽。
 * 没有这个槽，解释器 import 时会"礼貌地"重新打开 GIL。 */
static int pp_exec_module(PyObject *module) {
    return pp_module_init(module) < 0 ? -1 : 0;
}

static PyModuleDef_Slot pp_slots[] = {
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
    {Py_mod_exec, (void *)pp_exec_module},
    {0, NULL}
};

static struct PyModuleDef pp_module = {
    PyModuleDef_HEAD_INIT,
    "_pythonpp_native",
    "Python++ 的 C 加速内核（自由线程构建，无 GIL）",
    0,                      /* 多阶段初始化必须写 0 */
    pp_methods,
    pp_slots,
    NULL, NULL, NULL
};

PyMODINIT_FUNC PyInit__pythonpp_native(void) {
    return PyModuleDef_Init(&pp_module);
}
#else
static struct PyModuleDef pp_module = {
    PyModuleDef_HEAD_INIT,
    "_pythonpp_native",
    "Python++ 的 C 加速内核（稳定 ABI，单文件跨版本）",
    -1,
    pp_methods,
    NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC PyInit__pythonpp_native(void) {
    PyObject *module = PyModule_Create(&pp_module);
    if (!module) {
        return NULL;
    }
    if (pp_module_init(module) < 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}
#endif
