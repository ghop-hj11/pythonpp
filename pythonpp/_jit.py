# -*- coding: utf-8 -*-
"""Python++ 的 JIT：把纯整数热点函数编译成真正的机器码。

三种后端，按可用性自动挑选：

    1. ``c``        —— 用 zig/gcc 把函数翻译成 C 再编译成动态库（最快，需要编译器）
    2. ``vm``       —— 走内置 C 虚拟机的整数指令流（有 _pythonpp_native 就能用）
    3. ``python``   —— 原生 Python（永远可用，行为基准）

不管走哪条路，**结果一定和原生 Python 一致**：
所有后端都在溢出/除零/越界时返回失败状态，由外层用原函数重算。

用法：

    from pythonpp import jit

    @jit
    def loop_sum(n):
        total = 0
        for i in range(n):
            total += i * 2
        return total

    loop_sum(2_000_000)      # 第一次会编译（约 1~2 秒），之后直接跑机器码
    loop_sum.explain()       # 看看它到底用了哪个后端
"""

from __future__ import annotations

import functools
import inspect
import os
import textwrap
import time

from . import _cjit, _native

__all__ = [
    "jit",
    "JITFunction",
    "optimize",
    "native_available",
    "benchmark",
    "backend_of",
]

# 低于这个耗时就别折腾编译了 —— 编译一次 1~2 秒，不划算
_MIN_WORK = float(os.environ.get("PYTHONPP_JIT_MIN_WORK", "20000"))
_compiler = _cjit.Compiler()


def native_available() -> bool:
    """是否有可用的 C 后端（C 编译或内置虚拟机）。"""
    return _cjit.available() or hasattr(_native.native, "loop_run")


def backend_of(func):
    """查看某个（已包装的）函数当前用的后端。"""
    if isinstance(func, JITFunction):
        return func.backend
    return "python"


def _source_text(func):
    try:
        return textwrap.dedent(inspect.getsource(func))
    except (OSError, TypeError):
        return None


class JITFunction:
    """被 @jit 包装的函数。

    行为约定（重要）：
        * 任何一步失败 -> 安静地回退到原生 Python，结果永远一致；
        * 只有确实编译成功才会走快路径；
        * ``.explain()`` 能说清楚它现在走哪条路、为什么。
    """

    def __init__(self, func, warmup=True, backend="auto"):
        if not callable(func):
            raise TypeError("jit 需要可调用对象")
        functools.update_wrapper(self, func)
        self.__wrapped__ = func
        self._func = func
        self._warmup = warmup
        self._preferred = backend
        self._compiled = None
        self._backend = "python"
        self._status = "未编译"
        self._calls = 0
        self._hits = 0
        self._native_time = 0.0
        self._python_time = 0.0
        self._disabled = False

    # ---------- 状态 ----------
    @property
    def compiled(self) -> bool:
        return self._compiled is not None

    @property
    def backend(self) -> str:
        return self._backend

    def stats(self):
        return {
            "后端": self._backend,
            "状态": self._status,
            "调用次数": self._calls,
            "C 内核命中": self._hits,
            "C 耗时(秒)": round(self._native_time, 6),
            "Python 耗时(秒)": round(self._python_time, 6),
        }

    def explain(self) -> str:
        name = getattr(self._func, "__name__", "?")
        if self._backend == "c":
            return f"{name} 已编译成机器码（C 后端），{self._status}"
        if self._backend == "vm":
            return f"{name} 走内置 C 虚拟机，{self._status}"
        return f"{name} 走原生 Python：{self._status}"

    def _work_hint(self, args):
        """粗略估计这次调用的工作量，用来决定值不值得编译。"""
        for value in args:
            if isinstance(value, int) and not isinstance(value, bool):
                return abs(value)
        return 0

    # ---------- 编译 ----------
    def _try_compile(self, args):
        if self._disabled:
            return
        source = _source_text(self._func)
        if source is None:
            self._status = "拿不到源码（交互式定义的？），保持 Python"
            self._disabled = True
            return

        # 记录 C 编译为什么没成，后面即使退到 VM 也能说清楚
        c_problem = ""
        if self._preferred in ("auto", "c") and _cjit.available():
            compiled = _compiler.compile_function(source, self._func.__name__)
            if compiled is not None:
                self._compiled = ("c", compiled)
                self._backend = "c"
                self._status = f"编译耗时约 1 秒，缓存于 {compiled.path}"
                return
            c_problem = _compiler.last_error or "未知原因"
        elif self._preferred in ("auto", "c"):
            c_problem = _cjit.explain_unavailable()

        if self._preferred in ("auto", "vm") and hasattr(_native.native, "loop_run"):
            vm = _try_vm(self._func, source)
            if vm is not None:
                self._compiled = ("vm", vm)
                self._backend = "vm"
                self._status = (
                    f"已翻译成整数指令流（C 编译未启用：{c_problem}）"
                    if c_problem else "已翻译成整数指令流"
                )
                return

        self._status = (
            f"没有可用的加速后端（C：{c_problem}）" if c_problem
            else "没有可用的加速后端"
        )
        self._disabled = True

    # ---------- 调用 ----------
    def __call__(self, *args, **kwargs):
        self._calls += 1
        if kwargs or self._disabled:
            return self._python(*args)
        if self._compiled is None:
            # 工作量太小就别编译了，编译开销比省下来的还多
            if self._warmup and self._work_hint(args) < _MIN_WORK:
                self._status = "入参太小，先用 Python 跑"
                return self._python(*args)
            started = time.perf_counter()
            self._try_compile(args)
            self._compile_cost = time.perf_counter() - started
            if self._compiled is None:
                return self._python(*args)
        return self._call_backend(args)

    def _python(self, *args):
        started = time.perf_counter()
        result = self._func(*args)
        self._python_time += time.perf_counter() - started
        return result

    def _call_backend(self, args):
        _kind, impl = self._compiled
        if len(args) != len(impl.params):
            return self._python(*args)
        for value in args:
            if isinstance(value, bool) or not isinstance(value, int):
                return self._python(*args)      # 非整数：这一次交给 Python
        try:
            started = time.perf_counter()
            result, status = impl.call(args)
            elapsed = time.perf_counter() - started
            if status != 0:
                # 溢出 / 除零 / 步数超限：这次交回 Python 重算，保证结果一致
                reason = _cjit.STOP_REASONS.get(status, "未知原因")
                self._status = f"上次运算触发「{reason}」，已交回 Python 重算"
                return self._python(*args)
            self._native_time += elapsed
            self._hits += 1
            return result
        except Exception as exc:                 # noqa: BLE001
            self._status = f"运行期异常，永久回退：{exc!r}"
            self._disabled = True
            self._compiled = None
            self._backend = "python"
            return self._python(*args)

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return functools.partial(self.__call__, instance)

    def __repr__(self):
        name = getattr(self._func, "__name__", "?")
        return f"<JITFunction {name} backend={self._backend}>"


class _VMImpl:
    """内置 C 虚拟机的适配层，让两种后端对外接口一致。"""

    __slots__ = ("code", "params")

    def __init__(self, code, params):
        self.code = code
        self.params = params

    def call(self, args):
        out = _native.native.loop_run(self.code, [int(v) for v in args])
        return (out[0] if out else None), 0


def _try_vm(func, source):
    try:
        from ._jit_vm import compile_source

        code, params, _vars, returns = compile_source(source, func.__name__)
        if returns != 1:
            return None
        return _VMImpl(code, params)
    except Exception:                            # noqa: BLE001
        return None


def jit(func=None, *, warmup=True, backend="auto"):
    """装饰器：把纯整数函数下沉到 C。

        @jit
        def f(n): ...

    也可以临时关掉预热：``@jit(warmup=False)`` 表示第一次调用就编译。
    """
    if func is None:
        def decorator(fn):
            return JITFunction(fn, warmup=warmup, backend=backend)
        return decorator
    return JITFunction(func, warmup=warmup, backend=backend)


def optimize(func, *args):
    """手动下沉并立刻跑一次：``optimize(loop_sum, 1000)``。"""
    return jit(func)(*args)


def benchmark(func, *args, repeat=5):
    """粗略对比：原生 Python vs 加速后。返回 dict。"""
    baseline = timeit_best(func, *args, repeat=repeat)
    fast_fn = func if isinstance(func, JITFunction) else jit(func)
    fast_fn(*args)                                # 触发编译
    fast = timeit_best(fast_fn, *args, repeat=repeat)
    return {
        "python_秒": baseline,
        "加速后_秒": fast,
        "倍数": round(baseline / fast, 2) if fast else float("inf"),
        "后端": backend_of(fast_fn),
    }


def timeit_best(func, *args, repeat=5):
    best = None
    for _ in range(max(1, repeat)):
        start = time.perf_counter()
        func(*args)
        cost = time.perf_counter() - start
        if best is None or cost < best:
            best = cost
    return best
