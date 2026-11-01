# 发布 Python++ 到 GitHub / PyPI

> 1.1 起，Windows / Linux / macOS 的预编译 wheel 由 GitHub Actions 自动构建，
> 不再需要你在本机准备三套工具链。
> 1.2 起，CI 还会额外产出 3 个平台的**自由线程（无 GIL）** wheel。

## 自动发布（推荐）

仓库里已经配好 `.github/workflows/release.yml`，流程是：

1. 把改动推上去
2. 打一个 `v*` 标签
3. CI 自动在 Windows / Linux / macOS 上编译 C 内核 → 跑测试 → 打 wheel → 挂到 Release → 发布到 PyPI

```bash
git add -A
git commit -m "feat: GIL=False（原子操作 / 原生通道 / 自由线程构建）"
git push origin main

git tag v1.2.0
git push origin v1.2.0        # 这一下触发 CI
```

等几分钟，仓库的 **Releases** 页就会出现带这些附件的 v1.2.0：

| 文件 | 平台 |
|---|---|
| `pythonpp_plus-1.2.0-cp38-abi3-win_amd64.whl` | Windows x64 |
| `pythonpp_plus-1.2.0-cp38-abi3-manylinux2014_x86_64.whl` | Linux x64 |
| `pythonpp_plus-1.2.0-cp38-abi3-manylinux2014_aarch64.whl` | Linux ARM64 |
| `pythonpp_plus-1.2.0-cp38-abi3-macosx_11_0_arm64.whl` | macOS Apple Silicon |
| `pythonpp_plus-1.2.0-cp38-abi3-macosx_11_0_x86_64.whl` | macOS Intel |
| `pythonpp_plus-1.2.0-cp314-cp314t-win_amd64.whl` | Windows x64 · **3.14t 无 GIL** |
| `pythonpp_plus-1.2.0-cp314-cp314t-manylinux2014_x86_64.whl` | Linux x64 · **3.14t 无 GIL** |
| `pythonpp_plus-1.2.0-cp314-cp314t-macosx_11_0_arm64.whl` | macOS ARM64 · **3.14t 无 GIL** |
| `pythonpp_plus-1.2.0-py3-none-any.whl` | 纯 Python（无加速，兜底） |

`cp38-abi3` 表示「Python 3.8 及以上通用」，一个文件覆盖 3.8 ~ 3.14。
CI 里 Linux 用 zig 锁 glibc 2.17 产出 manylinux2014 兼容二进制，macOS 用
`-undefined dynamic_lookup` 让符号运行期解析，都不需要用户装编译器。

`cp314-cp314t` 是自由线程构建专用标签：它只能装在 3.13t / 3.14t 解释器里，
标签写错会导致 GIL 版解释器装上一个 ABI 不兼容的内核（PyObject 布局不同）。

CI 里还有两个额外的质量门：

- `test-compat`：Windows / Linux / macOS × Python 3.9 / 3.12 / 3.14 矩阵，
  每个组合都「编译稳定 ABI 内核 + 跑 144 项测试 + 跑纯 Python 回退测试」；
- `build-free-threaded`：在 `python-version: "3.14t"` 上编译并测试无 GIL 内核，
  再用 `sys._is_gil_enabled() == False` 自检解释器确实没有 GIL。

也可以在仓库的 **Actions** 页手动点「Run workflow」，不必打标签。

## 本机手动打包（单个平台）

只想在你自己这台机器上打一个 wheel：

```bash
pip install setuptools wheel ziglang
python build_native.py                     # 编译 C 内核（自动找 zig/gcc/cl）
python test_pythonpp.py                    # 先跑一遍测试
python setup.py bdist_wheel                # 打 wheel（get_tag() 自动生成 cp38-abi3）
```

产物在 `dist/` 下。

自由线程版要用**自由线程解释器**来跑同一套命令：

```bash
python3.14t build_native.py                # 会自动识别并加 -DPy_GIL_DISABLED=1
python3.14t test_pythonpp.py
python3.14t setup.py bdist_wheel           # 产出 cp314-cp314t-<平台>.whl
```

`setup.py` 会按文件名自动区分两套产物（`_pythonpp_native.pyd` vs
`_pythonpp_native.cp314t-win_amd64.pyd`），不会把错误 ABI 的二进制塞进 wheel。

## 交叉编译（进阶）

`build_native.py --target` 支持用 zig 交叉编译其它平台：

```bash
python build_native.py --target x86_64-linux-gnu.2.17   # Linux x64（manylinux2014）
python build_native.py --target aarch64-linux-gnu.2.17  # Linux ARM64
python build_native.py --target x86_64-macos            # macOS Intel
python build_native.py --target aarch64-macos           # macOS Apple Silicon
```

注意：交叉编译需要目标平台的 CPython 头文件（`pyconfig.h` 是平台相关的），
用环境变量 `PP_PY_INCLUDE` 指定，否则会报 `io.h not found` 之类的错。
打 wheel 时若平台标签不对（例如在 x86_64 上编 aarch64），用
`PYTHONPP_PLAT_TAG=manylinux2014_aarch64` 覆盖。

自由线程构建只允许**同系统同架构**的交叉编译（例如 x86_64 Linux 上锁 glibc 2.17）：
它的产物必须按当前解释器的 `EXT_SUFFIX` 命名，跨架构会写出错误的文件名。
**生产发布建议直接用上面的 CI 流程**，它在原生 runner 上跑，省心且正确。

## 上 PyPI

CI 里的 `publish-pypi` job 走 OpenID Connect（Trusted Publishing）：
在仓库 Settings 里按 `pypi` 这个 environment 名配好 Pending publisher 即可，
无需保存任何 token。PyPI 上包名是 `pythonpp-plus`（`pythonpp` 被占用了）。
