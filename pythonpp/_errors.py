# -*- coding: utf-8 -*-
"""把 Python 的英文报错翻成中文，并给出人话建议。

设计原则：
    * 只做展示层的翻译，不改真实异常对象 —— 想拿到原始信息随时可以。
    * 认不出来的错误原样保留，绝不吞掉信息。
    * 常见坑（除零、未定义变量、类型不匹配、下标越界……）额外给一句建议。
"""

from __future__ import annotations

import re

__all__ = [
    "PPError",
    "PPSyntaxError",
    "PPCompileError",
    "PPRuntimeError",
    "explain_error",
    "format_exception",
]

_NAME_RE = re.compile(r"name '([^']+)' is not defined")
_ATTR_RE = re.compile(r"has no attribute '([^']+)'")
_INDEX_RE = re.compile(r"(list|tuple|string) index out of range")
_KEY_RE = re.compile(r"^'([^']+)'$")

_HINTS = {
    "ZeroDivisionError": "除数是 0。做除法前先判断一下，或者用 if b: 包一层。",
    "NameError": "变量/函数名拼错了，或者还没赋值就用了。",
    "TypeError": "两边类型不一样（比如 str + int）。可以用 str()/int() 显式转换。",
    "ValueError": "类型对了，但值不合适（比如 int('abc')）。",
    "KeyError": "字典里没有这个键。用 d.get(k, 默认值) 更安全。",
    "IndexError": "下标越界了。检查 len() 或者用切片。",
    "AttributeError": "对象没有这个属性/方法，注意大小写和导入。",
    "ImportError": "模块没装或者路径不对。试试 pip install <包名>。",
    "ModuleNotFoundError": "模块没装或者路径不对。试试 pip install <包名>。",
    "FileNotFoundError": "文件不存在。先用 exists() 探一下，或者检查工作目录。",
    "RecursionError": "递归太深。检查终止条件，或者改成循环。",
    "MemoryError": "内存不够了。MemPool 的 max_bytes 可以调小一点。",
    "OverflowError": "数值超出范围。jit 遇到这个会自动回退到大整数实现。",
    "UnboundLocalError": "函数里先用了局部变量、后赋值。把赋值提到前面。",
    "IndentationError": "缩进不一致，混用了 Tab 和空格。",
    "SyntaxError": "语法写错了，看上面箭头指的位置。",
}


class PPError(Exception):
    """Python++ 自己的异常基类。"""


class PPSyntaxError(PPError, SyntaxError):
    """用户代码有语法错误。"""


class PPCompileError(PPSyntaxError):
    """编译阶段失败（Python++ 包了一层上下文）。"""


class PPRuntimeError(PPError, RuntimeError):
    """运行阶段失败。"""


def _translate(exc: BaseException):
    """返回 (中文类型名, 中文描述, 建议)。"""
    kind = type(exc).__name__
    text = str(exc)
    hint = _HINTS.get(kind)

    if isinstance(exc, NameError):
        match = _NAME_RE.search(text)
        if match:
            name = match.group(1)
            hint_text = f"是不是拼错了？确认 {name} 在作用域里，或者先用 {name} = ... 赋值。"
            return f"名字 {name!r} 没有定义", hint_text, hint
    if isinstance(exc, AttributeError):
        match = _ATTR_RE.search(text)
        if match:
            return (
                f"对象没有属性 {match.group(1)!r}",
                "检查拼写、大小写；有些方法在 Python++ 里叫别的名字。",
                hint,
            )
    if isinstance(exc, ZeroDivisionError):
        return "除以零", "除数是 0，先判断再除。", hint
    if isinstance(exc, IndexError):
        return "下标越界", "索引超过序列长度了，注意 0 开始计数。", hint
    if isinstance(exc, KeyError):
        match = _KEY_RE.search(text)
        key = match.group(1) if match else text
        return "字典键不存在", f"字典里没有 {key!r}，可以用 .get() 给默认值。", hint
    if isinstance(exc, FileNotFoundError):
        return "文件不存在", "检查路径、当前目录，或者先用 mkdir() 建目录。", hint
    if isinstance(exc, IndentationError):
        return "缩进错误", "同一层代码缩进必须一致，别混用 Tab 和空格。", hint
    if isinstance(exc, SyntaxError):
        return "语法错误", "看箭头指的位置，通常是括号、引号或冒号没配对。", hint
    if isinstance(exc, TypeError):
        return f"类型错误：{text}", "两边的类型不一样，试试显式转换。", hint
    if isinstance(exc, ValueError):
        return f"取值错误：{text}", "类型没问题，是值本身不合适。", hint
    if isinstance(exc, OverflowError):
        return f"数值溢出：{text}", "数值太大了，Python++ 会自动回退到大整数。", hint
    if isinstance(exc, MemoryError):
        return "内存不足", "减少数据量，或调小 MemPool 的 max_bytes。", hint
    return kind, text, hint


def _location(exc: BaseException) -> str:
    tb = exc.__traceback__
    filename = "<未知>"
    lineno = 0
    while tb is not None:
        frame = tb.tb_frame
        name = frame.f_code.co_filename
        if not name.startswith("<frozen") and "pythonpp" not in name.replace("\\", "/"):
            filename = name
            lineno = tb.tb_lineno
        tb = tb.tb_next
    return f"{filename}:{lineno}" if lineno else filename


def explain_error(exc: BaseException, source: str = "", show_source: bool = True) -> str:
    """把异常整理成一段中文说明，适合直接 print 或塞进日志。"""
    kind = type(exc).__name__
    title, detail, hint = _translate(exc)
    lines = [f"{kind if kind else '错误'} —— {title}"]

    location = _location(exc)
    if location != "<未知>":
        lines.append(f"位置：{location}")

    if source and show_source:
        lineno = 0
        tb = exc.__traceback__
        while tb is not None:
            lineno = tb.tb_lineno
            tb = tb.tb_next
        if lineno:
            source_lines = source.splitlines()
            if 0 < lineno <= len(source_lines):
                bad = source_lines[lineno - 1]
                lines.append(f"源码：{bad.strip()}")
                if getattr(exc, "offset", None):
                    lines.append("      " + " " * max(0, exc.offset - 1) + "^")

    if detail and detail != title:
        lines.append(f"细节：{detail}")
    if hint:
        lines.append(f"建议：{hint}")
    return "\n".join(lines)


def format_exception(exc: BaseException, source: str = "") -> str:
    """explain_error 的别名，语义更直白。"""
    return explain_error(exc, source)
