# -*- coding: utf-8 -*-
"""杂项小功能：保留旧版行为，顺手补齐。"""

from __future__ import annotations

import time

__all__ = ["nex", "bocodepy", "now", "sleep", "timeit", "counter"]


def nex():
    """旧版就有的占位函数，保留以免老代码报错。"""
    return None


def bocodepy():
    """彩蛋。"""
    return "8:16"


def now(fmt="%Y-%m-%d %H:%M:%S") -> str:
    """当前时间字符串。"""
    return time.strftime(fmt)


def sleep(seconds: float) -> None:
    """睡一会儿。"""
    time.sleep(seconds)


def timeit(func, *args, repeat=5, **kwargs):
    """测量函数平均耗时（秒）。返回 (最短耗时, 结果)。"""
    best = None
    result = None
    for _ in range(max(1, int(repeat))):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        cost = time.perf_counter() - start
        if best is None or cost < best:
            best = cost
    return best, result


def counter(iterable):
    """计数，等价于 collections.Counter 的轻量版。"""
    counts = {}
    for item in iterable:
        counts[item] = counts.get(item, 0) + 1
    return counts
