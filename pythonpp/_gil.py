# -*- coding: utf-8 -*-
"""GIL 配置与无锁并发原语（GIL=False 的 Python 侧入口）。

先说清楚**能做到什么、不能做到什么**，免得被名字骗了：

* 标准 CPython（官方安装包）的 GIL 是编译进去的，运行期无法删除。
  所以 ``configure(gil=False)`` 在这类解释器上做的是：
      - JIT 的整数循环在 C 里执行期间 ``PyEval_SaveThread()``，
        不占着 GIL，别的线程可以真并行推进；
      - 随机数改成线程局部状态，不再有全局数据竞争；
      - 需要共享/传递数据时，走本模块提供的原子量与通道。

* 自由线程（experimental free-threading，``python3.13t`` / ``python3.14t``）
  解释器里，Python 代码本身就能多核并行。装上 Python++ 的自由线程内核后，
  通过 ``Py_MOD_GIL_NOT_USED`` 声明"这个模块不需要 GIL"，不会在 import
  时把 GIL 又打开。

* 真正的"无锁"落在三处：
      1. ``AtomicInt``  —— C11 原子指令 / MSVC ``_Interlocked*``，
         带内存序（memory order），修掉数据竞争与指令重排；
      2. ``Channel``    —— 线程之间只传对象、不共享可变状态（Go channel 思路），
         阻塞等待时释放 GIL；
      3. JIT 的 VM 循环 —— 执行期释放 GIL，见 :func:`pythonpp.jit`。

用法：:

    import pythonpp as pp

    pp.configure(gil=False)          # 默认是 True（保持解释器原样）
    print(pp.gil_status())

    counter = pp.AtomicInt(0)
    def worker():
        for _ in range(100000):
            counter.add(1)
    pp.spawn(worker) ...
    assert counter.value == 100000

    ch = pp.Channel(8)
    ch.put("hello")
    print(ch.get())
    ch.close()

    results = pp.parallel_map(heavy_jit_function, range(8))
"""

from __future__ import annotations

import concurrent.futures
import queue as _queue
import sys
import threading
from typing import Any, Callable, Iterable, Iterator, Optional

from ._native import native as _kernel

__all__ = [
    "configure",
    "gil_enabled",
    "gil_status",
    "AtomicInt",
    "Channel",
    "ChannelClosed",
    "spawn",
    "parallel_map",
    "fence",
    "RELAXED",
    "ACQUIRE",
    "RELEASE",
    "ACQ_REL",
    "SEQ_CST",
]

# 与 C 内核里的 pp_mo_* 一一对应
RELAXED = 0
ACQUIRE = 1
RELEASE = 2
ACQ_REL = 3
SEQ_CST = 4

_ORDERS = {
    "relaxed": RELAXED,
    "acquire": ACQUIRE,
    "release": RELEASE,
    "acq_rel": ACQ_REL,
    "seq_cst": SEQ_CST,
    "seqcst": SEQ_CST,
}

# Python++ 自己的开关：True = 保持解释器原样（默认），False = GIL=False 模式
_configured_gil = True

# 原生内核是否具备无锁原语（纯 Python 回退时没有）
_HAS_ATOMICS = hasattr(_kernel, "atomic_new")
_HAS_CHANNELS = hasattr(_kernel, "channel_new")


def _order(value):
    if isinstance(value, int):
        return max(RELAXED, min(SEQ_CST, value))
    try:
        return _ORDERS[str(value).lower()]
    except KeyError:
        raise ValueError(f"未知的内存序：{value!r}（可选 relaxed/acquire/release/acq_rel/seq_cst）")


# ---------------------------------------------------------------------------
def _interpreter_gil_state():
    """返回 (是否有 GIL, 是否自由线程构建)。"""
    free_threaded = False
    try:
        free_threaded = bool(sysconfig_gil_disabled())
    except Exception:  # pragma: no cover
        free_threaded = False
    try:
        if hasattr(sys, "_is_gil_enabled"):
            return bool(sys._is_gil_enabled()), free_threaded
        return True, free_threaded
    except Exception:  # pragma: no cover
        return True, free_threaded


def sysconfig_gil_disabled():
    """解释器是不是 --disable-gil 构建（不依赖 sys._is_gil_enabled 的兜底）。"""
    import sysconfig

    value = sysconfig.get_config_var("Py_GIL_DISABLED")
    return bool(value)


def configure(gil: bool = True) -> bool:
    """配置 GIL 策略，返回**旧值**。

    ``gil=True``（默认）表示不做任何改变，行为与普通 Python 一致。
    ``gil=False`` 表示进入 GIL=False 模式：
        * 原生内核在 JIT 循环执行期间释放 GIL；
        * 通道在阻塞等待时释放 GIL；
        * 自由线程解释器上直接就是多核并行。

    注意：在标准 CPython 上这只是"更大程度地让出 GIL"，并不能删除它。
    想真正去掉 GIL，请用自由线程解释器（python3.14t）并安装对应的 wheel。
    """
    global _configured_gil
    previous = _configured_gil
    _configured_gil = bool(gil)
    if hasattr(_kernel, "set_option"):
        try:
            _kernel.set_option("release_gil", not _configured_gil)
        except Exception:  # pragma: no cover - 老内核没有这个开关
            pass
    return previous


def gil_enabled() -> bool:
    """Python++ 视角下 GIL 是否开启（默认 True）。"""
    return _configured_gil


def gil_status() -> dict:
    """返回一份可直接打印的 GIL 状态报告。"""
    interpreter_has_gil, free_threaded = _interpreter_gil_state()
    info = {}
    try:
        info = _kernel.info() or {}
    except Exception:  # pragma: no cover
        info = {}
    native_ok = bool(info) and not info.get("fallback")
    native_free = bool(info.get("free_threaded"))
    release = bool(info.get("gil_release"))

    if not interpreter_has_gil:
        parallelism = "full"        # 解释器本身就是自由线程
    elif release:
        parallelism = "native-only" # 只有 C 代码（JIT/通道）能真并行
    else:
        parallelism = "none"

    return {
        "configured_gil": _configured_gil,
        "gil_disabled_by_config": not _configured_gil,
        "interpreter_gil": interpreter_has_gil,
        "interpreter_free_threaded": free_threaded,
        "native": native_ok,
        "native_free_threaded": native_free,
        "atomics": _HAS_ATOMICS,
        "channels": _HAS_CHANNELS,
        "release_gil": release,
        "parallelism": parallelism,
    }


def describe() -> str:
    """一句中文说明，给 CLI / --info 用。"""
    status = gil_status()
    if status["interpreter_free_threaded"] and status["native_free_threaded"]:
        return "GIL 已彻底关闭（自由线程解释器 + 自由线程内核）"
    if status["interpreter_free_threaded"]:
        return "解释器是自由线程构建，但当前内核不是，import 时 GIL 可能被重新打开"
    if status["release_gil"]:
        return "标准 CPython：JIT 与通道执行期让出 GIL（Python 字节码仍受 GIL 限制）"
    return "GIL 保持开启（默认；configure(gil=False) 可切换）"


# ---------------------------------------------------------------------------
class AtomicInt:
    """64 位原子整数。

    底层是 C 的原子指令，所以多线程 ``add()`` 不会丢更新——
    这正是"两个线程同时改一个变量"那个经典数据竞争的根治办法。
    内存序参数用来挡住 CPU / 编译器的指令重排（默认 seq_cst 最安全）。
    """

    __slots__ = ("_cell", "_lock", "_value", "_native")

    def __init__(self, value: int = 0):
        value = int(value)
        if _HAS_ATOMICS:
            self._cell = _kernel.atomic_new(value)
            self._native = True
            self._lock = None
        else:                       # 纯 Python 回退：用互斥量保证正确性
            self._cell = None
            self._native = False
            self._lock = threading.Lock()
            self._value = value

    # ---- 读写 ----
    @property
    def value(self) -> int:
        return self.load()

    @value.setter
    def value(self, new_value: int) -> None:
        self.store(new_value)

    def load(self, order: Any = SEQ_CST) -> int:
        order = _order(order)       # 回退实现也做参数校验，行为保持一致
        if self._native:
            return _kernel.atomic_load(self._cell, order)
        with self._lock:
            return self._value

    def store(self, value: int, order: Any = SEQ_CST) -> None:
        value = int(value)
        order = _order(order)
        if self._native:
            _kernel.atomic_store(self._cell, value, order)
            return
        with self._lock:
            self._value = value

    def add(self, delta: int = 1, order: Any = SEQ_CST) -> int:
        """原子加，返回**加完之后**的值。"""
        delta = int(delta)
        if self._native:
            old = _kernel.atomic_add(self._cell, delta)
            return int(old) + delta
        with self._lock:
            self._value += delta
            return self._value

    def sub(self, delta: int = 1, order: Any = SEQ_CST) -> int:
        return self.add(-int(delta), order)

    def exchange(self, value: int) -> int:
        """原子换值，返回旧值。"""
        value = int(value)
        if self._native:
            return int(_kernel.atomic_exchange(self._cell, value))
        with self._lock:
            old = self._value
            self._value = value
            return old

    def compare_set(self, expected: int, desired: int) -> tuple:
        """CAS：成功返回 ``(True, 新值)``，失败返回 ``(False, 当前值)``。"""
        expected = int(expected)
        desired = int(desired)
        if self._native:
            current, ok = _kernel.atomic_cas(self._cell, expected, desired)
            return bool(ok), (desired if ok else int(current))
        with self._lock:
            if self._value == expected:
                self._value = desired
                return True, desired
            return False, self._value

    def or_(self, mask: int) -> int:
        mask = int(mask)
        if self._native:
            return int(_kernel.atomic_or(self._cell, mask))
        with self._lock:
            old = self._value
            self._value |= mask
            return old

    def and_(self, mask: int) -> int:
        mask = int(mask)
        if self._native:
            return int(_kernel.atomic_and(self._cell, mask))
        with self._lock:
            old = self._value
            self._value &= mask
            return old

    def xor(self, mask: int) -> int:
        mask = int(mask)
        if self._native:
            return int(_kernel.atomic_xor(self._cell, mask))
        with self._lock:
            old = self._value
            self._value ^= mask
            return old

    # ---- 语法糖 ----
    def __iadd__(self, delta):
        self.add(delta)
        return self

    def __isub__(self, delta):
        self.add(-int(delta))
        return self

    def __int__(self):
        return self.load()

    def __index__(self):
        return self.load()

    def __eq__(self, other):
        return self.load() == (other.load() if isinstance(other, AtomicInt) else other)

    def __repr__(self):
        return f"AtomicInt({self.load()}, native={self._native})"


# ---------------------------------------------------------------------------
class ChannelClosed(RuntimeError):
    """通道已关闭且里面已经没有消息了。"""


class Channel:
    """Go 风格的通道：只传对象，不共享可变状态。

    ``Channel(0)`` 表示无界；``Channel(n)`` 表示最多缓存 n 条消息。
    发送/接收在必须等待时会释放 GIL，所以不会把整个解释器卡住。

    关闭之后仍可把剩余消息取完，取空后抛 :class:`ChannelClosed`。
    """

    __slots__ = ("_handle", "_native", "_queue", "_closed_flag", "_marker", "_capacity")

    def __init__(self, capacity: int = 0):
        capacity = int(capacity)
        if capacity < 0:
            raise ValueError("通道容量不能为负数（0 表示无界）")
        self._capacity = capacity
        if _HAS_CHANNELS:
            self._handle = _kernel.channel_new(capacity)
            self._native = True
            self._marker = _kernel.channel_marker()
            self._queue = None
            self._closed_flag = None
        else:
            self._handle = None
            self._native = False
            # queue.Queue(0) 就是无界，语义刚好对上
            self._queue = _queue.Queue(maxsize=capacity)
            self._closed_flag = threading.Event()
            self._marker = None

    # ---- 发送 / 接收 ----
    def put(self, item: Any, timeout: Optional[float] = None) -> None:
        """放入一条消息；``timeout`` 为秒，None 表示一直等。"""
        if self._native:
            _kernel.channel_put(self._handle, item, timeout)
            return
        if not self._closed_flag.is_set():
            try:
                self._queue.put(item, block=True, timeout=timeout)
                return
            except _queue.Full as exc:
                raise TimeoutError("发送超时：通道已满") from exc
        raise RuntimeError("通道已关闭，不能再发送消息")

    def get(self, timeout: Optional[float] = None) -> Any:
        """取出并返回一条消息。"""
        if self._native:
            item = _kernel.channel_get(self._handle, timeout)
            if item is self._marker:
                raise ChannelClosed("通道已关闭且没有剩余消息")
            return item
        # 关闭之后不会再有新消息：队列空了就直接结束，不能再无限期阻塞
        if self._closed_flag.is_set() and self._queue.empty():
            raise ChannelClosed("通道已关闭且没有剩余消息")
        try:
            item = self._queue.get(block=True, timeout=timeout)
        except _queue.Empty as exc:
            if self._closed_flag.is_set():
                raise ChannelClosed("通道已关闭且没有剩余消息") from exc
            raise TimeoutError("接收超时：通道里暂时没有消息") from exc
        if item is self._marker:
            raise ChannelClosed("通道已关闭且没有剩余消息")
        return item

    def close(self) -> None:
        if self._native:
            _kernel.channel_close(self._handle)
            return
        self._closed_flag.set()
        with self._queue.mutex:            # 唤醒所有等待者
            self._queue.not_empty.notify_all()
            self._queue.not_full.notify_all()

    @property
    def closed(self) -> bool:
        if self._native:
            return bool(_kernel.channel_is_closed(self._handle))
        return self._closed_flag.is_set()

    def qsize(self) -> int:
        if self._native:
            return int(_kernel.channel_size(self._handle))
        return self._queue.qsize()

    # ---- 迭代：`for item in ch` 一直取到关闭 ----
    def __iter__(self) -> Iterator[Any]:
        while True:
            try:
                yield self.get()
            except ChannelClosed:
                return

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __repr__(self):
        return f"<Channel 容量={self._capacity or '无界'} 当前={self.qsize()} 关闭={self.closed}>"


# ---------------------------------------------------------------------------
def fence(order: Any = SEQ_CST) -> None:
    """内存屏障：阻止 CPU / 编译器把屏障前后的访存指令重排。

    典型用法：先写数据，``fence('release')``，再发布"就绪"标志；
    读端先看到标志，``fence('acquire')``，再读数据。这样就不会读到半成品。
    """
    if hasattr(_kernel, "atomic_fence"):
        _kernel.atomic_fence(_order(order))


def spawn(func: Callable, *args, **kwargs) -> concurrent.futures.Future:
    """在后台线程里跑 ``func(*args)``，返回 Future。

    GIL=False 时，如果 func 里是原生内核/JIT 在干活，就能与其他线程真正并行。
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pp")
    future = pool.submit(func, *args, **kwargs)
    # 结果拿到后自动回收线程，不留下悬空的池
    future.add_done_callback(lambda _f: pool.shutdown(wait=False))
    return future


def parallel_map(func: Callable, items: Iterable, workers: Optional[int] = None) -> list:
    """多线程 map，返回与输入顺序一致的结果列表。"""
    items = list(items)
    if not items:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(func, items))
