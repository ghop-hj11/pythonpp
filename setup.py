# -*- coding: utf-8 -*-
"""Python++ 的构建脚本。

项目元数据（名称、版本、许可证）在 ``pyproject.toml`` 里；
这里只做两件 setuptools 默认不管的事：

1. 把**已经编译好的** .pyd/.so 塞进 wheel；
2. 把 wheel 标签改成**平台相关**（`cp38-abi3-win_amd64` 之类）。

第 2 点很关键。setuptools 默认认为"纯 Python 包"，会打出
`py3-none-any` 的标签；可我们的 wheel 里带着 Windows 的二进制，
如果沿用 purelib 标签，Linux/macOS 用户 pip 时会以为能装，
装完才发现 .pyd 加载不了 —— 这是必须避免的坑。
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
from setuptools.command.build_py import build_py as _build_py

HERE = Path(__file__).resolve().parent
PKG = HERE / "pythonpp"


def _native_files():
    """当前目录下已经编译好的原生扩展。"""
    return sorted(list(PKG.glob("*.pyd")) + list(PKG.glob("*.so")))


def _platform_tag() -> str:
    """生成 wheel 的平台标签。交叉编译时可用 PYTHONPP_PLAT_TAG 覆盖。

    例如在 x86_64 机器上交叉编译 aarch64 的 .so 时，platform.machine()
    仍是 x86_64，必须显式指定 manylinux2014_aarch64，否则标签会写错。
    """
    override = os.environ.get("PYTHONPP_PLAT_TAG", "").strip()
    if override:
        return override
    machine = platform.machine().lower()
    system = platform.system().lower()
    if system == "windows":
        return "win_amd64" if machine in ("amd64", "x86_64") else "win_arm64"
    if system == "linux":
        return "manylinux2014_x86_64" if machine in ("x86_64", "amd64") \
            else "manylinux2014_aarch64"
    if system == "darwin":
        return "macosx_11_0_arm64" if machine == "arm64" else "macosx_10_9_x86_64"
    return "any"


class build_py(_build_py):
    """构建时把原生扩展一起带上。

    没有 .pyd/.so 时（例如在 Linux 上打 Windows 包，或仓库里没放二进制），
    安静地产出一个纯 Python 的 wheel —— 装上去照样能用，只是没加速。
    """

    def run(self):
        super().run()
        natives = _native_files()
        if not natives:
            self.announce("没有找到原生扩展，产出纯 Python 版本", level=2)
            return
        target_dir = Path(self.build_lib) / "pythonpp"
        target_dir.mkdir(parents=True, exist_ok=True)
        for src in natives:
            self.copy_file(str(src), str(target_dir / src.name))
            self.announce(f"打包原生内核：{src.name}", level=2)

    def finalize_options(self):
        super().finalize_options()
        # 只要带二进制，就必须按平台包处理
        if _native_files():
            self.plat_name = _platform_tag()


class bdist_wheel(_bdist_wheel):
    """把 wheel 标签修正成平台相关 + 稳定 ABI。"""

    def finalize_options(self):
        super().finalize_options()
        if _native_files():
            # Root-Is-Purelib = false，表示这是平台相关的包
            self.root_is_pure = False

    def get_tag(self):
        if not _native_files():
            # 纯 Python 回退版：任何平台、任何 3.8+ 都能装
            return ("py3", "none", "any")
        # 稳定 ABI：cp38-abi3-<平台>，一个文件覆盖 3.8 ~ 3.14。
        # 这里必须写死 cp38（对应 Py_LIMITED_API=0x030800），
        # 不能用当前解释器的版本号，否则 wheel 就只能装在当前版本上。
        return ("cp38", "abi3", _platform_tag())


setup(
    cmdclass={"build_py": build_py, "bdist_wheel": bdist_wheel},
    # 带二进制时按扩展模块处理（打平台包）
    has_ext_modules=lambda: bool(_native_files()),
)
