# -*- coding: utf-8 -*-
"""把 Python 函数翻译成 C 源码。

只处理"纯整数语义"的子集，但把子集里的每一条都翻译成**真正的机器码**，
而不是解释执行。生成的结构大概是：

    long long pp_f(long long n, int *status) {
        long long t1, t2, res = 0;
        int st = 0, ret = 0;
        long long guard = 400000000LL;
        ...
    }

每个算术后都跟着一次溢出检查，一旦溢出就 ``goto done`` 并置 status=1，
Python 侧收到后会用原生实现重算 —— 这样"快"永远不会以"错"为代价。

支持：int 变量/字面量、+= 等增强赋值、for+range、while、if/elif/else、
      break/continue、多个 return、and/or/not、比较链、三目表达式、
      + - * // % & | ^ << >> 与一元负号。
不支持：浮点、字符串、容器、函数调用、生成器等（自动回退）。
"""

from __future__ import annotations

import ast

__all__ = ["generate", "Unsupported"]


class Unsupported(Exception):
    """这段代码没法翻译成 C。"""


# ---- 状态码：必须和 _cjit.py 保持一致 ----
ST_OK = 0
ST_OVERFLOW = 1
ST_DIVZERO = 2
ST_GUARD = 3
ST_NO_RETURN = 4
ST_SHIFT = 5

_PRELUDE = r"""
#include <stdint.h>
#include <limits.h>

#ifdef _WIN32
#define PP_EXPORT __declspec(dllexport)
#else
#define PP_EXPORT __attribute__((visibility("default")))
#endif

/* Python 的 // 向下取整，C 的 / 向零取整 */
static long long pp_floordiv(long long a, long long b) {
    long long q = a / b, r = a % b;
    if (r != 0 && ((r ^ b) < 0)) q -= 1;
    return q;
}
static long long pp_mod(long long a, long long b) {
    long long r = a % b;
    if (r != 0 && ((r ^ b) < 0)) r += b;
    return r;
}
"""


class _Gen:
    def __init__(self, func_name):
        self.name = func_name
        self.lines = []
        self.decls = []
        self.known = set()          # 已经声明过的 C 变量名
        self.temps = 0
        self.labels = 0
        self.depth = 0
        self.ret_count = 0
        self.uses_guard = False

    # ---------- 基础设施 ----------
    def emit(self, line):
        self.lines.append("    " * self.depth + line)

    def temp(self):
        self.temps += 1
        name = f"t{self.temps}"
        self.decls.append(f"long long {name}")
        self.known.add(name)
        return name

    def label(self, hint="L"):
        self.labels += 1
        return f"{hint}{self.labels}"

    def mark(self, label):
        self.lines.append(f"{label}: ;")

    def var(self, name):
        """变量名 -> C 局部变量名（避开 C 关键字和内部名字）。"""
        return f"v_{name}"

    def declare_var(self, name):
        cname = self.var(name)
        entry = f"long long {cname}"
        if entry not in self.decls:
            self.decls.append(entry)
            self.known.add(cname)
        return cname

    # ---------- 语句 ----------
    def stmt(self, node):
        handler = getattr(self, "_s_" + type(node).__name__, None)
        if handler is None:
            raise Unsupported(f"语句 {type(node).__name__} 不支持")
        handler(node)

    def block(self, nodes):
        for node in nodes:
            self.stmt(node)

    def _s_Pass(self, node):
        self.emit(";")

    def _s_Assign(self, node):
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            raise Unsupported("只支持单个名字的赋值")
        value = self.expr(node.value)
        self.emit(f"{self.declare_var(node.targets[0].id)} = {value};")

    def _s_AnnAssign(self, node):
        if node.value is None:
            return
        if not isinstance(node.target, ast.Name):
            raise Unsupported("不支持带注解的复杂赋值")
        value = self.expr(node.value)
        self.emit(f"{self.declare_var(node.target.id)} = {value};")

    def _s_AugAssign(self, node):
        if not isinstance(node.target, ast.Name):
            raise Unsupported("只支持变量的增强赋值")
        target = self.declare_var(node.target.id)
        right = self.expr(node.value)
        self.binop_inplace(node.op, target, right)

    def _s_If(self, node):
        cond = self.condition(node.test)
        self.emit(f"if ({cond}) {{")
        self.depth += 1
        self.block(node.body)
        self.depth -= 1
        if node.orelse:
            self.emit("} else {")
            self.depth += 1
            self.block(node.orelse)
            self.depth -= 1
        self.emit("}")

    def _s_While(self, node):
        if node.orelse:
            raise Unsupported("while...else 不支持")
        self.uses_guard = True
        self.emit("while (1) {")
        self.depth += 1
        self.emit("if (--guard <= 0) { st = 3; goto pp_done; }")
        cond = self.condition(node.test)
        self.emit(f"if (!({cond})) break;")
        self.block(node.body)
        self.depth -= 1
        self.emit("}")

    def _s_For(self, node):
        if node.orelse:
            raise Unsupported("for...else 不支持")
        if not isinstance(node.target, ast.Name):
            raise Unsupported("只支持 for i in range(...)")
        call = node.iter
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "range"):
            raise Unsupported("只支持 range() 循环")
        if call.keywords:
            raise Unsupported("range() 不支持关键字参数")
        args = call.args
        if not 1 <= len(args) <= 3:
            raise Unsupported("range() 参数个数不对")

        # range 的参数只在循环开始前求值一次（和 Python 一致）
        if len(args) == 1:
            start = self.imm(0)
            stop = self.expr(args[0])
            step = self.imm(1)
        elif len(args) == 2:
            start = self.expr(args[0])
            stop = self.expr(args[1])
            step = self.imm(1)
        else:
            start = self.expr(args[0])
            stop = self.expr(args[1])
            step = self.expr(args[2])

        i = self.declare_var(node.target.id)
        self.emit(f"{i} = {start};")
        self.emit(f"if ({step} == 0) {{ st = 2; goto pp_done; }}")
        self.uses_guard = True
        self.emit("while (1) {")
        self.depth += 1
        self.emit("if (--guard <= 0) { st = 3; goto pp_done; }")
        self.emit(f"if (!(({step} > 0) ? ({i} < {stop}) : ({i} > {stop}))) break;")
        self.block(node.body)
        # i += step，带溢出检查
        self.emit(f"if (__builtin_add_overflow({i}, {step}, &{i})) "
                  "{ st = 1; goto pp_done; }")
        self.depth -= 1
        self.emit("}")

    def _s_Return(self, node):
        if node.value is None:
            raise Unsupported("不支持空 return")
        value = self.expr(node.value)
        self.ret_count += 1
        self.emit(f"res = {value}; ret = 1; goto pp_done;")

    def _s_Break(self, node):
        self.emit("break;")

    def _s_Continue(self, node):
        self.emit("continue;")

    def _s_Expr(self, node):
        raise Unsupported("独立的表达式语句不支持")

    # ---------- 条件 ----------
    def condition(self, node):
        """返回一个 C 布尔表达式（不新开临时变量之外的控制流）。"""
        if isinstance(node, ast.Compare):
            return self.compare(node)
        if isinstance(node, ast.BoolOp):
            op = " && " if isinstance(node.op, ast.And) else " || "
            return "(" + op.join(self.condition(v) for v in node.values) + ")"
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return f"(!({self.condition(node.operand)}))"
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return "1" if node.value else "0"
        if isinstance(node, ast.Name) and node.id in ("True", "False"):
            return "1" if node.id == "True" else "0"
        return f"({self.expr(node)} != 0)"

    def compare(self, node):
        parts = []
        left = self.expr(node.left)
        for op, right_node in zip(node.ops, node.comparators):
            right = self.expr(right_node)
            symbol = _CMP_C.get(type(op))
            if symbol is None:
                raise Unsupported(f"比较运算 {type(op).__name__}")
            if isinstance(op, ast.Is) or isinstance(op, ast.IsNot):
                raise Unsupported("is 比较不支持")
            parts.append(f"({left} {symbol} {right})")
            left = right
        return "(" + " && ".join(parts) + ")"

    # ---------- 表达式 ----------
    def imm(self, value):
        name = self.temp()
        self.emit(f"{name} = {value}LL;")
        return name

    def expr(self, node):
        handler = getattr(self, "_e_" + type(node).__name__, None)
        if handler is None:
            raise Unsupported(f"表达式 {type(node).__name__} 不支持")
        return handler(node)

    def _e_Constant(self, node):
        value = node.value
        if isinstance(value, bool) or not isinstance(value, int):
            raise Unsupported("只支持整数常量")
        if not -(2 ** 63) <= value < 2 ** 63:
            raise Unsupported("整数常量超出 int64")
        name = self.temp()
        self.emit(f"{name} = {value}LL;")
        return name

    def _e_Name(self, node):
        cname = self.var(node.id)
        if cname not in self.known:
            raise Unsupported(f"变量 {node.id} 使用前没有赋值")
        return cname

    def _e_UnaryOp(self, node):
        if isinstance(node.op, ast.USub):
            inner = self.expr(node.operand)
            out = self.temp()
            self.emit(f"if (__builtin_sub_overflow(0LL, {inner}, &{out})) "
                      "{ st = 1; goto pp_done; }")
            return out
        if isinstance(node.op, ast.UAdd):
            return self.expr(node.operand)
        if isinstance(node.op, ast.Invert):
            inner = self.expr(node.operand)
            out = self.temp()
            self.emit(f"if (__builtin_sub_overflow(0LL, {inner}, &{out})) "
                      "{ st = 1; goto pp_done; }")
            self.emit(f"if (__builtin_sub_overflow({out}, 1LL, &{out})) "
                      "{ st = 1; goto pp_done; }")
            return out
        if isinstance(node.op, ast.Not):
            inner = self.expr(node.operand)
            out = self.temp()
            self.emit(f"{out} = !({inner} != 0);")
            return out
        raise Unsupported("一元运算不支持")

    def _e_BinOp(self, node):
        left = self.expr(node.left)
        right = self.expr(node.right)
        out = self.temp()
        self.binop_into(node.op, left, right, out)
        return out

    def _e_BoolOp(self, node):
        """and / or 作为值：短路求值，结果为 0/1（严格布尔，够用了）。"""
        out = self.temp()
        end = self.label("bool")
        if isinstance(node.op, ast.And):
            self.emit(f"{out} = 0;")
            for value in node.values:
                cond = self.condition(value)
                self.emit(f"if (!({cond})) goto {end};")
            self.emit(f"{out} = 1;")
        else:
            self.emit(f"{out} = 1;")
            for value in node.values:
                cond = self.condition(value)
                self.emit(f"if ({cond}) goto {end};")
            self.emit(f"{out} = 0;")
        self.mark(end)
        return out

    def _e_Compare(self, node):
        out = self.temp()
        cond = self.compare(node)
        self.emit(f"{out} = ({cond}) ? 1 : 0;")
        return out

    def _e_IfExp(self, node):
        out = self.temp()
        other = self.label("iexpr")
        end = self.label("iend")
        cond = self.condition(node.test)
        self.emit(f"if (!({cond})) goto {other};")
        body = self.expr(node.body)
        self.emit(f"{out} = {body}; goto {end};")
        self.mark(other)
        alt = self.expr(node.orelse)
        self.emit(f"{out} = {alt};")
        self.mark(end)
        return out

    # ---------- 二元运算 ----------
    def binop_into(self, op, left, right, out):
        if isinstance(op, (ast.Add, ast.Sub, ast.Mult)):
            builtin = {
                ast.Add: "__builtin_add_overflow",
                ast.Sub: "__builtin_sub_overflow",
                ast.Mult: "__builtin_mul_overflow",
            }[type(op)]
            self.emit(f"if ({builtin}({left}, {right}, &{out})) "
                      "{ st = 1; goto pp_done; }")
            return
        if isinstance(op, (ast.FloorDiv, ast.Mod)):
            self.emit(f"if ({right} == 0) {{ st = 2; goto pp_done; }}")
            self.emit(f"if ({left} == LLONG_MIN && {right} == -1) "
                      "{ st = 1; goto pp_done; }")
            fn = "pp_floordiv" if isinstance(op, ast.FloorDiv) else "pp_mod"
            self.emit(f"{out} = {fn}({left}, {right});")
            return
        if isinstance(op, (ast.BitAnd, ast.BitOr, ast.BitXor)):
            symbol = {ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^"}[type(op)]
            self.emit(f"{out} = {left} {symbol} {right};")
            return
        if isinstance(op, ast.LShift):
            self.emit(f"if ({right} < 0 || {right} > 62) {{ st = 5; goto pp_done; }}")
            self.emit(f"if (__builtin_mul_overflow({left}, 1LL << {right}, &{out})) "
                      "{ st = 1; goto pp_done; }")
            return
        if isinstance(op, ast.RShift):
            self.emit(f"if ({right} < 0 || {right} > 63) {{ st = 5; goto pp_done; }}")
            # Python 的 >> 对负数是向下取整，C 的算术右移正好一致
            self.emit(f"{out} = {left} >> {right};")
            return
        if isinstance(op, ast.Pow):
            raise Unsupported("幂运算不支持")
        if isinstance(op, ast.Div):
            raise Unsupported("真除法（浮点）不支持")
        if isinstance(op, ast.MatMult):
            raise Unsupported("矩阵乘法不支持")
        raise Unsupported(f"二元运算 {type(op).__name__} 不支持")

    def binop_inplace(self, op, target, right):
        out = self.temp()
        self.binop_into(op, target, right, out)
        self.emit(f"{target} = {out};")

    # ---------- 生成 ----------
    def program(self, params):
        # 参数已经是 C 形参，不能再当局部变量声明一次
        param_names = {self.var(p) for p in params}
        decls = ";\n    ".join(
            d for d in self.decls if d.split()[-1] not in param_names
        )
        if decls:
            decls += ";"
        guard_line = "long long guard = 400000000LL;" if self.uses_guard else ""
        cparams = ", ".join(f"long long {self.var(p)}" for p in params) or "void"
        body = "\n".join(self.lines)
        return f"""{_PRELUDE}
PP_EXPORT long long pp_entry({cparams}, int *status)
{{
    long long res = 0;
    int st = 0, ret = 0;
    {decls}
    {guard_line}
    (void)res; (void)ret; (void)guard;
{body}
pp_done:
    if (!ret && st == 0) st = {ST_NO_RETURN};
    *status = st;
    return res;
}}
"""


_C_KEYWORDS = frozenset(
    """auto break case char const continue default do double else enum extern
    float for goto if inline int long register restrict return short signed
    sizeof static struct switch typedef union unsigned void volatile while""".split()
)

_CMP_C = {
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
}


def generate(source, func_name=None):
    """把源码里的函数翻译成 C 源码。

    返回 ``(c_source, 参数名, 函数名)``；不能翻译时抛 :class:`Unsupported`。
    """
    import textwrap

    try:
        module = ast.parse(textwrap.dedent(source))
    except SyntaxError as exc:
        raise Unsupported(f"源码无法解析：{exc}") from exc

    node = None
    for item in module.body:
        if isinstance(item, ast.FunctionDef):
            if func_name is None or item.name == func_name:
                node = item
                break
    if node is None:
        raise Unsupported("源码里没有函数定义")

    args = node.args
    if args.vararg or args.kwarg or args.kwonlyargs:
        raise Unsupported("不支持 *args / **kwargs / 仅关键字参数")
    if args.defaults or args.kw_defaults:
        raise Unsupported("不支持默认参数")
    params = [a.arg for a in args.args]
    if not params:
        raise Unsupported("没有参数")

    gen = _Gen(node.name)
    for name in params:
        gen.declare_var(name)
    gen.block(node.body)
    if gen.ret_count == 0:
        raise Unsupported("函数没有 return")
    code = gen.program(params)
    return code, params, node.name
