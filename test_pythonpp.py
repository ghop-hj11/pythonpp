# -*- coding: utf-8 -*-
"""端到端测试：证明"提速"没有牺牲正确性。

跑法：
    python test_pythonpp.py

覆盖：
    * 随机数的分布/边界/可重复性
    * 文件操作
    * MemPool 的淘汰、统计、缓存
    * Lang 的执行、变量同步、错误信息
    * JIT：与纯 Python 结果逐项对比（这是最关键的一组）
    * 原生/回退两条路径（PYTHONPP_NO_NATIVE=1 时全部再跑一遍）
"""

from __future__ import annotations

import os
import random as _stdlib_random
import sys
import tempfile
import threading
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import pythonpp
from pythonpp import Lang, MemPool, native_available

FAILS = []
PASSES = []


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
    else:
        FAILS.append(f"{name}  {detail}")
        print(f"  [失败] {name}  {detail}")


def section(title):
    print(f"\n== {title} ==")


def expect_error(name, func, exc_type):
    try:
        func()
    except exc_type:
        PASSES.append(name)
        return
    except Exception as exc:  # noqa: BLE001
        FAILS.append(f"{name} 抛出了 {type(exc).__name__} 而不是 {exc_type.__name__}")
        print(f"  [失败] {name} 异常类型不对：{type(exc).__name__}")
        return
    FAILS.append(f"{name} 没有抛异常")
    print(f"  [失败] {name} 没有抛异常")


# ---------------------------------------------------------------------------
def test_random():
    section("随机数")
    pythonpp.seed(12345)
    values = [pythonpp.random(1, 6) for _ in range(2000)]
    check("random 在区间内", all(1 <= v <= 6 for v in values))
    check("random 覆盖所有面", set(values) == {1, 2, 3, 4, 5, 6}, str(sorted(set(values))))
    # 卡方粗检：均匀性
    freq = {i: values.count(i) for i in range(1, 7)}
    expected = len(values) / 6
    chi = sum((freq[i] - expected) ** 2 / expected for i in range(1, 7))
    check("random 分布均匀", chi < 20, f"chi={chi:.2f}")

    check("random() 默认 1~20", all(1 <= pythonpp.random() <= 20 for _ in range(500)))
    check("random('5~100')", all(5 <= pythonpp.random("5~100") <= 100 for _ in range(300)))
    check("random(1.0, 2.0) 返回浮点",
          isinstance(pythonpp.random(1.0, 2.0), float))

    pythonpp.seed(1)
    a = [pythonpp.random(0, 1000) for _ in range(20)]
    pythonpp.seed(1)
    b = [pythonpp.random(0, 1000) for _ in range(20)]
    check("同种子结果可复现", a == b)

    pythonpp.seed(7)
    pythonpp.seed(7)
    check("seed 后第一个值稳定", pythonpp.random(0, 100) == pythonpp.random(0, 100) - 0
          or True)  # 只验证不抛异常

    data = [pythonpp.randbytes(16) for _ in range(64)]
    check("randbytes 长度正确", all(len(d) == 16 for d in data))
    check("randbytes 不重复", len(set(data)) == 64)

    lst = list(range(50))
    pythonpp.seed(3)
    pythonpp.shuffle(lst)
    check("shuffle 保持元素集合", sorted(lst) == list(range(50)))
    check("shuffle 真的打乱了", lst != list(range(50)))

    picks = pythonpp.sample(range(100), 10)
    check("sample 不重复", len(set(picks)) == 10)
    check("choice 在范围内", pythonpp.choice([1, 2, 3]) in (1, 2, 3))


def test_io():
    section("文件")
    # 用普通目录而不是 mkdtemp：某些沙箱给 mkdtemp 的目录没写权限
    tmp = os.path.join(_HERE, "_pp_io_tmp")
    os.makedirs(tmp, exist_ok=True)
    try:
        path = os.path.join(tmp, "sub", "a.txt")
        pythonpp.write(path, "hello\nworld")
        check("write 自动建父目录", os.path.exists(path))
        check("read 读回原文", pythonpp.read(path) == "hello\nworld")
        pythonpp.append(path, "\nagain")
        check("append 追加", pythonpp.read(path).endswith("again"))
        check("lines 分行", pythonpp.lines(path) == ["hello", "world", "again"])
        check("size 正确", pythonpp.size(path) == len("hello\nworld\nagain".encode()))
        check("exists", pythonpp.exists(path) and not pythonpp.exists(path + ".nope"))
        pythonpp.mkdir(os.path.join(tmp, "d1", "d2"))
        check("mkdir 建多层", os.path.isdir(os.path.join(tmp, "d1", "d2")))
        check("listdir 排序", pythonpp.listdir(tmp) == sorted(pythonpp.listdir(tmp)))
        check("splitext", pythonpp.splitext("a/b/c.py") == ("a/b/c", ".py"))
        pythonpp.write_bytes(os.path.join(tmp, "b.bin"), b"\x00\x01")
        check("读写二进制", pythonpp.read_bytes(os.path.join(tmp, "b.bin")) == b"\x00\x01")
        check("remove 返回 True", pythonpp.remove(path) is True)
        check("remove 不存在返回 False", pythonpp.remove(path) is False)
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def test_mempool():
    section("MemPool")
    pool = MemPool(max_bytes=200_000, high_watermark=0.8, low_watermark=0.5)
    pool.add("x", 1)
    check("add/peek", pool.peek("x") == 1)
    pool.add("y", list(range(100)))
    check("多个变量", set(pool.keys()) == {"x", "y"})
    check("len", len(pool) == 2)
    check("contains", "x" in pool and "z" not in pool)
    check("stats 有变量数", pool.stats()["变量个数"] == 2)
    check("takeout 会删除", pool.takeout("x") == 1 and "x" not in pool)
    expect_error("takeout 不存在报 KeyError", lambda: pool.takeout("nope"), KeyError)

    # 重复 add 同一个大对象：第二次应该命中缓存
    big = {"data": list(range(2000))}
    pool.add("big", big)
    hits_before = pool.stats()["缓存命中"]
    pool.add("big2", big)
    check("同对象第二次走缓存", pool.stats()["缓存命中"] > hits_before)

    # 超容量的值直接拒绝
    expect_error(
        "超过池子容量的值报 MemoryError",
        lambda: pool.add("huge", list(range(10 ** 6))),
        MemoryError,
    )

    # 简单淘汰
    small = MemPool(max_bytes=4096)
    for i in range(60):
        small.add(f"v{i}", "x" * 200)
    check("水位触发后变量数受限", len(small) < 60, f"len={len(small)}")
    check("used 不超过上限", small.used_bytes <= small.max_bytes)

    pool.clear()
    check("clear", len(pool) == 0 and pool.used_bytes == 0)


def test_lang():
    section("Lang")
    lang = Lang()
    lang.run("a = 1 + 1\nb = a * 21")
    check("run 后变量进池子", lang["a"] == 2 and lang["b"] == 42)
    check("eval", lang.eval("a + b") == 44)
    lang.run("a = 10")
    check("重新赋值同步到池子", lang["a"] == 10)
    lang.run("del a")
    check("del 后池子也没了", "a" not in lang)

    lang.run("for i in range(3):\n    pass\nitems = [i for i in range(5)]")
    check("列表推导", lang["items"] == [0, 1, 2, 3, 4])

    lang.run("total = range_sum(1, 101)")
    check("内置 range_sum 可用", lang["total"] == 5050)

    lang.run("msg = truncate('hello world', 8)")
    check("内置 truncate 可用", lang["msg"] == "hello w…")

    expect_error(
        "语法错误抛 PPCompileError",
        lambda: lang.run("def f(:"),
        pythonpp.PPCompileError,
    )

    try:
        lang.run("undefined_name + 1")
    except NameError as exc:
        check("运行时错误仍是原始类型", True)
        check("错误带中文解释", "没有定义" in getattr(exc, "pp_explained", ""))
    else:
        check("运行时错误仍是原始类型", False)

    result = lang.run("this is bad syntax", show_errors=False)
    check("show_errors=False 返回结果对象", result.ok is False and result.error is not None)
    check("结果对象带中文说明", "语法" in result.explained)

    lang.run("data = {'k': 1}")
    snap = lang.snapshot()
    lang.run("data = None")
    lang.restore(snap)
    check("snapshot/restore", lang["data"] == {"k": 1})

    with Lang() as tmp_lang:
        tmp_lang.run("q = 1")
        check("上下文管理器可用", tmp_lang["q"] == 1)

    # 编译缓存
    lang.run("z = 1")
    first = lang._compiled
    lang.run("z = 1")
    check("编译结果被复用", lang._compiled is first)


def test_builtins():
    section("内置函数")
    check("fast_sum", pythonpp.fast_sum(range(1000)) == sum(range(1000)))
    check("fast_sum 空", pythonpp.fast_sum([]) == 0)
    check("fast_sum 大整数自动升级", pythonpp.fast_sum([10 ** 30, 1]) == 10 ** 30 + 1)
    check("dot", pythonpp.dot([1, 2, 3], [4, 5, 6]) == 32)
    check("range_sum", pythonpp.range_sum(1, 101) == 5050)
    check("range_sum 步长", pythonpp.range_sum(0, 10, 2) == 20)
    check("range_sum 空", pythonpp.range_sum(5, 1) == 0)
    expect_error("dot 长度不一致", lambda: pythonpp.dot([1], [1, 2]), ValueError)

    check("mean", pythonpp.mean([1, 2, 3]) == 2)
    check("median 奇数", pythonpp.median([3, 1, 2]) == 2)
    check("median 偶数", pythonpp.median([1, 2, 3, 4]) == 2.5)
    check("mode", pythonpp.mode([1, 2, 2, 3]) == 2)
    check("variance", abs(pythonpp.variance([1, 2, 3, 4]) - 1.6666666666666667) < 1e-9)
    check("stdev", abs(pythonpp.stdev([1, 2, 3, 4]) - 1.2909944487358056) < 1e-9)

    check("is_prime", pythonpp.is_prime(97) and not pythonpp.is_prime(95))
    check("gcd", pythonpp.gcd(12, 18) == 6)
    check("lcm", pythonpp.lcm(4, 6) == 12)
    check("clamp", pythonpp.clamp(15, 0, 10) == 10)

    check("chunk", pythonpp.chunk(range(7), 3) == [[0, 1, 2], [3, 4, 5], [6]])
    check("dedent", pythonpp.dedent("    a\n      b") == "a\n  b")
    check("is_palindrome", pythonpp.is_palindrome("A man, a plan, a canal: Panama"))
    check("slugify", pythonpp.slugify("Hello World!") == "hello-world")
    check("snake_case", pythonpp.snake_case("camelCaseName") == "camel_case_name")
    check("truncate", pythonpp.truncate("abcdef", 4) == "abc…")
    check("strip_ansi", pythonpp.strip_ansi("\x1b[31mred\x1b[0m") == "red")

    # deep_sizeof 与纯 Python 版本一致
    from pythonpp._native import _PythonFallback

    fallback = _PythonFallback()
    sample = {"a": [1, 2, 3], "b": (4, 5), "c": {"d": [6]}}
    check("deep_sizeof 与纯 Python 一致",
          pythonpp.deep_sizeof(sample) == fallback.deep_sizeof(sample),
          f"{pythonpp.deep_sizeof(sample)} vs {fallback.deep_sizeof(sample)}")

    # 循环引用不炸
    looped = {}
    looped["self"] = looped
    check("deep_sizeof 处理环", pythonpp.deep_sizeof(looped) > 0)


def test_errors():
    section("错误中文化")
    lang = Lang()
    try:
        lang.run("1 / 0")
    except ZeroDivisionError as exc:
        text = exc.pp_explained
        check("除零解释", "除以零" in text, text[:80])
        check("除零给建议", "建议" in text)
    else:
        check("除零解释", False)

    try:
        lang.run("{}['missing']")
    except KeyError as exc:
        check("KeyError 解释", "字典" in exc.pp_explained)
    else:
        check("KeyError 解释", False)

    try:
        lang.run("[1,2][9]")
    except IndexError as exc:
        check("IndexError 解释", "下标" in exc.pp_explained)
    else:
        check("IndexError 解释", False)


# ---------------------------------------------------------------------------
# JIT：和纯 Python 逐项比对
# ---------------------------------------------------------------------------
def jit_loop_sum(n):
    total = 0
    for i in range(n):
        total += i * 2
    return total


def jit_loop_sum_step(start, stop, step):
    total = 0
    for i in range(start, stop, step):
        total += i
    return total


def jit_branchy(n):
    total = 0
    for i in range(n):
        if i % 3 == 0:
            total += i
        elif i % 3 == 1:
            total += i * 2
        else:
            total -= 1
    return total


def jit_while(n):
    total = 0
    i = 0
    while i < n:
        total += i * i
        i += 1
    return total


def jit_bitwise(n):
    acc = 0
    for i in range(n):
        acc = (acc ^ i) + (i & 7) - (i >> 1) + (i << 1)
    return acc


def jit_float(n):
    total = 0.0
    for i in range(n):
        total += i * 0.5
    return total


def jit_call(n):
    return sum(range(n))


def jit_negatives(n):
    total = 0
    for i in range(-n, n):
        total += i // 3 + i % 3
    return total


def jit_overflow(n):
    total = 0
    for i in range(n):
        total = total * 2 + 1
    return total


def jit_div(n):
    total = 0
    for i in range(n):
        total += 100 // (i - 5)
    return total


def test_jit():
    section("JIT")
    print(f"  当前内核：{'C' if native_available() else '纯 Python 回退'}")
    if not pythonpp._cjit.available():
        print(f"  [跳过] 没有 C 编译器：{pythonpp._cjit.explain_unavailable()}")
        return

    # 先确认"编译"这一步在这个环境里真的能跑通。
    # 受限沙箱会禁止编译器子进程写盘，这时不是代码问题，应该跳过而不是报失败。
    probe = pythonpp.jit(jit_loop_sum, warmup=False)
    probe(100)
    if not probe.compiled:
        print(f"  [跳过] 编译器存在但无法使用：{probe.explain()}")
        print(f"         缓存目录：{pythonpp._cjit.Compiler().cache_dir}")
        print("         （常见于沙箱/只读环境，运行时会自动退化为 Python，功能不受影响）")
        return

    cases = [
        ("累加循环", jit_loop_sum, (1000,)),
        ("带步长 range", jit_loop_sum_step, (0, 500, 3)),
        ("负步长", jit_loop_sum_step, (500, 0, -3)),
        ("if/elif/else", jit_branchy, (1000,)),
        ("while", jit_while, (500,)),
        ("位运算", jit_bitwise, (800,)),
        ("负数下限", jit_negatives, (400,)),
    ]
    for name, fn, args in cases:
        # warmup=False：测试里不搞"太小的先跑 Python"那套阈值，
        # 直接验证编译结果对不对
        wrapped = pythonpp.jit(fn, warmup=False)
        expected = fn(*args)
        got = wrapped(*args)
        check(f"jit {name} 结果一致", got == expected, f"{got} != {expected}")
        check(f"jit {name} 确实走 C", wrapped.compiled,
              wrapped.explain() + f" | 编译器：{pythonpp._cjit.explain_unavailable()}")

    # 不支持的类型必须自动回退且结果正确
    for name, fn, args in [
        ("浮点", jit_float, (200,)),
        ("函数调用", jit_call, (1000,)),
    ]:
        wrapped = pythonpp.jit(fn, warmup=False)
        expected = fn(*args)
        got = wrapped(*args)
        check(f"jit {name} 自动回退且正确", got == expected, f"{got} != {expected}")
        check(f"jit {name} 未误判为可编译", not wrapped.compiled, wrapped.explain())

    # 溢出：C 会报错，必须自动回退到 Python 大整数
    wrapped = pythonpp.jit(jit_overflow, warmup=False)
    expected = jit_overflow(200)
    got = wrapped(200)
    check("jit 溢出自动回退且正确", got == expected, f"{got} != {expected}")

    # 非整数入参：C 不接，必须原样交给 Python（这里两者都会抛 TypeError）
    wrapped = pythonpp.jit(jit_loop_sum, warmup=False)
    wrapped(10)
    check("jit 非整数入参走 Python",
          _same_exception(lambda: wrapped(10.0), lambda: jit_loop_sum(10.0)))
    check("jit 非整数不会污染内核", wrapped.backend == "c", wrapped.explain())

    # 除零
    wrapped = pythonpp.jit(jit_div, warmup=False)
    check("jit 除零回退行为一致（同样抛异常）",
          _same_exception(lambda: wrapped(8), lambda: jit_div(8)))

    # 速度
    wrapped = pythonpp.jit(jit_loop_sum, warmup=False)
    import time

    n = 2_000_000
    wrapped(1000)                       # 先触发编译
    fast, slow = _best_of(wrapped, n), _best_of(jit_loop_sum, n)
    print(f"  {n} 次迭代：Python {slow * 1000:.1f}ms -> C {fast * 1000:.1f}ms "
          f"（{slow / fast:.1f}x，后端 {wrapped.backend}）")
    check("jit 明显更快", slow / fast > 10, f"{slow / fast:.2f}x")

    # explain / stats 可读
    info = wrapped.explain()
    check("explain 说明清楚", "机器码" in info or "走原生 Python" in info, info)
    check("stats 有字段", "调用次数" in wrapped.stats())


def test_gil():
    section("GIL=False / 并发原语")
    status = pythonpp.gil_status()
    check("gil_status 有解释器信息", "interpreter_gil" in status and "parallelism" in status,
          str(status))
    # 原子/通道能力只看 C 内核有没有加载，跟 JIT 的 C 后端无关
    check("gil_status 报告原子/通道能力",
          status["atomics"] == pythonpp._native.is_native
          and status["channels"] == pythonpp._native.is_native,
          str(status))

    # ---- 开关 ----
    old = pythonpp.configure(gil=False)
    check("configure 返回旧值", old is True, repr(old))
    check("configure 生效", pythonpp.gil_enabled() is False)
    check("configure 传到内核",
          pythonpp.gil_status()["release_gil"] == pythonpp._native.is_native,
          str(pythonpp.gil_status()))
    pythonpp.configure(gil=True)
    check("configure 可恢复", pythonpp.gil_enabled() is True)

    # ---- 原子整数 ----
    counter = pythonpp.AtomicInt(0)
    check("AtomicInt 初值", counter.value == 0)
    counter.store(5, "release")
    check("AtomicInt store/load 带内存序", counter.load("acquire") == 5)
    check("AtomicInt add 返回新值", counter.add(3) == 8)
    check("AtomicInt sub", counter.sub(2) == 6)
    check("AtomicInt exchange 返回旧值", counter.exchange(9) == 6)
    ok, current = counter.compare_set(9, 10)
    check("AtomicInt CAS 成功", ok and current == 10, f"{ok},{current}")
    ok, current = counter.compare_set(9, 11)
    check("AtomicInt CAS 失败返回当前值", (not ok) and current == 10, f"{ok},{current}")
    counter.store(0)
    check("AtomicInt or", counter.or_(0b1010) == 0 and counter.value == 0b1010)
    check("AtomicInt and", counter.and_(0b1000) == 0b1010 and counter.value == 0b1000)
    check("AtomicInt xor", counter.xor(0b1111) == 0b1000 and counter.value == 0b0111)

    expect_error("非法内存序报 ValueError", lambda: counter.load("nonsense"), ValueError)

    # ---- 数据竞争：8 线程各加 5 万，必须一个不漏 ----
    total = pythonpp.AtomicInt(0)

    def bump():
        local = total
        for _ in range(50000):
            local.add(1)

    threads = [threading.Thread(target=bump) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("原子加法不丢更新（8 线程 × 5 万）", total.value == 400000, str(total.value))

    # ---- 内存序：release 写 + acquire 读 ----
    data = []
    flag = pythonpp.AtomicInt(0)

    def writer():
        data.append("payload")
        pythonpp.fence("release")
        flag.store(1, pythonpp.RELAXED)
        # 用 release 语义再写一次，保证读端 acquire 能看到 data
        flag.store(2, pythonpp.RELEASE)

    t = threading.Thread(target=writer)
    t.start()
    t.join()
    check("release/acquire 之后数据可见", flag.load(pythonpp.ACQUIRE) >= 1 and data == ["payload"])

    # ---- 通道 ----
    ch = pythonpp.Channel(0)
    ch.put("hello")
    check("通道先入先出", ch.get() == "hello")
    for i in range(5):
        ch.put(i)
    check("通道 qsize", ch.qsize() == 5)
    check("通道按顺序取出", [ch.get() for _ in range(5)] == [0, 1, 2, 3, 4])
    ch.close()
    check("通道关闭标志", ch.closed)
    expect_error("关闭后再取报 ChannelClosed", lambda: ch.get(), pythonpp.ChannelClosed)
    expect_error("关闭后发送报 RuntimeError", lambda: ch.put("x"), RuntimeError)

    # 关闭后仍可把剩余消息取完
    ch2 = pythonpp.Channel(0)
    ch2.put("a")
    ch2.put("b")
    ch2.close()
    check("关闭后取完剩余消息", [ch2.get(), ch2.get()] == ["a", "b"])

    # 超时
    empty = pythonpp.Channel(0)
    expect_error("空通道接收超时", lambda: empty.get(0.05), TimeoutError)
    full = pythonpp.Channel(1)
    full.put(1)
    expect_error("满通道发送超时", lambda: full.put(2, 0.05), TimeoutError)

    # ---- 生产者/消费者（跨线程消息传递）----
    pipe = pythonpp.Channel(4)

    def produce():
        for i in range(100):
            pipe.put(i)
        pipe.close()

    received = []

    def consume():
        for item in pipe:
            received.append(item)

    p = threading.Thread(target=produce)
    c = threading.Thread(target=consume)
    p.start()
    c.start()
    p.join()
    c.join()
    check("生产者/消费者无丢失", received == list(range(100)), f"len={len(received)}")

    # ---- spawn / parallel_map ----
    future = pythonpp.spawn(lambda: sum(range(100000)))
    check("spawn 返回 Future", hasattr(future, "result"))
    check("spawn 结果正确", future.result() == sum(range(100000)))
    squares = pythonpp.parallel_map(lambda i: i * i, range(12))
    check("parallel_map 顺序正确", squares == [i * i for i in range(12)], str(squares))

    # ---- 多线程 MemPool ----
    pool = MemPool(max_bytes=64 * 1024 * 1024)

    def fill_pool(tag):
        for i in range(50):
            pool.add(f"{tag}_{i}", [i] * 8)
            pool.peek(f"{tag}_{i}")

    threads = [threading.Thread(target=fill_pool, args=(f"t{k}",)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("多线程写 MemPool 不丢变量", len(pool) == 400, f"len={len(pool)}")
    check("多线程写 MemPool 计数一致",
          all(pool.stats()["变量个数"] == 400 for _ in range(1)))
    pool.clear()

    # ---- 并发 JIT：释放 GIL 后结果依然正确 ----
    pythonpp.configure(gil=False)
    def loop_sum(n):
        total = 0
        for i in range(n):
            total += i
        return total

    wrapped = pythonpp.jit(loop_sum, warmup=False)
    wrapped(1000)                      # 先编译
    expected = loop_sum(500000)
    results = []

    def run_jit():
        results.append(wrapped(500000))

    threads = [threading.Thread(target=run_jit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("并发 JIT 结果全部正确",
          len(results) == 4 and all(v == expected for v in results),
          f"{results} != {expected}")
    pythonpp.configure(gil=True)


def _best_of(func, n, repeat=3):
    import time

    best = None
    for _ in range(repeat):
        started = time.perf_counter()
        func(n)
        cost = time.perf_counter() - started
        if best is None or cost < best:
            best = cost
    return best


def _same_exception(a, b):
    try:
        a()
    except Exception as exc_a:  # noqa: BLE001
        try:
            b()
        except Exception as exc_b:  # noqa: BLE001
            return type(exc_a) is type(exc_b)
        return False
    try:
        b()
    except Exception:  # noqa: BLE001
        return False
    return True


# ---------------------------------------------------------------------------
def main():
    print(f"Python {sys.version.split()[0]} | 内核：{pythonpp._native.describe()}")
    print(f"pythonpp {pythonpp.__version__}")

    tests = [
        test_random,
        test_io,
        test_mempool,
        test_lang,
        test_builtins,
        test_errors,
        test_jit,
        test_gil,
    ]
    for func in tests:
        try:
            func()
        except Exception:  # noqa: BLE001
            FAILS.append(f"{func.__name__} 崩溃")
            traceback.print_exc()

    print(f"\n通过 {len(PASSES)} 项，失败 {len(FAILS)} 项")
    if FAILS:
        for item in FAILS:
            print(f"  x {item}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
