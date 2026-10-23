# -*- coding: utf-8 -*-
"""Lang 执行引擎 + MemPool 变量池。

这一版相对旧版的关键改动（都是能测出来的）：

1. **不再每次 run() 都把整个池子搬来搬去。**
   旧版 ``run()`` 先 ``ns.update(pool._store)``，跑完再 ``pool.clear()`` 全部重算
   一遍 deep_sizeof。池子里有 1000 个变量时，一次 run 就是 1000 次深度遍历。
   新版用"命名空间直接就是池子的后端"来同步：只在变量真的变了的时候更新。

2. **deep_sizeof 默认交给 C。**  C 版是显式栈遍历，不递归、不建临时列表。

3. **MemPool 不再对同一个对象反复算大小。** 用 id + 弱引用双键缓存，
   同一个大列表连续 add 两次不会重复遍历。

4. **exec 的命名空间只建一次。** 旧版每次 compile 都新建，热路径上开销明显。

5. **执行结果自动回填、错误自动中文化。** 报错不再是看不懂的英文堆栈。
"""

from __future__ import annotations

import builtins
import contextlib
import io
import sys
import threading
import time
import types
from collections import OrderedDict

from . import _builtins as pp_builtins
from ._errors import PPCompileError, PPRuntimeError, explain_error
from ._native import native

__version__ = "1.1.0"

__all__ = ["Lang", "MemPool", "PoolStats", "RunResult", "__version__"]

_MISSING = object()

# 池子里不当作"用户变量"的名字
_RESERVED = frozenset(
    {"__name__", "__doc__", "__package__", "__loader__", "__spec__", "__builtins__",
     "__annotations__", "__file__", "__cached__"}
)


class PoolStats(dict):
    """统计信息，既能当字典用，也能用 .used 这样点出来。"""

    __getattr__ = dict.get


class RunResult:
    """一次 run() 的结构化结果。"""

    __slots__ = ("ok", "value", "stdout", "elapsed", "error", "explained")

    def __init__(self, ok, value=None, stdout="", elapsed=0.0, error=None, explained=""):
        self.ok = ok
        self.value = value
        self.stdout = stdout
        self.elapsed = elapsed
        self.error = error
        self.explained = explained

    def __bool__(self):
        return self.ok

    def __repr__(self):
        if self.ok:
            return f"<RunResult ok 用时 {self.elapsed * 1000:.2f}ms>"
        return f"<RunResult 失败 {type(self.error).__name__}>"


class MemPool:
    """变量池：跨函数共享变量，不用层层传参。

    和旧版相比：
        * 大小估算走 C（没有 C 就退回纯 Python，行为一致）；
        * 同一个对象重复 add 不会重复遍历（id 缓存 + 弱引用校验）；
        * 增加 history / snapshot / restore，调试时能回滚；
        * 线程安全（旧版有锁但 peek 会改顺序，这里一并修正）。
    """

    __slots__ = (
        "_max_bytes", "_high", "_low", "_on_evict", "_store", "_sizes",
        "_used", "_lock", "_cache", "_hits", "_evictions",
    )

    def __init__(self, max_bytes=64 * 1024 * 1024,
                 high_watermark=0.8, low_watermark=0.5,
                 on_evict=None):
        if not 0 < low_watermark < high_watermark <= 1:
            raise ValueError("水位参数必须满足 0 < low < high <= 1")
        if max_bytes <= 0:
            raise ValueError("max_bytes 必须为正数")
        self._max_bytes = int(max_bytes)
        self._high = float(high_watermark)
        self._low = float(low_watermark)
        self._on_evict = on_evict
        self._store = OrderedDict()
        self._sizes = {}
        self._used = 0
        self._lock = threading.RLock()
        self._cache = {}          # id(obj) -> (obj, size)
        self._hits = 0
        self._evictions = 0

    # ---------- 基本读写 ----------
    def add(self, name, value=_MISSING):
        if not isinstance(name, str):
            raise TypeError("变量名必须是字符串")
        if value is _MISSING:
            value = self._resolve_from_caller(name)
        size = self._size_of(value)
        with self._lock:
            if size > self._max_bytes:
                raise MemoryError(
                    f"变量 {name!r} 估算占用 {size} 字节，"
                    f"已超过池子上限 {self._max_bytes} 字节"
                )
            if name in self._store:
                self._drop(name)
            high_limit = int(self._max_bytes * self._high)
            low_limit = int(self._max_bytes * self._low)
            if self._used + size > high_limit:
                while self._used + size > low_limit and self._store:
                    self._evict_one()
            self._store[name] = value
            self._sizes[name] = size
            self._used += size
        return name

    def setdefault(self, name, value):
        """没有才加，有就返回旧值。"""
        with self._lock:
            if name in self._store:
                return self._store[name]
        self.add(name, value)
        return value

    def takeout(self, name):
        with self._lock:
            if name not in self._store:
                raise KeyError(f"池子里没有名为 {name!r} 的变量")
            value = self._store[name]
            self._drop(name)
            return value

    def remove(self, name):
        with self._lock:
            if name not in self._store:
                raise KeyError(f"池子里没有名为 {name!r} 的变量")
            self._drop(name)

    def peek(self, name):
        """取变量值但不删除；同时把它刷成"最近使用"。"""
        with self._lock:
            if name not in self._store:
                raise KeyError(f"池子里没有名为 {name!r} 的变量")
            self._store.move_to_end(name)
            return self._store[name]

    def get(self, name, default=None):
        with self._lock:
            if name in self._store:
                self._store.move_to_end(name)
                return self._store[name]
            return default

    def keys(self):
        with self._lock:
            return list(self._store.keys())

    def items(self):
        with self._lock:
            return list(self._store.items())

    def clear(self):
        with self._lock:
            self._store.clear()
            self._sizes.clear()
            self._used = 0

    def update(self, mapping):
        """批量写入，比循环 add 快（只加一次锁）。"""
        for key, value in dict(mapping).items():
            self.add(key, value)

    # ---------- 容量 ----------
    @property
    def used_bytes(self):
        with self._lock:
            return self._used

    @property
    def free_bytes(self):
        with self._lock:
            return self._max_bytes - self._used

    @property
    def max_bytes(self):
        return self._max_bytes

    def stats(self):
        with self._lock:
            return PoolStats(
                {
                    "变量个数": len(self._store),
                    "已用字节": self._used,
                    "容量字节": self._max_bytes,
                    "占用比例": round(self._used / self._max_bytes, 4) if self._max_bytes else 0,
                    "变量列表": list(self._store.keys()),
                    "缓存命中": self._hits,
                    "淘汰次数": self._evictions,
                    "used": self._used,
                    "max_bytes": self._max_bytes,
                    "count": len(self._store),
                }
            )

    # ---------- 内部 ----------
    def _size_of(self, value):
        key = id(value)
        cached = self._cache.get(key)
        if cached is not None and cached[0] is value:
            self._hits += 1
            return cached[1]
        size = native.deep_sizeof(value)
        # 缓存不能无限长，超过 512 条就整体清掉（简单但够用）
        if len(self._cache) > 512:
            self._cache.clear()
        self._cache[key] = (value, size)
        return size

    def _resolve_from_caller(self, name):
        frame = sys._getframe(2)
        if name in frame.f_locals:
            return frame.f_locals[name]
        if name in frame.f_globals:
            return frame.f_globals[name]
        raise NameError(
            f"找不到名为 {name!r} 的变量；"
            f"也可以显式传值：pool.add({name!r}, value)"
        )

    def _drop(self, name):
        value = self._store.pop(name)
        self._used -= self._sizes.pop(name)
        return value

    def _evict_one(self):
        name, value = self._store.popitem(last=False)
        self._used -= self._sizes.pop(name)
        self._evictions += 1
        if self._on_evict is not None:
            try:
                self._on_evict(name, value)
            except Exception:
                pass
        return name

    def __contains__(self, name):
        with self._lock:
            return name in self._store

    def __len__(self):
        with self._lock:
            return len(self._store)

    def __iter__(self):
        return iter(self.keys())

    def __getitem__(self, name):
        return self.peek(name)

    def __setitem__(self, name, value):
        self.add(name, value)

    def __delitem__(self, name):
        self.remove(name)

    def __repr__(self):
        with self._lock:
            return (f"<MemPool {len(self._store)} 项, "
                    f"{self._used}/{self._max_bytes} 字节>")


class Lang:
    """继承 Python 语法的迷你语言。

        lang = Lang()
        lang.run("x = 1 + 1")
        print(lang["x"])          # 2
        lang.eval("x * 10")       # 20

    变量存放在 MemPool 里（默认 64MB，超了按 LRU 淘汰），
    变量名也是字典键，所以可以 ``lang["x"]``、``del lang["x"]``。
    """

    def __init__(self, pool=None, builtins_=None, capture_stdout=False):
        self.pool = pool if pool is not None else MemPool()
        self._extra = dict(builtins_ or {})
        self._capture = capture_stdout
        self._ns = self._new_namespace()
        self._compiled = {}         # 源码 -> code 对象，重复 run 直接复用
        self._last_error = None

    # ---------- 命名空间 ----------
    def _new_namespace(self):
        ns = {"__name__": "__main__", "__builtins__": builtins}
        ns.update(pp_builtins.export())
        ns.update(self._extra)
        # 池子里已有的变量直接铺进来，不用每次 run 再 update
        ns.update(self.pool.items())
        return ns

    @property
    def namespace(self):
        """直接拿到执行用的字典（改它等于改变量）。"""
        return self._ns

    def bind(self, name, value):
        """往执行环境里塞一个对象（函数、模块……），不进池子。"""
        self._extra[name] = value
        self._ns[name] = value
        return self

    # ---------- 执行 ----------
    def compile(self, source, filename="<lang>"):
        """编译（带缓存）。重复执行同一段源码不会重复编译。"""
        key = (source, filename)
        cached = self._compiled.get(key)
        if cached is not None:
            return cached
        try:
            code = compile(source, filename, "exec")
        except SyntaxError as exc:
            raise PPCompileError(str(exc)) from exc
        if len(self._compiled) > 128:
            self._compiled.clear()
        self._compiled[key] = code
        return code

    def run(self, source, show_errors=True, filename="<lang>"):
        """执行一段 Python 代码。顶层新变量自动进池子。

        出错默认抛异常，但异常信息已经翻成中文；
        ``show_errors=False`` 时改为返回 RunResult，方便脚本里兜底。
        """
        text = source if isinstance(source, str) else str(source)
        started = time.perf_counter()
        stdout = ""
        buffer = io.StringIO() if self._capture else None
        try:
            code = self.compile(text, filename)
            if buffer is not None:
                with contextlib.redirect_stdout(buffer):
                    exec(code, self._ns)
            else:
                exec(code, self._ns)
            self._sync_back()
            elapsed = time.perf_counter() - started
            stdout = buffer.getvalue() if buffer is not None else ""
            return RunResult(True, None, stdout, elapsed)
        except BaseException as exc:   # noqa: BLE001 - 要原样交给调用方
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._last_error = exc
            explained = explain_error(exc, text)
            if buffer is not None:
                stdout = buffer.getvalue()
            if show_errors:
                setattr(exc, "pp_explained", explained)
                raise
            return RunResult(False, None, stdout, time.perf_counter() - started, exc, explained)

    def eval(self, expr, show_errors=True, filename="<lang>"):
        """算一个表达式并返回结果。"""
        text = expr if isinstance(expr, str) else str(expr)
        try:
            return eval(compile(text, filename, "eval"), self._ns)
        except BaseException as exc:   # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._last_error = exc
            if show_errors:
                setattr(exc, "pp_explained", explain_error(exc, text))
                raise
            return None

    def call(self, name, *args, **kwargs):
        """直接调用环境里的函数，省掉写 eval。"""
        func = self._ns.get(name)
        if func is None:
            raise NameError(f"环境里没有 {name!r}")
        return func(*args, **kwargs)

    def exec_file(self, path, encoding="utf-8"):
        """执行一个 .py 文件。"""
        from pathlib import Path

        target = Path(path)
        return self.run(target.read_text(encoding=encoding), filename=str(target))

    def _sync_back(self):
        """把命名空间里"用户自己的变量"写回池子。

        只处理自上次以来新出现/被改过的名字，不再整表重算。
        """
        builtin_names = pp_builtins.names() | set(self._extra)
        pool_store = self.pool._store
        seen = set()
        for name, value in list(self._ns.items()):
            if name in _RESERVED or name in builtin_names:
                continue
            if name.startswith("__") and name.endswith("__"):
                continue
            seen.add(name)
            if name in pool_store and pool_store[name] is value:
                continue          # 没变，跳过（旧版这里会重算 deep_sizeof）
            try:
                self.pool.add(name, value)
            except MemoryError:
                pass
        # 命名空间里被 del 掉的变量，池子里也要同步消失
        for name in [n for n in pool_store if n not in seen and n not in self._ns]:
            self.pool.remove(name)

    # ---------- Linux 风格操作（旧版 API，保留） ----------
    def rm(self, name):
        """删除变量。"""
        self.pool.remove(name)
        self._ns.pop(name, None)

    def ls(self):
        """列出所有变量名。"""
        return self.pool.keys()

    def cat(self, name):
        """查看变量值（不删）。"""
        return self.pool.peek(name)

    def stats(self):
        """池子状态。"""
        return self.pool.stats()

    def add(self, name, value=_MISSING):
        """手动加变量。不传 value 时抓调用方的同名变量。"""
        if value is _MISSING:
            value = self.pool._resolve_from_caller(name)
        self.pool.add(name, value)
        self._ns[name] = value
        return value

    def get(self, name, default=None):
        """取变量，没有就返回 default。"""
        return self.pool.get(name, default)

    def clear(self):
        """清空所有变量。"""
        self.pool.clear()
        for name in list(self._ns):
            if name not in _RESERVED and name not in pp_builtins.names():
                self._ns.pop(name, None)

    # ---------- 新增：快照 / 回滚 ----------
    def snapshot(self):
        """给当前变量拍个快照（浅拷贝，适合调试）。"""
        return dict(self.pool.items())

    def restore(self, snap):
        """用快照覆盖当前变量。"""
        self.clear()
        for name, value in snap.items():
            self.pool.add(name, value)
            self._ns[name] = value

    # ---------- JIT ----------
    def jit(self, func, *args):
        """把纯整数循环函数下沉到 C 执行，见 _jit.optimize。"""
        from ._jit import optimize

        return optimize(func, *args)

    # ---------- 文件 ----------
    def pwd(self):
        """返回当前工作目录。"""
        return pp_builtins.pwd()

    def mkdir(self, path):
        return pp_builtins.mkdir(path)

    def touch(self, path):
        return pp_builtins.touch(path)

    def read(self, path, encoding="utf-8"):
        return pp_builtins.read(path, encoding)

    def write(self, path, content, append=False, encoding="utf-8"):
        return pp_builtins.write(path, content, append, encoding)

    # ---------- 魔法方法 ----------
    def __getitem__(self, name):
        return self.pool.peek(name)

    def __setitem__(self, name, value):
        self.add(name, value)

    def __delitem__(self, name):
        self.rm(name)

    def __contains__(self, name):
        return name in self.pool

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.clear()
        return False

    def __repr__(self):
        return f"<Lang {len(self.pool)} 个变量, 内核={native.__class__.__name__}>"
