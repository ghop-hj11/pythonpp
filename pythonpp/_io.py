# -*- coding: utf-8 -*-
"""文件与目录：保持旧版函数签名，另加几个常用操作。

旧版用法完全不变：

    pwd()              -> 当前目录
    mkdir("a/b")       -> 建目录（含父目录），返回路径
    touch("a.txt")     -> 建空文件，返回路径
    read("a.txt")      -> 读文本
    write("a.txt", s)  -> 写文本

新增：

    append(path, s)        追加
    read_bytes/write_bytes 二进制
    exists(path)           是否存在
    listdir(path)          目录项
    remove(path)           删文件（不存在不报错）
    rmtree(path)           删目录树
    copy/move              复制/移动
    size(path)             文件大小
    home()                 用户主目录
    join(*parts)           拼路径
    splitext(path)         拆扩展名
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

__all__ = [
    "pwd",
    "home",
    "join",
    "mkdir",
    "touch",
    "read",
    "write",
    "append",
    "read_bytes",
    "write_bytes",
    "exists",
    "is_dir",
    "is_file",
    "listdir",
    "remove",
    "rmtree",
    "copy",
    "move",
    "size",
    "splitext",
    "lines",
]

# 默认用 UTF-8；Windows 上还顺手兼容带 BOM 的 UTF-8 文件
_DEFAULT_ENCODING = "utf-8"


def pwd() -> str:
    """返回当前工作目录。"""
    return str(Path.cwd())


def home() -> str:
    """返回用户主目录。"""
    return str(Path.home())


def join(*parts) -> str:
    """拼接路径片段，自动处理分隔符。"""
    return str(Path(*[str(p) for p in parts]))


def mkdir(path) -> str:
    """创建目录（含不存在的父目录），并返回其路径。已存在不报错。"""
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def touch(path) -> str:
    """创建空文件；文件已存在时保留内容并更新时间。"""
    target = Path(path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=True)
    return str(target)


def read(path, encoding=_DEFAULT_ENCODING) -> str:
    """读取文本文件。"""
    data = Path(path).read_bytes()
    if data.startswith(b"\xef\xbb\xbf") and encoding.lower().replace("-", "") == "utf8":
        data = data[3:]
    return data.decode(encoding)


def write(path, content, append=False, encoding=_DEFAULT_ENCODING) -> str:
    """写入文本文件；append=True 时追加到文件末尾。返回路径。"""
    target = Path(path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    mode = "ab" if append else "wb"
    data = content if isinstance(content, bytes) else str(content).encode(encoding)
    with target.open(mode) as file:
        file.write(data)
    return str(target)


def append(path, content, encoding=_DEFAULT_ENCODING) -> str:
    """追加写，等价于 write(path, content, append=True)。"""
    return write(path, content, append=True, encoding=encoding)


def read_bytes(path) -> bytes:
    """读取二进制文件。"""
    return Path(path).read_bytes()


def write_bytes(path, data) -> str:
    """写入二进制文件。"""
    target = Path(path)
    if target.parent and not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(bytes(data))
    return str(target)


def exists(path) -> bool:
    """路径是否存在。"""
    return Path(path).exists()


def is_dir(path) -> bool:
    """是否是目录。"""
    return Path(path).is_dir()


def is_file(path) -> bool:
    """是否是普通文件。"""
    return Path(path).is_file()


def listdir(path=".") -> list:
    """列出目录项，排序后返回。"""
    return sorted(os.listdir(path))


def remove(path) -> bool:
    """删除文件（不存在也不报错）。返回是否真的删掉了。"""
    try:
        Path(path).unlink()
        return True
    except FileNotFoundError:
        return False


def rmtree(path) -> bool:
    """删除整个目录树（不存在也不报错）。"""
    target = Path(path)
    if not target.exists():
        return False
    shutil.rmtree(target)
    return True


def copy(src, dst) -> str:
    """复制文件或目录，返回目标路径。"""
    return str(shutil.copy2(src, dst))


def move(src, dst) -> str:
    """移动文件或目录，返回目标路径。"""
    return str(shutil.move(src, dst))


def size(path) -> int:
    """文件字节数；传目录则递归求和。"""
    target = Path(path)
    if target.is_dir():
        return sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
    return target.stat().st_size


def splitext(path):
    """拆出 (主名, 扩展名)。"""
    return os.path.splitext(str(path))


def lines(path, encoding=_DEFAULT_ENCODING) -> list:
    """按行读取，自动去掉行尾换行符。"""
    return read(path, encoding).splitlines()
