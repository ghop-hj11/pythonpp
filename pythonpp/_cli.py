# -*- coding: utf-8 -*-
"""Python++ 的命令行工具（python -m pythonpp）。

    pp                    进入交互式 REPL
    pp script.pp          运行脚本
    pp -c "print(1+1)"    直接执行一段代码
    pp --bench            跑内置基准测试
    pp --info             看当前用的是哪个内核
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__, _cjit, _native
from ._core import Lang
from ._errors import explain_error


def _banner():
    lines = [
        f"Python++ {__version__}  ——  Python 语法 + C 加速",
        f"内核：{_native.describe()}",
        f"JIT ：{_cjit.explain_unavailable()}",
        "输入 exit() 或 Ctrl-Z 回车退出；help() 看内置函数。",
    ]
    return "\n".join(lines)


def repl():
    """交互式解释器：逐行执行，出错就地翻译成中文。"""
    print(_banner())
    lang = Lang()
    buffer = []
    while True:
        prompt = ">>> " if not buffer else "... "
        try:
            line = input(prompt)
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print("\n（已取消当前输入）")
            buffer.clear()
            continue

        if not buffer and line.strip() in ("exit()", "quit()", "exit", "quit"):
            break
        buffer.append(line)
        source = "\n".join(buffer)

        try:
            compiled = compile(source, "<stdin>", "exec")
        except SyntaxError as exc:
            # 只有"输入还不完整"时才继续等，否则就报错
            if exc.msg.endswith("unexpected EOF") or "was never closed" in exc.msg:
                continue
            buffer.clear()
            print(explain_error(exc, source))
            continue
        except (KeyboardInterrupt, SystemExit):
            break

        buffer.clear()
        try:
            # 先按表达式求值，这样 print 就省了
            value = eval(compile(source, "<stdin>", "eval"), lang.namespace)
            if value is not None:
                print(repr(value))
        except SyntaxError:
            try:
                lang.run(source)
            except BaseException as exc:      # noqa: BLE001
                print(getattr(exc, "pp_explained", None) or explain_error(exc, source))
        except BaseException as exc:          # noqa: BLE001
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                break
            print(getattr(exc, "pp_explained", None) or explain_error(exc, source))
        finally:
            try:
                lang._sync_back()
            except Exception:                 # noqa: BLE001
                pass


def run_file(path, trace=False):
    """运行一个脚本文件。"""
    with open(path, encoding="utf-8") as fp:
        source = fp.read()
    lang = Lang()
    started = time.perf_counter()
    try:
        lang.run(source, filename=path)
    except BaseException as exc:              # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        print(getattr(exc, "pp_explained", None) or explain_error(exc, source),
              file=sys.stderr)
        return 1
    if trace:
        elapsed = time.perf_counter() - started
        print(f"\n[用时 {elapsed * 1000:.1f} ms，{len(lang.pool)} 个变量]",
              file=sys.stderr)
    return 0


def run_code(source):
    lang = Lang()
    try:
        lang.run(source)
    except BaseException as exc:              # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        print(getattr(exc, "pp_explained", None) or explain_error(exc, source),
              file=sys.stderr)
        return 1
    return 0


def show_info():
    print(_banner())
    print()
    print("检查项：")
    print(f"  Python      ：{sys.version.split()[0]}")
    print(f"  原生内核    ：{_native.backend}（{_native.native.info().get('version')}）")
    print(f"  C 编译器    ：{_cjit.explain_unavailable()}")
    print(f"  JIT 缓存目录：{_cjit.Compiler().cache_dir}")
    print(f"  平台        ：{sys.platform}")
    try:
        from ._rand import random

        print(f"  随机数自检  ：{random(1, 100)}")
    except Exception as exc:                  # noqa: BLE001
        print(f"  随机数自检  ：失败 {exc!r}")


def show_help():
    from . import _builtins

    print("内置函数（在 Lang 和 REPL 里直接用）：\n")
    groups = [
        ("随机", ["random", "seed", "shuffle", "randbytes", "choice", "sample"]),
        ("文件", ["pwd", "home", "join", "mkdir", "touch", "read", "write", "append",
                 "listdir", "exists", "remove", "copy", "move", "size", "lines"]),
        ("计算", ["fast_sum", "dot", "range_sum", "mean", "median", "mode",
                 "variance", "stdev", "is_prime", "gcd", "lcm", "clamp"]),
        ("字符串", ["chunk", "dedent", "is_palindrome", "slugify", "snake_case",
                  "truncate", "strip_ansi"]),
        ("其他", ["now", "sleep", "timeit", "counter", "deep_sizeof", "jit"]),
    ]
    for title, names in groups:
        shown = [n for n in names if n in _builtins.names()]
        print(f"  {title}：{'、'.join(shown)}")
    print("\n用法：help(函数名) 看文档，例如 help(random)")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="pp", description="Python++：在 Python 语法之上做加法，用 C 提速"
    )
    parser.add_argument("script", nargs="?", help="要运行的 .py/.pp 脚本")
    parser.add_argument("-c", "--command", help="直接执行一段代码")
    parser.add_argument("--bench", action="store_true", help="跑基准测试")
    parser.add_argument("--info", action="store_true", help="显示环境信息")
    parser.add_argument("--help-builtins", action="store_true", help="列出内置函数")
    parser.add_argument("--trace", action="store_true", help="运行结束后打印用时")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.info:
        show_info()
        return 0
    if args.help_builtins:
        show_help()
        return 0
    if args.bench:
        from ._bench import main as bench_main

        return bench_main()
    if args.command:
        return run_code(args.command)
    if args.script:
        return run_file(args.script, trace=args.trace)
    repl()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
