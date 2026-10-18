# -*- coding: utf-8 -*-
"""Python++ —— 在 Python 语法之上做加法。

这个包的全部对外能力都在 ``__init__`` 里导出，常用入口：

    from pythonpp import Lang
    lang = Lang()
    lang.run("x = 1 + 1")
    lang.jit(func, args...)        # 整数热点循环自动下沉到 C

兼容旧写法：``import pythonpp`` 之后 ``pythonpp.Lang()`` 照旧可用，
单文件 ``pythonpp.py`` 也仍然可以照旧使用（两者 API 一致）。
"""

from __future__ import annotations

from ._core import (
    Lang,
    MemPool,
    PoolStats,
    __version__,
)
from . import _native

# 纯函数接口（与旧版一致，只是内核换成 C）
from ._io import (
    append,
    copy,
    exists,
    home,
    is_dir,
    is_file,
    join,
    lines,
    listdir,
    mkdir,
    move,
    read,
    read_bytes,
    remove,
    rmtree,
    size,
    splitext,
    touch,
    write,
    write_bytes,
    pwd,
)
from ._misc import bocodepy, counter, nex, now, sleep, timeit
from ._rand import choice, randbytes, random, sample, seed, shuffle
from ._calcs import (
    clamp,
    deep_sizeof,
    dot,
    fast_sum,
    gcd,
    is_prime,
    lcm,
    mean,
    median,
    mode,
    range_sum,
    stdev,
    variance,
)
from ._console import ensure_utf8_console

# 包一被导入就把控制台编码兜底好。
# 这样用户 print 中文、看中文报错时，不会因为终端是 cp1252/cp437 而崩掉。
ensure_utf8_console()

from ._errors import (
    PPError,
    PPCompileError,
    PPRuntimeError,
    PPSyntaxError,
    explain_error,
)
from ._jit import JITFunction, jit, native_available
from ._strutil import (
    chunk,
    dedent,
    is_palindrome,
    slugify,
    snake_case,
    strip_ansi,
    truncate,
)

__all__ = [
    # 核心
    "Lang",
    "MemPool",
    "PoolStats",
    "__version__",
    # 随机
    "random",
    "seed",
    "shuffle",
    "randbytes",
    "choice",
    "sample",
    # 文件
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
    # 计算
    "deep_sizeof",
    "dot",
    "fast_sum",
    "range_sum",
    "mean",
    "median",
    "mode",
    "variance",
    "stdev",
    "clamp",
    "is_prime",
    "gcd",
    "lcm",
    # 字符串
    "chunk",
    "dedent",
    "is_palindrome",
    "slugify",
    "snake_case",
    "truncate",
    "strip_ansi",
    # 加速
    "jit",
    "JITFunction",
    "native_available",
    # 错误
    "PPError",
    "PPCompileError",
    "PPRuntimeError",
    "PPSyntaxError",
    "explain_error",
    # 杂项 / 彩蛋
    "now",
    "sleep",
    "timeit",
    "counter",
    "nex",
    "bocodepy",
]

__local_version__ = __version__
