# -*- coding: utf-8 -*-
"""随机数：默认就走 C 内核（xoshiro256**），没编译 C 时自动回退。

对外接口刻意保持和旧版一模一样：

    random()             -> 1~20 之间的整数
    random(1, 20)        -> 1~20
    random("1~20")       -> 1~20
    random("5~100")      -> 5~100
    random(1.0, 2.0)     -> 浮点区间
    random(10)           -> 0~10
    random([1, 2, 3])    -> 从序列里挑一个
    random() 的分布不再是取模偏置，长区间也均匀了。
"""

from __future__ import annotations

from typing import Any, Sequence

from ._native import native

__all__ = ["random", "seed", "shuffle", "randbytes", "choice", "sample"]

_SEQ_TYPES = (list, tuple, str, bytes, bytearray, range)


def seed(value: Any = None) -> None:
    """重置随机数种子。传 None 表示用时间重新播种。"""
    native.seed(value)


def random(a: Any = "1~20", b: Any = None) -> Any:
    """产生随机值，见模块文档。"""
    # 序列：随机挑一个（str 单独处理，它的语义是"1~20"这种区间写法）
    if b is None and isinstance(a, _SEQ_TYPES) and not isinstance(a, str):
        if len(a) == 0:
            raise IndexError("random() 不能从空序列里取值")
        return a[native.random_int(0, len(a) - 1)]

    # 两个浮点：返回浮点
    if b is not None and (isinstance(a, float) or isinstance(b, float)):
        lo, hi = float(a), float(b)
        if hi < lo:
            lo, hi = hi, lo
        return lo + (hi - lo) * native.random_float()

    if b is not None:
        lo, hi = int(a), int(b)
    elif isinstance(a, str):
        text = a.strip()
        if "~" not in text and ".." not in text:
            value = int(text)
            lo, hi = 0, value
        else:
            sep = "~" if "~" in text else ".."
            left, right = text.split(sep, 1)
            lo, hi = int(left.strip()), int(right.strip())
    elif isinstance(a, float):
        lo, hi = 0, int(a)
    else:
        lo, hi = 0, int(a)

    if hi < lo:
        lo, hi = hi, lo
    return int(native.random_int(lo, hi))


def shuffle(seq) -> None:
    """原地洗牌（C 实现，比 random.shuffle 快）。"""
    if isinstance(seq, list):
        native.shuffle(seq)
        return
    if not hasattr(seq, "__setitem__"):
        raise TypeError("shuffle() 需要可变序列")
    for i in range(len(seq) - 1, 0, -1):
        j = native.random_int(0, i)
        seq[i], seq[j] = seq[j], seq[i]


def randbytes(n: int) -> bytes:
    """n 个随机字节（C 实现）。"""
    return native.random_bytes(n)


def choice(seq: Sequence):
    """从序列中随机取一个元素。"""
    if len(seq) == 0:
        raise IndexError("choice() 不能从空序列里取值")
    return seq[native.random_int(0, len(seq) - 1)]


def sample(seq: Sequence, k: int):
    """不重复地取 k 个元素（Floyd 算法，O(k)）。"""
    n = len(seq)
    k = int(k)
    if k < 0 or k > n:
        raise ValueError("sample() 的 k 必须满足 0 <= k <= len(seq)")
    pool = list(seq)
    chosen = set()
    result = []
    for j in range(n - k, n):
        t = native.random_int(0, j)
        if t in chosen:
            t = j
        chosen.add(t)
        result.append(pool[t])
    return result
