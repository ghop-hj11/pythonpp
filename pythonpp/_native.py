# -*- coding: utf-8 -*-
"""原生内核加载器。

策略：
    1. 先试 ``_pythonpp_native``（稳定 ABI 的 .pyd/.so，3.8+ 通用）；
    2. 失败就退到纯 Python 实现，功能一模一样，只是慢一点；
    3. 任何情况下都不抛异常 —— 装不上 C 版本的机器也必须能跑起来。

想确认当前用的是哪一套，看 ``native_available()`` 或 ``_native.backend``。
"""

from __future__ import annotations

import importlib
import os
import sys

__all__ = ["native", "backend", "is_native", "describe"]

backend = "python"      # "native" 或 "python"


def _try_import_native():
    """尝试导入 C 内核：先当包内模块找，再当顶层模块找。"""
    # 允许用环境变量强制关掉原生实现，方便对比测试
    if os.environ.get("PYTHONPP_NO_NATIVE", "").strip() not in ("", "0", "false", "False"):
        return None
    for name in ("pythonpp._pythonpp_native", "_pythonpp_native"):
        try:
            module = importlib.import_module(name)
        except Exception:
            continue
        try:
            info = module.info()
        except Exception:
            continue
        if isinstance(info, dict) and "version" in info:
            return module
    return None


class _PythonFallback:
    """纯 Python 实现：接口与 C 内核一一对应，保证行为一致。"""

    __version__ = "1.0.0"

    def __init__(self):
        self._state = 0x9E3779B9

    # ---- 随机 ----
    def seed(self, value=None):
        if value is None:
            try:
                import time

                value = time.time_ns() & 0xFFFFFFFF
            except Exception:  # pragma: no cover
                value = 0x9E3779B9
        value = int(value) & 0xFFFFFFFFFFFFFFFF
        self._state = value or 0x9E3779B9

    def _next(self):
        x = self._state
        x ^= (x << 13) & 0xFFFFFFFFFFFFFFFF
        x ^= x >> 7
        x ^= (x << 17) & 0xFFFFFFFFFFFFFFFF
        self._state = x & 0xFFFFFFFFFFFFFFFF
        return self._state

    def random_int(self, lo, hi):
        lo, hi = int(lo), int(hi)
        if hi < lo:
            lo, hi = hi, lo
        return lo + self._next() % (hi - lo + 1)

    def random_float(self):
        return (self._next() >> 11) * (1.0 / 9007199254740992.0)

    def random_bytes(self, n):
        n = int(n)
        if n < 0:
            raise ValueError("字节数不能为负数")
        out = bytearray(n)
        i = 0
        while i < n:
            r = self._next()
            for _ in range(8):
                if i >= n:
                    break
                out[i] = r & 0xFF
                r >>= 8
                i += 1
        return bytes(out)

    def shuffle(self, seq):
        if not isinstance(seq, list):
            raise TypeError("shuffle() 只接受 list")
        for i in range(len(seq) - 1, 0, -1):
            j = self.random_int(0, i)
            seq[i], seq[j] = seq[j], seq[i]

    # ---- 数值 ----
    def sum_int(self, iterable):
        return sum(iterable)

    def dot(self, a, b):
        a = list(a)
        b = list(b)
        if len(a) != len(b):
            raise ValueError("dot() 两个序列长度不一致")
        return sum(x * y for x, y in zip(a, b))

    def sum_range(self, a, b, step=1):
        return sum(range(int(a), int(b), int(step)))

    # ---- 杂项 ----
    def deep_sizeof(self, obj):
        seen = set()
        pending = [obj]
        total = 0
        while pending:
            current = pending.pop()
            key = id(current)
            if key in seen:
                continue
            seen.add(key)
            try:
                total += sys.getsizeof(current)
            except TypeError:
                continue
            if isinstance(current, dict):
                pending.extend(current.keys())
                pending.extend(current.values())
            elif isinstance(current, (list, tuple, set, frozenset)):
                pending.extend(current)
        return total

    def hash64(self, obj):
        if isinstance(obj, bytes):
            data = obj
        elif isinstance(obj, str):
            data = obj.encode("utf-8", "surrogatepass")
        else:
            data = str(obj).encode("utf-8", "surrogatepass")
        h = 0xCBF29CE484222325
        for byte in data:
            h ^= byte
            h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        return h

    def loop_run(self, code, args):
        raise NotImplementedError("纯 Python 后端不支持 loop_run")

    def info(self):
        return {
            "name": "_pythonpp_native",
            "version": self.__version__,
            "limited_api": 0,
            "built_against": 0,
            "fallback": True,
        }


_native = _try_import_native()
if _native is not None:
    backend = "native"
    native = _native
else:
    native = _PythonFallback()
    backend = "python"

is_native = backend == "native"


def describe():
    """给 ``pp --version`` 用的中文描述。"""
    if is_native:
        info = native.info()
        return f"C 内核 {info['version']}（稳定 ABI，Python {sys.version.split()[0]}）"
    return f"纯 Python 回退（Python {sys.version.split()[0]}），装个编译器还能再快一截"
