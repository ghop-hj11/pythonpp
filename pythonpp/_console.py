# -*- coding: utf-8 -*-
"""控制台编码兜底。

为什么需要这个模块？
    Python++ 的提示、报错、日志全是中文。而在某些环境里，标准输出用的不是
    UTF-8：GitHub Actions 的 Windows 机器默认是 cp1252，部分精简版系统是
    cp437，还有一些服务器的 LANG 没设。这些情况下 `print("中文")` 会直接抛
    UnicodeEncodeError —— 一句日志就能把整个构建/测试搞挂。

处理原则：
    * 能改就改：如果 stdout/stderr 还没被写过，尽量切到 UTF-8；
    * 改不了也不能崩：退化成 backslashreplace，把中文写成 \\uXXXX 也要让程序跑完；
    * 绝不因为日志编码问题影响真正的功能。
"""

from __future__ import annotations

import sys

__all__ = ["ensure_utf8_console"]

_PATCHED = False


def _switch_stream(stream, encoding="utf-8"):
    """试着把流的编码换掉；不行就退到 backslashreplace 错误策略。"""
    if stream is None:
        return
    try:
        stream.reconfigure(encoding=encoding, errors="backslashreplace")
        return
    except (AttributeError, ValueError, OSError):
        pass
    # 有些流（如被重定向的管道）不允许 reconfigure，只能改错误策略
    try:
        stream.reconfigure(errors="backslashreplace")
    except Exception:  # noqa: BLE001 - 兜底，不能因为日志问题崩掉
        pass


def ensure_utf8_console() -> bool:
    """让控制台至少不会因为中文崩掉。返回是否做过处理。"""
    global _PATCHED
    if _PATCHED:
        return False
    _PATCHED = True

    fixed = False
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding in ("utf8", "utf8mb4"):
            continue
        _switch_stream(stream)
        fixed = True
    return fixed
