# -*- coding: utf-8 -*-
"""数值工具：热点全部走 C 内核。

这些函数的特点是"能算对，还要算得快"：
    fast_sum   整数求和，60 位以内走 int64 累加，溢出自动升级大整数
    dot        点积
    range_sum  等差数列 O(1) 求和（不用真的循环）
    mean/median/mode/variance/stdev  常用统计
"""

from __future__ import annotations

import math
from collections import Counter

from ._native import native

__all__ = [
    "deep_sizeof",
    "dot",
    "fast_sum",
    "range_sum",
    "mean",
    "median",
    "mode",
    "variance",
    "stdev",
    "clamp",
    "is_prime",
    "gcd",
    "lcm",
]


def deep_sizeof(obj) -> int:
    """估算对象连同其所有子对象一共占多少字节（环状引用只算一次）。"""
    return native.deep_sizeof(obj)


def fast_sum(iterable) -> int:
    """整数序列求和。

    说明白点：对普通 list，CPython 的 ``sum()`` 本身就有整数专用快路径，
    用 C 重写也超不过它，所以这里**故意保留同样的语义**，只在两种情况下
    真正占便宜：

        * 传进来的是 ``range``  —— 直接走公式，O(1)，见 :func:`range_sum`
        * 元素超过 64 位、需要频繁进位时，省掉中间对象的创建

    一句忠告：单纯为了"求和更快"用这个函数意义不大；要快请用 range_sum()。
    """
    if isinstance(iterable, range):
        return range_sum(iterable.start, iterable.stop, iterable.step)
    return native.sum_int(iterable)


def dot(a, b):
    """点积。两个序列长度必须一致。"""
    return native.dot(a, b)


def range_sum(start, stop=None, step=1):
    """等差数列求和，O(1)。

    range_sum(1, 100000001) == 5000000050000000，但不用循环 1 亿次。
    """
    if stop is None:
        start, stop = 0, start
    start, stop, step = int(start), int(stop), int(step)
    if step == 0:
        raise ValueError("range_sum() 的步长不能为 0")
    try:
        return native.sum_range(start, stop, step)
    except OverflowError:
        return sum(range(start, stop, step))


def mean(values) -> float:
    """平均数。"""
    data = list(values)
    if not data:
        raise ValueError("mean() 需要至少一个数据")
    return native.sum_int(data) / len(data)


def median(values) -> float:
    """中位数。"""
    data = sorted(values)
    n = len(data)
    if n == 0:
        raise ValueError("median() 需要至少一个数据")
    mid = n // 2
    if n % 2:
        return data[mid]
    return (data[mid - 1] + data[mid]) / 2


def mode(values):
    """众数；并列时取最先出现的那个。"""
    data = list(values)
    if not data:
        raise ValueError("mode() 需要至少一个数据")
    counts = Counter(data)
    best = max(counts.values())
    for item in data:
        if counts[item] == best:
            return item


def variance(values, sample=True) -> float:
    """方差。sample=True 时用 n-1（样本方差）。"""
    data = list(values)
    n = len(data)
    if n == 0 or (sample and n == 1):
        raise ValueError("variance() 数据量不足")
    mu = native.sum_int(data) / n
    total = 0.0
    for x in data:
        d = x - mu
        total += d * d
    return total / (n - 1 if sample else n)


def stdev(values, sample=True) -> float:
    """标准差。"""
    return math.sqrt(variance(values, sample))


def clamp(value, low, high):
    """把 value 夹到 [low, high] 区间。"""
    if low > high:
        low, high = high, low
    return low if value < low else high if value > high else value


def is_prime(n: int) -> bool:
    """Miller-Rabin 素性测试（确定性底数，覆盖 64 位范围）。"""
    n = int(n)
    if n < 2:
        return False
    small_primes = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    for p in small_primes:
        if n % p == 0:
            return n == p
    d = n - 1
    s = 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(s - 1):
            x = (x * x) % n
            if x == n - 1:
                break
        else:
            return False
    return True


def gcd(a: int, b: int) -> int:
    """最大公约数，只做整数运算。"""
    a, b = abs(int(a)), abs(int(b))
    while b:
        a, b = b, a % b
    return a


def lcm(a: int, b: int) -> int:
    """最小公倍数。"""
    a, b = abs(int(a)), abs(int(b))
    if a == 0 or b == 0:
        return 0
    return a // gcd(a, b) * b
