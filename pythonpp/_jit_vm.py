# -*- coding: utf-8 -*-
"""后备后端：把纯整数函数翻译成内置 C 虚拟机的指令流。

只有在**装不上编译器**的时候才会用到它。它比原生 Python 快一倍多，
但远不如真正编译成机器码，所以优先级排在 C 编译之后。

指令格式（每条 24 字节，与 pythonpp_native.c 里的 pp_ins 一一对应）：

    struct { uint8 op; int16 a, b; int32 c; int64 imm; uint8 pad[7]; }

    PUSH_IMM imm            压入立即数
    PUSH_VAR a              压入变量 a
    STORE    a              弹出栈顶写入变量 a
    ADD/SUB/MUL/DIV/MOD/AND/OR/XOR/SHL/SHR
    NEG
    CMP      a=cond imm=目标   弹出 b、a；条件成立就跳
    JMP      imm=目标
    LOOP_CHECK a=循环变量 b=终值 c=步长 imm=退出目标
    OUT                     把栈顶推入输出列表
    HALT
"""

from __future__ import annotations

import ast
import struct
import textwrap

__all__ = ["compile_source", "OP"]

OP = {
    "PUSH_IMM": 1, "PUSH_VAR": 2, "STORE": 3,
    "ADD": 4, "SUB": 5, "MUL": 6, "DIV": 7, "MOD": 8,
    "AND": 9, "OR": 10, "XOR": 11, "SHL": 12, "SHR": 13,
    "NEG": 14, "CMP": 15, "JMP": 16, "LOOP_CHECK": 17,
    "OUT": 18, "HALT": 19,
}
COND = {"==": 0, "!=": 1, "<": 2, "<=": 3, ">": 4, ">=": 5}
NEGATE = {"==": "!=", "!=": "==", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}
INS = struct.Struct("<Bhhiq7x")
INS_SIZE = INS.size
IMM_OFFSET = 9
MAX_VARS = 12

_CMP = {
    ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
}
_BINOP = {
    ast.Add: "ADD", ast.Sub: "SUB", ast.Mult: "MUL", ast.FloorDiv: "DIV",
    ast.Mod: "MOD", ast.BitAnd: "AND", ast.BitOr: "OR", ast.BitXor: "XOR",
    ast.LShift: "SHL", ast.RShift: "SHR",
}


class Unsupported(Exception):
    pass


class _Emitter:
    def __init__(self):
        self.buf = bytearray()
        self.count = 0
        self.fixups = []

    def emit(self, op, a=0, b=0, c=0, imm=0, jump=None):
        index = self.count
        try:
            self.buf += INS.pack(OP[op], int(a), int(b), int(c), int(imm))
        except (struct.error, OverflowError) as exc:
            raise Unsupported(f"操作数越界：{op}") from exc
        self.count += 1
        if jump is not None:
            self.fixups.append((index, jump))
        return index

    def patch(self, labels):
        for index, label in self.fixups:
            if labels.get(label) is None:
                raise Unsupported("跳转目标丢失")
            offset = index * INS_SIZE + IMM_OFFSET
            self.buf[offset:offset + 8] = int(labels[label]).to_bytes(8, "little", signed=True)
        self.fixups.clear()


class _Translator:
    def __init__(self, node):
        self.node = node
        self.em = _Emitter()
        self.vars = {}
        self.labels = {}
        self.seq = 0
        self.returns = 0
        self.temps = 0
        self.active = []

    # ---- 基础设施 ----
    def slot(self, name, create=False):
        if name in self.vars:
            return self.vars[name]
        if not create:
            raise Unsupported(f"变量 {name} 使用前没有赋值")
        if len(self.vars) >= MAX_VARS:
            raise Unsupported("变量太多")
        self.vars[name] = len(self.vars)
        return self.vars[name]

    def temp(self):
        self.temps += 1
        return self.slot(f"$t{self.temps}", create=True)

    def label(self, hint="L"):
        self.seq += 1
        name = f"{hint}{self.seq}"
        self.labels[name] = None
        return name

    def mark(self, label):
        self.labels[label] = self.em.count

    # ---- 入口 ----
    def translate(self):
        params = [a.arg for a in self.node.args.args]
        for p in params:
            self.slot(p, create=True)
        for stmt in self.node.body:
            self.stmt(stmt)
        self.em.emit("HALT")
        self.em.patch(self.labels)
        return params, bytes(self.em.buf)

    # ---- 语句 ----
    def stmt(self, node):
        handler = getattr(self, "_s_" + type(node).__name__, None)
        if handler is None:
            raise Unsupported(f"语句 {type(node).__name__}")
        handler(node)

    def block(self, nodes):
        for n in nodes:
            self.stmt(n)

    def _s_Pass(self, node):
        return None

    def _s_Assign(self, node):
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            raise Unsupported("只支持单个名字赋值")
        self.expr(node.value)
        self.em.emit("STORE", self.slot(node.targets[0].id, create=True))

    def _s_AugAssign(self, node):
        if not isinstance(node.target, ast.Name):
            raise Unsupported("只支持变量增强赋值")
        op = _BINOP.get(type(node.op))
        if op is None:
            raise Unsupported("增强赋值不支持")
        slot = self.slot(node.target.id, create=False)
        self.em.emit("PUSH_VAR", slot)
        self.expr(node.value)
        self.em.emit(op)
        self.em.emit("STORE", slot)

    def _s_If(self, node):
        other = self.label("else")
        self.jump_if_false(node.test, other)
        self.block(node.body)
        if node.orelse:
            end = self.label("endif")
            self.em.emit("JMP", jump=end)
            self.mark(other)
            self.block(node.orelse)
            self.mark(end)
        else:
            self.mark(other)

    def _s_While(self, node):
        if node.orelse:
            raise Unsupported("while...else")
        start = self.label("while")
        end = self.label("wend")
        self.mark(start)
        self.jump_if_false(node.test, end)
        self.block(node.body)
        self.em.emit("JMP", jump=start)
        self.mark(end)

    def _s_For(self, node):
        if node.orelse:
            raise Unsupported("for...else")
        if not isinstance(node.target, ast.Name):
            raise Unsupported("只支持 for i in range(...)")
        call = node.iter
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "range"):
            raise Unsupported("只支持 range()")
        if call.keywords or not 1 <= len(call.args) <= 3:
            raise Unsupported("range() 参数不对")
        args = call.args
        name = node.target.id
        if name in self.active:
            raise Unsupported("嵌套循环复用同一变量")

        t_start, t_stop, t_step = self.temp(), self.temp(), self.temp()
        if len(args) == 1:
            self.em.emit("PUSH_IMM", imm=0)
            self.em.emit("STORE", t_start)
            self.expr(args[0])
            self.em.emit("STORE", t_stop)
        else:
            self.expr(args[0])
            self.em.emit("STORE", t_start)
            self.expr(args[1])
            self.em.emit("STORE", t_stop)
        if len(args) == 3:
            self.expr(args[2])
        else:
            self.em.emit("PUSH_IMM", imm=1)
        self.em.emit("STORE", t_step)

        loop = self.slot(name, create=True)
        self.em.emit("PUSH_VAR", t_start)
        self.em.emit("STORE", loop)

        check = self.label("for")
        end = self.label("fend")
        self.mark(check)
        self.em.emit("LOOP_CHECK", a=loop, b=t_stop, c=t_step, jump=end)
        self.active.append(name)
        self.block(node.body)
        self.active.pop()
        self.em.emit("PUSH_VAR", loop)
        self.em.emit("PUSH_VAR", t_step)
        self.em.emit("ADD")
        self.em.emit("STORE", loop)
        self.em.emit("JMP", jump=check)
        self.mark(end)

    def _s_Return(self, node):
        if node.value is None:
            raise Unsupported("空 return")
        self.returns += 1
        if self.returns > 1:
            raise Unsupported("多个 return")
        self.expr(node.value)
        self.em.emit("OUT")
        if node is not self.node.body[-1]:
            raise Unsupported("return 必须是最后一条语句")

    def _s_Break(self, node):
        raise Unsupported("break")

    def _s_Continue(self, node):
        raise Unsupported("continue")

    def _s_Expr(self, node):
        raise Unsupported("表达式语句")

    # ---- 条件 ----
    def jump_if_false(self, node, target):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op = _CMP.get(type(node.ops[0]))
            if op is None:
                raise Unsupported("比较运算")
            self.expr(node.left)
            self.expr(node.comparators[0])
            self.em.emit("CMP", a=COND[NEGATE[op]], jump=target)
            return
        self.expr(node)
        self.em.emit("PUSH_IMM", imm=0)
        self.em.emit("CMP", a=COND["=="], jump=target)

    # ---- 表达式 ----
    def expr(self, node):
        handler = getattr(self, "_e_" + type(node).__name__, None)
        if handler is None:
            raise Unsupported(f"表达式 {type(node).__name__}")
        handler(node)

    def _e_Constant(self, node):
        if isinstance(node.value, bool) or not isinstance(node.value, int):
            raise Unsupported("只支持整数字面量")
        self.em.emit("PUSH_IMM", imm=node.value)

    def _e_Name(self, node):
        self.em.emit("PUSH_VAR", self.slot(node.id, create=False))

    def _e_UnaryOp(self, node):
        if isinstance(node.op, ast.USub):
            if isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, int):
                self.em.emit("PUSH_IMM", imm=-node.operand.value)
            else:
                self.expr(node.operand)
                self.em.emit("NEG")
        elif isinstance(node.op, ast.UAdd):
            self.expr(node.operand)
        else:
            raise Unsupported("一元运算")

    def _e_BinOp(self, node):
        op = _BINOP.get(type(node.op))
        if op is None:
            raise Unsupported("二元运算")
        self.expr(node.left)
        self.expr(node.right)
        self.em.emit(op)

    def _e_Compare(self, node):
        if len(node.ops) != 1:
            raise Unsupported("链式比较")
        op = _CMP.get(type(node.ops[0]))
        if op is None:
            raise Unsupported("比较运算")
        true_label = self.label("ct")
        end = self.label("cend")
        self.expr(node.left)
        self.expr(node.comparators[0])
        self.em.emit("CMP", a=COND[op], jump=true_label)
        self.em.emit("PUSH_IMM", imm=0)
        self.em.emit("JMP", jump=end)
        self.mark(true_label)
        self.em.emit("PUSH_IMM", imm=1)
        self.mark(end)

    def _e_IfExp(self, node):
        other = self.label("ie")
        end = self.label("iend")
        self.jump_if_false(node.test, other)
        self.expr(node.body)
        self.em.emit("JMP", jump=end)
        self.mark(other)
        self.expr(node.orelse)
        self.mark(end)


def compile_source(source, func_name=None):
    """翻译成整数指令流，返回 ``(指令流, 参数名, 变量表, return 个数)``。"""
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
    if args.vararg or args.kwarg or args.kwonlyargs or args.defaults:
        raise Unsupported("不支持可变参数或默认参数")
    translator = _Translator(node)
    params, code = translator.translate()
    return code, params, translator.vars, translator.returns
