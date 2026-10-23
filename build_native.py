# -*- coding: utf-8 -*-
"""Python++ 的 C 内核构建脚本。

用法（不需要 Visual Studio / CMake，只要一个能联网的 Python）：

    python build_native.py                       # 编译到当前目录（本机平台）
    python build_native.py --check               # 只检查现有产物是否可用
    python build_native.py --fetch-zig           # 没有编译器就自动装一个 zig
    python build_native.py --target x86_64-linux-gnu.2.17   # 用 zig 交叉编译 Linux
    python build_native.py --target aarch64-macos          # 用 zig 交叉编译 macOS arm64

它会自动找一个 C 编译器，优先级如下：
    1. 环境变量 PP_CC 指定的命令
    2. zig cc（推荐：跨平台、无需安装 SDK，还能交叉编译）
    3. gcc / clang / cl
    4. MSVC 的 vcvarsall

找不到编译器时**不会报错退出**：纯 Python 回退实现照样能跑，只是慢一些。

产物命名（稳定 ABI，Py_LIMITED_API=3.8，3.8+ 通用）：
    Windows  -> pythonpp/_pythonpp_native.pyd
    Linux    -> pythonpp/_pythonpp_native.abi3.so
    macOS    -> pythonpp/_pythonpp_native.abi3.so
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE / "pythonpp"
SOURCE = PACKAGE / "pythonpp_native.c"
# 稳定 ABI：同一个 .pyd/.so 可以给 3.8+ 的所有 CPython 用
LIMITED_API = "0x030800"

ZIG_VERSION = "0.15.2"

# 平台 -> 系统名的统一映射
_SYSTEM_ALIASES = {
    "win32": "windows",
    "cygwin": "windows",
    "darwin": "macos",
    "linux": "linux",
}


def _log(message):
    """打印日志。

    注意：Windows 的 CI 环境（以及部分精简系统）控制台编码是 cp1252，
    直接 print 中文会抛 UnicodeEncodeError 把构建整个搞挂。
    这里做了兜底：编码不了就退化成转义输出，绝不让日志拖垮构建。
    """
    text = f"[build_native] {message}"
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        safe = text.encode(encoding, "backslashreplace").decode(encoding, "replace")
        print(safe, flush=True)


def _run(cmd, **kwargs):
    _log(" ".join(str(part) for part in cmd))
    return subprocess.run(cmd, check=False, **kwargs)


def _target_info(target):
    """把 --target 字符串解析成 (system, arch)。

    支持：native / x86_64-linux-gnu[.2.17] / aarch64-linux-gnu /
    x86_64-macos / aarch64-macos / x86_64-windows-gnu ...
    """
    if not target or target == "native":
        sysname = _SYSTEM_ALIASES.get(sys.platform, sys.platform)
        machine = platform.machine().lower()
        arch = {
            "amd64": "x86_64",
            "x86_64": "x86_64",
            "arm64": "aarch64",
            "aarch64": "aarch64",
        }.get(machine, machine)
        return sysname, arch
    t = target.lower()
    if "linux" in t:
        system = "linux"
    elif "macos" in t or "darwin" in t:
        system = "macos"
    elif "windows" in t or "win32" in t:
        system = "windows"
    else:
        system = "unknown"
    arch = "aarch64" if ("aarch64" in t or "arm64" in t) else "x86_64"
    return system, arch


def _native_name_for(system):
    """按目标平台返回正确的原生模块文件名（稳定 ABI 命名）。"""
    return "_pythonpp_native.pyd" if system == "windows" else "_pythonpp_native.abi3.so"


def _find_zig():
    """找一个可用的 zig 可执行文件。"""
    env = os.environ.get("PP_ZIG")
    if env and Path(env).exists():
        return env
    found = shutil.which("zig")
    if found:
        return found
    # 之前用 build_native.py --fetch-zig 下好的 / pip --target 装的
    for candidate in (
        HERE / ".zig" / "ziglang" / "zig.exe",
        HERE / ".zig" / "ziglang" / "zig",
        HERE / ".zig" / "pip" / "ziglang" / "zig.exe",
        HERE / ".zig" / "pip" / "ziglang" / "zig",
    ):
        if candidate.exists():
            return str(candidate)
    # 装在 site-packages 里的 ziglang 包，二进制在包目录下
    try:
        import ziglang

        for name in ("zig.exe", "zig"):
            exe = Path(ziglang.__file__).parent / name
            if exe.exists():
                return str(exe)
    except Exception:  # pragma: no cover - ziglang 未安装
        pass
    return None


def _zig_cache_env(env):
    """把 zig 的缓存目录指到可写位置，避免被系统目录的权限卡住。"""
    cache = HERE / ".zig-cache"
    cache.mkdir(parents=True, exist_ok=True)
    env.setdefault("ZIG_GLOBAL_CACHE_DIR", str(cache / "global"))
    env.setdefault("ZIG_LOCAL_CACHE_DIR", str(cache / "local"))
    return env


def _python_dev_paths(system):
    """拿到头文件（和 Windows 需要的导入库）。

    注意：稳定 ABI 的扩展在 Linux/macOS 上**不需要**链 libpython——
    Py* 符号在运行期由解释器解析。只有 Windows 才需要 python3.lib 导入库。
    """
    # 交叉编译时允许显式指定目标平台的 Python 头文件目录
    override = os.environ.get("PP_PY_INCLUDE", "").strip()
    if override and Path(override, "Python.h").exists():
        return override, None

    include = None
    libdir = None
    try:
        import sysconfig

        include = sysconfig.get_paths().get("include")
        libdir = sysconfig.get_config_var("LIBDIR")
    except Exception:  # pragma: no cover - 极老的 Python
        include = None

    root = Path(sys.base_prefix)
    candidates_include = [include, str(root / "Include"), str(root / "include")]
    candidates_lib = [
        libdir,
        str(root / "libs"),
        str(root / "lib"),
        str(Path(sys.executable).parent / "libs"),
    ]
    include = next(
        (c for c in candidates_include if c and Path(c, "Python.h").exists()), None
    )
    lib = None
    if system == "windows":
        for c in candidates_lib:
            if c and Path(c, "python3.lib").exists():
                lib = str(Path(c, "python3.lib"))
                break
    return include, lib


def compile_with_zig(zig, target=None, output=None):
    output = Path(output) if output else None
    system, _arch = _target_info(target)
    include, lib = _python_dev_paths(system)
    if not include:
        _log("找不到 Python 头文件（Python.h），无法编译")
        return False
    cmd = [
        zig,
        "cc",
        "-shared",
        "-O2",
        "-fno-sanitize=undefined",
        "-Wall",
        "-Wextra",
        "-Wno-unused-parameter",
        f"-DPy_LIMITED_API={LIMITED_API}",
    ]
    if target and target != "native":
        cmd += ["-target", target]
    if system == "macos":
        # 稳定 ABI 扩展的 Py* 符号运行期由解释器解析，链接期放行未定义符号
        cmd += ["-Wl,-undefined,dynamic_lookup"]
        # 尽量压低最低部署版本，让 wheel 标签与二进制真正兼容
        min_ver = os.environ.get("MACOSX_DEPLOYMENT_TARGET", "").strip()
        if min_ver:
            cmd += [f"-mmacosx-version-min={min_ver}"]
    cmd += ["-I", include, "-o", str(output), str(SOURCE)]
    if system == "windows" and lib:
        cmd.append(lib)
    env = _zig_cache_env(dict(os.environ))
    result = _run(cmd, env=env)
    return result.returncode == 0 and Path(output).exists()


def compile_with_cc(cc, output=None):
    output = Path(output) if output else None
    system, _arch = _target_info("native")
    include, lib = _python_dev_paths(system)
    if not include:
        _log("找不到 Python 头文件（Python.h），无法编译")
        return False
    is_cl = Path(cc).name.lower() in {"cl", "cl.exe"}
    cmd = [cc]
    if is_cl:
        cmd += ["/nologo", "/LD", "/O2", "/W3", f"/DPy_LIMITED_API={LIMITED_API}",
                f"/I{include}", str(SOURCE), "/link", f"/OUT:{output}"]
        if lib:
            cmd.append(lib)
    else:
        cmd += ["-shared", "-O2", "-Wall", f"-DPy_LIMITED_API={LIMITED_API}",
                "-I", include, "-o", str(output), str(SOURCE)]
        if lib:
            cmd.append(lib)
    result = _run(cmd)
    return result.returncode == 0 and Path(output).exists()


def build(target=None):
    if not SOURCE.exists():
        _log(f"缺少 C 源码：{SOURCE}")
        return False

    system, arch = _target_info(target)
    output = PACKAGE / _native_name_for(system)
    cross = bool(target and target != "native")

    zig = _find_zig()
    if zig:
        where = f"（目标 {target}，产出 {output.name}）" if cross else ""
        _log(f"使用 zig cc：{zig}{where}")
        if compile_with_zig(zig, target, output):
            return _verify(output, importable=not cross)

    if cross:
        _log("交叉编译需要 zig（pip install ziglang，或 python build_native.py --fetch-zig）")
        return False

    for name in ("gcc", "clang", "cl"):
        cc = shutil.which(name)
        if cc:
            _log(f"使用 {name}：{cc}")
            if compile_with_cc(cc, output):
                return _verify(output)

    _log("没有找到 C 编译器。")
    _log("装 zig 最简单：pip install ziglang，或运行 python build_native.py --fetch-zig")
    _log("没有 C 内核也能跑，Python++ 会自动使用纯 Python 回退实现（只是慢一些）。")
    return False


def _verify(output, importable=True):
    if not Path(output).exists():
        _log("编译命令成功但没生成产物")
        return False
    if not importable:
        # 交叉编译出的 .so 无法在当前平台 import，只校验文件是否非空
        size = Path(output).stat().st_size
        _log(f"交叉编译完成：{output.name}（{size} 字节，目标平台加载用，本机不 import）")
        return True
    sys.path.insert(0, str(HERE))
    try:
        for mod in ("_pythonpp_native", "pythonpp._pythonpp_native", "pythonpp"):
            sys.modules.pop(mod, None)
        from pythonpp import _pythonpp_native as native

        info = native.info()
        _log(f"验证通过：{info['name']} {info['version']}（{Path(output).stat().st_size} 字节）")
        _log("别忘了跑一遍测试：python test_pythonpp.py")
        return True
    except Exception as exc:  # pragma: no cover
        _log(f"编译产物无法导入：{exc!r}")
        return False


def fetch_zig():
    """没有编译器时，自动装一个 zig 到 .zig/pip 下（跨平台，用 pip 安装）。"""
    if _find_zig():
        _log("已经能找到 zig，无需下载")
        return True
    dest = HERE / ".zig" / "pip"
    dest.mkdir(parents=True, exist_ok=True)
    _log(f"用 pip 安装 ziglang 到 {dest} ...")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--target", str(dest), "ziglang"],
            check=False,
        )
    except Exception as exc:  # pragma: no cover
        _log(f"pip 安装失败：{exc!r}")
        return False
    if result.returncode != 0:
        _log("pip 安装 ziglang 失败，可手动执行：pip install ziglang")
        return False
    _log("zig 已就绪")
    return _find_zig() is not None


def main(argv=None):
    parser = argparse.ArgumentParser(description="编译 Python++ 的 C 内核")
    parser.add_argument("--check", action="store_true", help="只检查现有产物")
    parser.add_argument("--fetch-zig", action="store_true", help="下载 zig 编译器")
    parser.add_argument("--clean", action="store_true", help="删除编译产物")
    parser.add_argument(
        "--target", default=None,
        help="交叉编译目标，例如 x86_64-linux-gnu.2.17、aarch64-macos（默认 native）",
    )
    args = parser.parse_args(argv)

    if args.clean:
        for path in PACKAGE.glob("_pythonpp_native.*"):
            path.unlink(missing_ok=True)
        for path in (PACKAGE / "_pythonpp_native.lib", PACKAGE / "_pythonpp_native.pdb"):
            path.unlink(missing_ok=True)
        _log("已清理")
        return 0
    if args.fetch_zig:
        return 0 if fetch_zig() and build(args.target) else 1
    if args.check:
        system, _arch = _target_info(args.target)
        return 0 if _verify(PACKAGE / _native_name_for(system),
                            importable=(not args.target or args.target == "native")) else 1
    return 0 if build(args.target) else 1


if __name__ == "__main__":
    raise SystemExit(main())
