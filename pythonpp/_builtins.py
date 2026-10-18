# -*- coding: utf-8 -*-
"""把 Python++ 自带的函数注入 Lang 的执行环境。

单独一层是为了：
    * 延迟导入，避免循环依赖；
    * ``Lang(builtins_=...)`` 可以覆盖任意一个；
    * 旧版 ``Lang`` 里那 9 个内置函数的名字和用法保持不变。
"""

from __future__ import annotations

from ._rand import choice, randbytes, random, sample, seed, shuffle
from ._io import (
    append,
    copy,
    exists,
    home,
    is_dir,
    is_file,
    join,
    lines,
    listdir,
    mkdir,
    move,
    pwd,
    read,
    read_bytes,
    remove,
    rmtree,
    size,
    splitext,
    touch,
    write,
    write_bytes,
)
from ._misc import bocodepy, counter, nex, now, sleep, timeit
from ._calcs import (
    clamp,
    deep_sizeof,
    dot,
    fast_sum,
    gcd,
    is_prime,
    lcm,
    mean,
    median,
    mode,
    range_sum,
    stdev,
    variance,
)
from ._strutil import chunk, dedent, is_palindrome, slugify, snake_case, strip_ansi, truncate
from ._jit import jit

_BUILTINS = {
    # 旧版就有的
    "random": random,
    "seed": seed,
    "nex": nex,
    "bocodepy": bocodepy,
    "pwd": pwd,
    "mkdir": mkdir,
    "touch": touch,
    "read": read,
    "write": write,
    # 随机
    "shuffle": shuffle,
    "randbytes": randbytes,
    "choice": choice,
    "sample": sample,
    # 文件
    "join": join,
    "home": home,
    "exists": exists,
    "is_dir": is_dir,
    "is_file": is_file,
    "listdir": listdir,
    "remove": remove,
    "rmtree": rmtree,
    "copy": copy,
    "move": move,
    "size": size,
    "splitext": splitext,
    "append": append,
    "read_bytes": read_bytes,
    "write_bytes": write_bytes,
    "lines": lines,
    # 计算
    "deep_sizeof": deep_sizeof,
    "fast_sum": fast_sum,
    "dot": dot,
    "range_sum": range_sum,
    "mean": mean,
    "median": median,
    "mode": mode,
    "variance": variance,
    "stdev": stdev,
    "clamp": clamp,
    "is_prime": is_prime,
    "gcd": gcd,
    "lcm": lcm,
    # 字符串
    "chunk": chunk,
    "dedent": dedent,
    "is_palindrome": is_palindrome,
    "slugify": slugify,
    "snake_case": snake_case,
    "truncate": truncate,
    "strip_ansi": strip_ansi,
    # 杂项
    "now": now,
    "sleep": sleep,
    "timeit": timeit,
    "counter": counter,
    # JIT
    "jit": jit,
}

_NAMES = frozenset(_BUILTINS)


def export():
    """返回一份新的 {名字: 函数}，Lang 每建一个实例拿一份。"""
    return dict(_BUILTINS)


def names():
    """所有预置名字。"""
    return _NAMES
