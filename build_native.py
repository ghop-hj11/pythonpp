# -*- coding: utf-8 -*-
"""Python++ 的 C 内核构建脚本。

用法（不需要 Visual Studio / CMake，只要一个能联网的 Python）：

    python build_native.py            # 编译到当前目录
    python build_native.py --check    # 只检查现有 .pyd 是否可用

它会自动找一个 C 编译器，优先级如下：
    1. 环境变量 PP_CC 指定的命令
    2. zig cc（推荐：跨平台、无需安装 SDK）
    3. gcc / clang / cl
    4. MSVC 的 vcvarsall

找不到编译器时**不会报错退出**：纯 Python 回退实现照样能跑，只是慢一些。
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
TARGET = PACKAGE / "_pythonpp_native.pyd"
# 稳定 ABI：同一个 .pyd 可以给 3.8+ 的所有 CPython 用
LIMITED_API = "0x030800"

ZIG_VERSION = "0.15.2"


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


def _find_zig():
    """找一个可用的 zig 可执行文件。"""
    env = os.environ.get("PP_ZIG")
    if env and Path(env).exists():
        return env
    found = shutil.which("zig")
    if found:
        return found
    # 之前用 build_native.py --fetch-zig 下好的
    for candidate in (
        HERE / ".zig" / "ziglang" / "zig.exe",
        HERE / ".zig" / "ziglang" / "zig",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def _zig_cache_env(env):
    """把 zig 的缓存目录指到可写位置，避免被系统目录的权限卡住。"""
    cache = HERE / ".zig-cache"
    cache.mkdir(parents=True, exist_ok=True)
    env.setdefault("ZIG_GLOBAL_CACHE_DIR", str(cache / "global"))
    env.setdefault("ZIG_LOCAL_CACHE_DIR", str(cache / "local"))
    return env


def _python_dev_paths():
    """拿到头文件和导入库的位置（venv / embeddable 发行版也能用）。"""
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
    if platform.system() == "Windows":
        for c in candidates_lib:
            if c and Path(c, "python3.lib").exists():
                lib = str(Path(c, "python3.lib"))
                break
    else:
        for c in candidates_lib:
            for name in ("libpython3.so", f"libpython{sys.version_info.major}.{sys.version_info.minor}.so"):
                if c and Path(c, name).exists():
                    lib = str(Path(c, name))
                    break
            if lib:
                break
    return include, lib


def _suffix():
    try:
        import sysconfig

        return sysconfig.get_config_var("EXT_SUFFIX") or ".pyd"
    except Exception:  # pragma: no cover
        return ".pyd"


def compile_with_zig(zig, output=TARGET):
    include, lib = _python_dev_paths()
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
        "-I",
        include,
        "-o",
        str(output),
        str(SOURCE),
    ]
    if lib:
        cmd.append(lib)
    env = _zig_cache_env(dict(os.environ))
    result = _run(cmd, env=env)
    return result.returncode == 0 and Path(output).exists()


def compile_with_cc(cc, output=TARGET):
    include, lib = _python_dev_paths()
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


def build(force_zig=False):
    if not SOURCE.exists():
        _log(f"缺少 C 源码：{SOURCE}")
        return False

    zig = _find_zig()
    if zig and (force_zig or True):
        _log(f"使用 zig cc：{zig}")
        if compile_with_zig(zig):
            return _verify()

    for name in ("gcc", "clang", "cl"):
        cc = shutil.which(name)
        if cc:
            _log(f"使用 {name}：{cc}")
            if compile_with_cc(cc):
                return _verify()

    _log("没有找到 C 编译器。")
    _log("装 zig 最简单：pip install ziglang，或运行 python build_native.py --fetch-zig")
    _log("没有 C 内核也能跑，Python++ 会自动使用纯 Python 回退实现（只是慢一些）。")
    return False


def _verify():
    if not TARGET.exists():
        _log("编译命令成功但没生成 .pyd")
        return False
    sys.path.insert(0, str(HERE))
    try:
        for mod in ("_pythonpp_native", "pythonpp._pythonpp_native", "pythonpp"):
            sys.modules.pop(mod, None)
        from pythonpp import _pythonpp_native as native

        info = native.info()
        _log(f"验证通过：{info['name']} {info['version']}（{TARGET.stat().st_size} 字节）")
        _log("别忘了跑一遍测试：python test_pythonpp.py")
        return True
    except Exception as exc:  # pragma: no cover
        _log(f"编译产物无法导入：{exc!r}")
        return False


def _wheel_url():
    """pypi 上 ziglang 的 Windows wheel 地址（按 simple index 查最新的）。"""
    import json
    import urllib.request

    for base in ("https://pypi.org", "https://mirrors.aliyun.com"):
        try:
            path = f"/pypi/ziglang/{ZIG_VERSION}/json" if base == "https://pypi.org" \
                else f"/pypi/web/json/ziglang/{ZIG_VERSION}"
            with urllib.request.urlopen(f"{base}{path}", timeout=30) as resp:
                data = json.load(resp)
            urls = data.get("urls") or data.get("releases", {}).get(ZIG_VERSION, [])
            for item in urls:
                if "win_amd64" in item["filename"]:
                    return item["url"]
        except Exception:
            continue
    raise RuntimeError("找不到 ziglang 的 Windows wheel")


def fetch_zig():
    """没有编译器时，自动下载一个 zig（约 90MB）到 .zig/ 下。"""
    if _find_zig():
        _log("已经能找到 zig，无需下载")
        return True
    if platform.system() != "Windows" or platform.machine().lower() not in {
        "amd64",
        "x86_64",
    }:
        _log("自动下载只处理 Windows x64；其它平台请用 pip install ziglang")
        return False
    import urllib.request
    import zipfile

    dest = HERE / ".zig"
    dest.mkdir(parents=True, exist_ok=True)
    try:
        url = _wheel_url()
    except Exception as exc:
        _log(f"查不到下载地址：{exc!r}")
        return False
    wheel = dest / f"ziglang-{ZIG_VERSION}-win_amd64.whl"
    _log(f"下载 {url}")
    try:
        with urllib.request.urlopen(url, timeout=180) as resp, wheel.open("wb") as fp:
            shutil.copyfileobj(resp, fp)
    except Exception as exc:
        _log(f"下载失败：{exc!r}")
        return False
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(dest)
    wheel.unlink(missing_ok=True)
    _log("zig 已就绪")
    return _find_zig() is not None


def main(argv=None):
    parser = argparse.ArgumentParser(description="编译 Python++ 的 C 内核")
    parser.add_argument("--check", action="store_true", help="只检查现有产物")
    parser.add_argument("--fetch-zig", action="store_true", help="下载 zig 编译器")
    parser.add_argument("--clean", action="store_true", help="删除编译产物")
    args = parser.parse_args(argv)

    if args.clean:
        for path in (TARGET, PACKAGE / "_pythonpp_native.lib", PACKAGE / "_pythonpp_native.pdb"):
            path.unlink(missing_ok=True)
        _log("已清理")
        return 0
    if args.fetch_zig:
        return 0 if fetch_zig() and build() else 1
    if args.check:
        return 0 if _verify() else 1
    return 0 if build() else 1


if __name__ == "__main__":
    raise SystemExit(main())
