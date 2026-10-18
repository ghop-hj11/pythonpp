# -*- coding: utf-8 -*-
"""内置基准测试：用真实数据说明"提速"提在哪。

    python -m pythonpp --bench

每一项都同时跑"原生写法"和"Python++ 写法"，并给出倍数。
"""

from __future__ import annotations

import random as std_random
import time

from . import _cjit, _native
from ._calcs import fast_sum, range_sum
from ._rand import random as pp_random
from ._rand import seed as pp_seed

__all__ = ["main", "run_all"]


def _timeit(func, repeat=5):
    best = None
    result = None
    for _ in range(repeat):
        started = time.perf_counter()
        result = func()
        cost = time.perf_counter() - started
        if best is None or cost < best:
            best = cost
    return best, result


def _row(name, python_time, pp_time, note=""):
    ratio = python_time / pp_time if pp_time else float("inf")
    if ratio >= 1e6:
        shown = f"{ratio / 1e6:.0f} 百万"
    else:
        shown = f"{ratio:.1f}"
    flag = "快" if ratio >= 1.05 else ("慢" if ratio <= 0.95 else "持平")
    print(
        f"  {name:<22} Python {python_time * 1000:>8.2f} ms | "
        f"Python++ {pp_time * 1000:>8.2f} ms | "
        f"{shown:>9}x {flag}{('  ' + note) if note else ''}"
    )
    return ratio


def run_all(verbose=True):
    results = {}
    if verbose:
        print(f"Python {__import__('sys').version.split()[0]} | {_native.describe()}")
        print(f"JIT：{_cjit.explain_unavailable()}\n")

    # 1. 随机数 -------------------------------------------------------------
    n = 200_000
    std_random.seed(42)
    pp_seed(42)
    py_t, _ = _timeit(lambda: [std_random.randint(1, 1000) for _ in range(n)])
    pp_t, _ = _timeit(lambda: [pp_random(1, 1000) for _ in range(n)])
    if verbose:
        print("【随机数】生成 20 万个 1~1000 的整数")
    results["random"] = _row("random", py_t, pp_t)

    # 2. 求和 ---------------------------------------------------------------
    data = list(range(1, 1_000_001))
    py_t, _ = _timeit(lambda: sum(data))
    pp_t, _ = _timeit(lambda: fast_sum(data))
    if verbose:
        print("\n【求和】100 万个整数的 list")
    results["sum"] = _row("fast_sum(list)", py_t, pp_t, "略慢：sum() 有 C 专用路径")

    # 3. 等差求和（O(1) vs 循环）-------------------------------------------
    py_t, _ = _timeit(lambda: sum(range(100_000_000)), repeat=1)
    pp_t, _ = _timeit(lambda: range_sum(1, 100_000_001))
    if verbose:
        print("\n【等差求和】1 到 1 亿")
    results["range_sum"] = _row("range_sum", py_t, pp_t, "O(1) 公式")

    # 3b. 对 range 求和也用公式 ---------------------------------------------
    py_t, _ = _timeit(lambda: sum(range(10_000_000)), repeat=1)
    pp_t, _ = _timeit(lambda: fast_sum(range(10_000_000)))
    if verbose:
        print("\n【求和】range(1000 万) —— fast_sum 自动走公式")
    results["fast_sum_range"] = _row("fast_sum(range)", py_t, pp_t, "O(1)")

    # 4. 深度 sizeof --------------------------------------------------------
    sample = {"a": list(range(5000)), "b": {"c": [str(i) for i in range(2000)]}}

    def py_sizeof():
        import sys

        seen, pending, total = set(), [sample], 0
        while pending:
            cur = pending.pop()
            if id(cur) in seen:
                continue
            seen.add(id(cur))
            total += sys.getsizeof(cur)
            if isinstance(cur, dict):
                pending.extend(cur.keys())
                pending.extend(cur.values())
            elif isinstance(cur, (list, tuple, set, frozenset)):
                pending.extend(cur)
        return total

    from ._calcs import deep_sizeof

    py_t, py_val = _timeit(py_sizeof)
    pp_t, pp_val = _timeit(lambda: deep_sizeof(sample))
    if verbose:
        print("\n【内存估算】5000 元素列表 + 2000 字符串")
    results["deep_sizeof"] = _row("deep_sizeof", py_t, pp_t)
    assert py_val == pp_val, "deep_sizeof 结果必须与纯 Python 一致"

    # 5. JIT ---------------------------------------------------------------
    if _cjit.available():
        source_file = _write_bench_module()
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("_pp_bench_mod", source_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            from ._jit import jit

            n_iter = 20_000_000
            fast = jit(module.bench_loop, warmup=False)
            fast(1000)                       # 触发编译
            py_t, expected = _timeit(lambda: module.bench_loop(n_iter), repeat=3)
            pp_t, got = _timeit(lambda: fast(n_iter), repeat=3)
            assert expected == got, "JIT 结果必须与纯 Python 一致"
            if verbose:
                print(f"\n【JIT】2000 万次循环迭代（后端 {fast.backend}）")
            results["jit"] = _row("jit 循环", py_t, pp_t, "编译成机器码")
        finally:
            try:
                __import__("os").unlink(source_file)
            except OSError:
                pass
    elif verbose:
        print("\n【JIT】没有 C 编译器，跳过（pip install ziglang 可启用）")

    if verbose:
        print("\n说明：倍数越高越好；'持平' 说明这个场景本来就够快，没必要加速。")
    return results


def _write_bench_module():
    import os
    import tempfile

    source = '''# -*- coding: utf-8 -*-
def bench_loop(n):
    total = 0
    for i in range(n):
        if i % 3 == 0:
            total += i * 2
        else:
            total -= 1
    return total
'''
    fd, path = tempfile.mkstemp(suffix=".py", prefix="pp_bench_")
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        fp.write(source)
    return path


def main():
    run_all(verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
