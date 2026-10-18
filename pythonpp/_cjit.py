# -*- coding: utf-8 -*-
"""把生成的 C 源码编译成动态库，并用 ctypes 调用。

这里的取舍很明确：
    * 编译开销约 1~2 秒（zig/gcc 冷启动），所以结果按源码哈希缓存到磁盘，
      同一个函数第二次运行时直接加载 .dll，不再编译；
    * 没有编译器 / 编译太慢 / 编译失败，统统安静地回退到纯 Python；
    * 用 ctypes 而不是写 Python 扩展：不需要 Python 头文件，跨版本零负担。
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from . import _codegen

__all__ = ["Compiler", "Compiled", "available", "explain_unavailable", "STOP_REASONS"]

ST_OK = 0
ST_OVERFLOW = 1
ST_DIVZERO = 2
ST_GUARD = 3
ST_NO_RETURN = 4
ST_SHIFT = 5

STOP_REASONS = {
    ST_OVERFLOW: "整数溢出（改走大整数实现）",
    ST_DIVZERO: "除以零",
    ST_GUARD: "循环步数超过安全上限",
    ST_NO_RETURN: "函数没有执行到 return",
    ST_SHIFT: "移位位数超出 int64",
}


def _cache_candidates():
    """按优先级列出所有可能可写的缓存目录。"""
    override = os.environ.get("PYTHONPP_CACHE")
    if override:
        yield Path(override)
        return
    local = os.environ.get("LOCALAPPDATA")
    if local:
        yield Path(local) / "pythonpp" / "cjit"
    yield Path(tempfile.gettempdir()) / "pythonpp" / "cjit"
    # 最后兜底：包旁边（用户目录不可写的受限环境常见）
    yield Path(__file__).resolve().parent.parent / ".jitcache"


def _cache_root() -> Path:
    """挑第一个真的能写的缓存目录。

    这个函数存在的理由很实际：受限环境（容器、沙箱、只读用户目录）里
    LOCALAPPDATA 经常不可写，如果还死盯着它，JIT 会安静地退化成慢速后端。
    所以这里挨个试，只留下能创建文件的。
    """
    for candidate in _cache_candidates():
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except OSError:
            continue
    return Path(tempfile.gettempdir()) / "pythonpp" / "cjit"


def _find_compiler():
    """返回 (命令前缀, 说明)；找不到就 (None, 原因)。"""
    env = os.environ.get("PYTHONPP_CC", "").strip()
    if env:
        return (env.split(), "PYTHONPP_CC")

    zig = os.environ.get("PP_ZIG", "").strip()
    candidates = []
    if zig and Path(zig).exists():
        candidates.append(zig)
    found = shutil.which("zig")
    if found:
        candidates.append(found)
    here = Path(__file__).resolve().parent
    candidates += [
        str(here / ".zig" / "ziglang" / "zig.exe"),
        str(here / ".zig" / "ziglang" / "zig"),
        str(here.parent / ".zig" / "ziglang" / "zig.exe"),
        str(here.parent / ".zig" / "ziglang" / "zig"),
    ]
    for path in candidates:
        if path and Path(path).exists():
            return ([path, "cc"], f"zig cc ({path})")

    for name in ("gcc", "clang"):
        exe = shutil.which(name)
        if exe:
            return ([exe], name)
    return (None, "没有找到 C 编译器（pip install ziglang 即可）")


def available() -> bool:
    """当前环境能不能做 C 级 JIT。"""
    return _find_compiler()[0] is not None


def explain_unavailable() -> str:
    command, reason = _find_compiler()
    return "可用" if command else reason


class Compiled:
    """一个已经编译好的函数。"""

    __slots__ = ("func", "name", "path", "params", "calls", "library")

    def __init__(self, func, name, path, params):
        self.func = func
        self.name = name
        self.path = path
        self.params = params
        self.calls = 0
        self.library = None

    def call(self, values):
        status = ctypes.c_int(0)
        args = [ctypes.c_longlong(int(v)) for v in values]
        result = self.func(*args, ctypes.byref(status))
        self.calls += 1
        return result, status.value


class Compiler:
    """把 C 源码编译成可加载的动态库（带磁盘缓存）。"""

    def __init__(self, cache_dir=None, keep_source=False):
        self.cache_dir = Path(cache_dir) if cache_dir else _cache_root()
        self.keep_source = keep_source
        self._lock = threading.RLock()
        self._loaded = {}
        self.last_error = ""
        self.command, self.description = _find_compiler()

    # ---- 公共接口 ----
    def compile_function(self, source, func_name):
        """把源码里的函数编译成可调用对象；失败返回 None（原因在 last_error）。"""
        if self.command is None:
            self.last_error = self.description
            return None
        try:
            c_source, params, name = _codegen.generate(source, func_name)
        except _codegen.Unsupported as exc:
            self.last_error = str(exc)
            return None
        except Exception as exc:                  # noqa: BLE001
            self.last_error = f"代码生成失败：{exc!r}"
            return None

        key = hashlib.sha256(c_source.encode("utf-8")).hexdigest()[:24]
        with self._lock:
            cached = self._loaded.get(key)
            if cached is not None:
                return cached
            path = self._build(c_source, key)
            if path is None:
                return None
            compiled = self._load(path, params, name)
            if compiled is not None:
                self._loaded[key] = compiled
            return compiled

    # ---- 内部 ----
    def _build(self, c_source, key):
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.last_error = f"建不了缓存目录：{exc!r}"
            return None
        # 一律转成绝对路径：编译器子进程的 cwd 可能不同，相对路径会解析错
        cache_dir = self.cache_dir.resolve()
        suffix = ".dll" if sys.platform == "win32" else ".so"
        library = cache_dir / f"pp_{key}{suffix}"
        if library.exists():
            return library

        source_path = cache_dir / f"pp_{key}.c"
        source_path.write_text(c_source, encoding="utf-8")
        command = list(self.command)
        is_zig = Path(command[0]).name.lower().startswith("zig")
        cmd = command + [
            "-O2", "-shared", "-fno-sanitize=undefined",
            "-o", str(library),
        ]
        if not (is_zig and sys.platform == "win32"):
            cmd.append("-fPIC")
        cmd.append(str(source_path))
        try:
            env = dict(os.environ)
            # zig/clang 默认往用户目录写缓存，受限环境里会失败；
            # 统一指到我们自己的缓存目录，而且必须用绝对路径：
            # 子进程的 cwd 是 cache_dir，相对路径会解析到别处。
            if is_zig:
                env["ZIG_GLOBAL_CACHE_DIR"] = str(cache_dir / "zig-global")
                env["ZIG_LOCAL_CACHE_DIR"] = str(cache_dir / "zig-local")
            # 编译器还要往临时目录写中间文件；受限环境里系统临时目录也可能只读，
            # 所以一并指到我们自己的缓存目录（这个目录刚刚验证过可写）。
            tmp_dir = cache_dir / "tmp"
            try:
                tmp_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                tmp_dir = cache_dir
            for key in ("TMPDIR", "TMP", "TEMP"):
                env[key] = str(tmp_dir)
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180,
                cwd=str(cache_dir), encoding="utf-8", errors="replace", env=env,
            )
        except Exception as exc:                  # noqa: BLE001
            self.last_error = f"编译没能启动：{exc!r}"
            return None
        if proc.returncode != 0 or not library.exists():
            detail = (proc.stderr or proc.stdout or "编译失败").strip()
            self.last_error = detail[:800]
            return None
        if not self.keep_source:
            source_path.unlink(missing_ok=True)
        return library

    def _load(self, path, params, name):
        try:
            library = ctypes.CDLL(str(path))
        except Exception as exc:                  # noqa: BLE001
            self.last_error = f"动态库加载失败：{exc!r}"
            return None
        entry = getattr(library, "pp_entry", None)
        if entry is None:
            self.last_error = "动态库里没有 pp_entry"
            return None
        entry.restype = ctypes.c_longlong
        entry.argtypes = [ctypes.c_longlong] * len(params) + [
            ctypes.POINTER(ctypes.c_int)
        ]
        compiled = Compiled(entry, name, path, params)
        compiled.library = library      # 保住引用，别让 CDLL 被回收
        return compiled

    def clear_cache(self):
        """删除磁盘缓存。"""
        with self._lock:
            self._loaded.clear()
            if self.cache_dir.exists():
                shutil.rmtree(self.cache_dir, ignore_errors=True)

    def __repr__(self):
        return f"<Compiler {self.description}>"
