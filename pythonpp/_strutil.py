# -*- coding: utf-8 -*-
"""字符串小工具：写脚本时高频使用的几个。"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "chunk",
    "dedent",
    "is_palindrome",
    "slugify",
    "snake_case",
    "truncate",
    "strip_ansi",
]

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD_RE = re.compile(r"[^a-zA-Z0-9]+")


def chunk(iterable, size: int):
    """把序列按 size 切成若干块（返回 list）。size <= 0 抛 ValueError。"""
    size = int(size)
    if size <= 0:
        raise ValueError("chunk() 的块大小必须为正数")
    data = list(iterable)
    return [data[i:i + size] for i in range(0, len(data), size)]


def dedent(text: str, strip_blank: bool = True) -> str:
    """去掉公共缩进；strip_blank=True 时同时去掉首尾空行。"""
    lines = str(text).splitlines()
    if strip_blank:
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
    widths = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    cut = min(widths) if widths else 0
    return "\n".join(line[cut:] if line.strip() else "" for line in lines)


def is_palindrome(text) -> bool:
    """忽略大小写、空格和标点，判断是否回文。"""
    cleaned = [ch for ch in str(text).lower() if ch.isalnum()]
    return cleaned == cleaned[::-1]


def slugify(text, sep: str = "-") -> str:
    """把任意文本变成 URL 友好的 slug（保留中文）。"""
    normalized = unicodedata.normalize("NFKD", str(text))
    kept = []
    for ch in normalized:
        if ch.isascii() and ch.isalnum():
            kept.append(ch.lower())
        elif ch.isalnum():
            kept.append(ch)
        elif " " in ch or ch in "-_":
            kept.append(sep)
    out = _NON_WORD_RE.sub(sep, "".join(kept)).strip(sep)
    while sep * 2 in out:
        out = out.replace(sep * 2, sep)
    return out


def snake_case(text) -> str:
    """camelCase / PascalCase / kebab-case -> snake_case。"""
    s = _CAMEL_RE.sub("_", str(text))
    s = _NON_WORD_RE.sub("_", s)
    return s.strip("_").lower()


def truncate(text, limit: int = 40, ellipsis: str = "…") -> str:
    """按字符数截断，超长时补省略号。"""
    text = str(text)
    limit = int(limit)
    if limit < 0:
        raise ValueError("truncate() 的 limit 不能为负")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(ellipsis))] + ellipsis


def strip_ansi(text) -> str:
    """去掉终端颜色控制字符。"""
    return _ANSI_RE.sub("", str(text))
