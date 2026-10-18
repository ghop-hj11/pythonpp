# Python++ 1.0

> Python 你会多少，Python++ 就会多少。它不改变语法，只是在旁边放了一箱更好用的工具，
> 并且能在你需要的时候，把慢代码真正编译成机器码。

## 安装

```bash
pip install pythonpp-plus
```

> ⚠️ **包名和导入名不一样**，这是唯一需要留意的点：
>
> | | 名字 |
> |---|---|
> | `pip install` 时写的 | `pythonpp-plus` |
> | 代码里 `import` 的 | `pythonpp` |
>
> 为什么？因为 PyPI 上的 `pythonpp` 这个名字已经被另一个同名项目占用了，
> 只能换个发布名，但导入名保持 `pythonpp` 不变，你写的代码完全不用迁就它。

装好之后验证一下：

```python
import pythonpp
print(pythonpp._native.describe())
```

看到 `C 内核 1.0.0（稳定 ABI，...）` 就说明原生加速已经生效；
如果显示 `纯 Python 回退`，说明当前平台没有对应的预编译包（功能完全正常，只是慢一点），
装上 `pip install ziglang` 后重新安装即可启用。

---

# 第一部分：先看怎么用（零基础也能照着做）

## 1. 它是什么？一句话说清

Python++ 是**跑在 Python 上面的一个工具包**。

它**不是**一门新语言。你不用重新学语法——`for`、`if`、函数、类、`import`，
Python 怎么写，这里就怎么写。它做三件事：

1. **加工具**：内置 50 多个常用函数（读写文件、随机数、算平均值……），不用再 `import`
2. **加翻译**：报错信息变成中文，还告诉你该怎么改
3. **加速度**：给函数加一个 `@jit`，某些纯计算的循环能快 100 倍

## 2. 三步跑起来

**第一步：确认 Python 装好了**

按 `Win + R`，输入 `cmd` 回车，在黑窗口里输入：

```bash
python --version
```

看到 `Python 3.x.x` 就说明装好了。没有的话去 <https://www.python.org/downloads/>
下载安装，安装时**记得勾选 "Add Python to PATH"**。

**第二步：进到 Python++ 所在的文件夹**

假设它在 `D:\Python++`：

```bash
cd /d D:\Python++
```

> 小提示：`cd` 是"切换文件夹"的意思，`/d` 表示允许切换到别的盘。
> 如果你把 Python++ 放在别处，把路径换成你自己的。

**第三步：跑一下试试**

```bash
python -m pythonpp --info
```

如果屏幕上打印出类似这样的内容，就成功了：

```
Python++ 1.0.0  ——  Python 语法 + C 加速
内核：C 内核 1.0.0（稳定 ABI，Python 3.12.14）
JIT ：可用
...
```

## 3. 你会用到的四种玩法

### 玩法一：交互式（像计算器一样，边写边看）

```bash
python -m pythonpp
```

看到 `>>>` 就可以直接敲代码了：

```
>>> 1 + 1
2
>>> random(1, 6)
4
>>> name = "小明"
>>> print("你好，" + name)
你好，小明
>>> exit()
```

几点说明：

* **不用写 `print` 也能看到结果**——敲 `1 + 1` 直接显示 `2`
* 内置函数（`random`、`read`、`write` 等）**拿来就用**，不用 import
* 输入 `exit()` 退出
* 输错了不要慌，它会用中文告诉你哪里错了

### 玩法二：写个文件跑（最常用）

新建一个文件 `hello.py`（用记事本、VS Code 都行），写：

```python
from pythonpp import Lang

lang = Lang()
lang.run("""
名字 = "小明"
年龄 = 18
print(f"{名字} 今年 {年龄} 岁")
""")
```

然后运行：

```bash
python hello.py
```

输出：

```
小明 今年 18 岁
```

> 注意：写代码时**建议存成 UTF-8 编码**，否则中文可能乱码。
> 记事本另存为的时候，编码选 "UTF-8"。

### 玩法三：不写文件，直接跑一行

```bash
python -m pythonpp -c "print(sum(range(1, 101)))"
```

输出 `5050`。适合快速试一小段代码。

### 玩法四：给老代码提速（进阶，但很简单）

原来的代码**一个字都不用改**，只要在函数上加一行 `@jit`：

```python
from pythonpp import jit

@jit
def 求和(n):
    总量 = 0
    for i in range(n):
        总量 += i * 2
    return 总量

print(求和(20_000_000))    # 原本要 3 秒，现在 0.03 秒
```

> 第一次调用会花约 1 秒编译，之后就一直是机器码速度。
> 如果这段代码不适合编译（比如有字符串、浮点数），它会**自动退回普通 Python**，
> 结果永远正确，只是没加速而已。

## 4. 遇到报错怎么办？

Python++ 会把英文报错翻成中文，还给你建议。比如：

```python
lang.run("1 / 0")
```

屏幕上出现的是：

```
ZeroDivisionError —— 除以零
位置：<lang>:1
源码：1 / 0
细节：除数是 0，先判断再除。
建议：除数是 0。做除法前先判断一下，或者用 if b: 包一层。
```

再比如写错变量名：

```
NameError —— 名字 'undefined_var' 没有定义
位置：<lang>:1
源码：print(undefined_var)
细节：是不是拼错了？确认 undefined_var 在作用域里，或者先用 undefined_var = ... 赋值。
建议：变量/函数名拼错了，或者还没赋值就用了。
```

**看最上面那一行就够了**，它会告诉你错在哪一类；下面的"建议"告诉你怎么改。

## 5. 五个最常见的坑

| 你看到的 | 意思 | 怎么办 |
|---|---|---|
| `No module named 'pythonpp'` | 找不到 Python++ | 先 `cd` 到 Python++ 所在的文件夹再运行 |
| 中文变成乱码 | 文件编码不对 | 文件另存为 UTF-8 编码 |
| `IndentationError` | 缩进错了 | 同一层的代码缩进必须一样，别混用 Tab 和空格 |
| `@jit` 没变快 | 这段代码不适合编译 | 只对"纯整数计算"的循环有效；其他情况自动退回，不会出错 |
| `python` 命令不存在 | Python 没装好或没加 PATH | 重装 Python，勾选 "Add Python to PATH" |

## 6. 文件放哪、从哪开始看

```
D:\Python++\
├── pythonpp.py          ← 老入口（照样能用，别删）
├── pythonpp\            ← 真正的实现，整个文件夹
├── README.md            ← 你正在看的这份
├── build_native.py      ← 重新编译 C 内核时用
└── test_pythonpp.py     ← 自检：python test_pythonpp.py
```

**建议阅读顺序**：先把上面的"四种玩法"都试一遍 → 再看第二部分有没有需要的函数 →
最后有需要再研究第三部分的原理。

---

# 第二部分：完整功能说明

## 1. 兼容性：你的老代码不用改

旧版 `pythonpp.py` 的**每一个 API 都还在**，原有代码一行都不用改：

```python
from pythonpp import Lang
lang = Lang()
print(lang.touch("test.txt"))
```

`pythonpp.py` 现在是一个兼容层，会自动转发到 `pythonpp/` 包。两种写法等价：

```python
import pythonpp            # 导入兼容层 → 转发到包
from pythonpp import Lang  # 同上
```

旧版文件已备份为 `pythonpp_v1_backup.py`。

## 2. Lang：执行环境

```python
from pythonpp import Lang

lang = Lang()
lang.run("x = 1 + 1")
print(lang["x"])            # 2
print(lang.eval("x * 21"))  # 42
```

常用方法：

```python
lang.run(源码)                    # 执行一段代码，返回 RunResult
lang.eval(表达式)                 # 算一个表达式
lang["x"]                         # 取变量（也可 lang["x"] = 1 赋值、del lang["x"] 删除）
lang.ls()                         # 列出所有变量名
lang.cat("x")                     # 查看变量值（不删）
lang.rm("x")                      # 删除变量
lang.stats()                      # 变量池状态
lang.add("名字", 值)               # 手动加变量
lang.bind("函数名", 函数)          # 往环境里塞对象（函数、模块）
lang.snapshot() / lang.restore(s) # 拍快照 / 回滚
lang.exec_file("脚本.py")          # 执行一个文件
lang.jit(函数, 参数)               # 手动下沉
with Lang() as lang:              # 用完自动清空
    ...
```

`run()` 返回一个 `RunResult`：

```python
result = lang.run("x = 1")
result.ok       # True
result.elapsed  # 用时（秒）
result.stdout   # 如果 Lang(capture_stdout=True)，这里是捕获的输出
```

出错时默认抛异常（异常信息已翻成中文，并带 `.pp_explained` 属性）。
想让脚本自己兜底，就传 `show_errors=False`：

```python
result = lang.run("这段代码有错", show_errors=False)
if not result.ok:
    print(result.explained)   # 中文说明
```

## 3. 内置函数（52 个，进了 Lang 就能直接用）

> 交互式里和 `Lang` 里都直接可用，**不需要 import**。
> 想查某个函数的用法：`help(函数名)`。

### 随机数（6 个）

```python
random()              # 1~20 的随机整数
random(1, 6)          # 1~6（掷骰子）
random("5~100")       # 5~100，字符串写法也行
random(1.0, 2.0)      # 浮点区间
random(["石头","剪刀","布"])   # 从列表里随机挑一个
seed(42)              # 固定种子，结果可复现
shuffle(列表)          # 原地洗牌
randbytes(16)         # 16 个随机字节
choice(列表)           # 随机取一个
sample(列表, 3)        # 不重复地取 3 个
```

用的是 xoshiro256\*\* 算法（C 实现），长区间也分布均匀，没有取模偏置。

### 文件操作（21 个）

```python
pwd()                       # 当前目录
home()                      # 用户主目录
join("a", "b.txt")          # 拼路径 → a\b.txt
mkdir("a/b/c")              # 建目录（含父目录），已存在不报错
touch("a.txt")              # 建空文件
read("a.txt")               # 读文本
write("a.txt", 内容)         # 写文本（自动建父目录）
append("a.txt", 内容)        # 追加
read_bytes("a.bin")         # 读二进制
write_bytes("a.bin", 数据)   # 写二进制
exists("a.txt")             # 是否存在
is_dir / is_file            # 是目录 / 是文件
listdir(".")                # 列出目录项（已排序）
remove("a.txt")             # 删文件（不存在也不报错，返回 bool）
rmtree("目录")               # 删整个目录树
copy("源", "目标")           # 复制
move("源", "目标")           # 移动
size("文件或目录")            # 字节数
splitext("a/b.py")          # → ("a/b", ".py")
lines("a.txt")              # 按行读，自动去掉换行符
```

### 计算（13 个）

```python
fast_sum(可迭代对象)         # 整数求和；对 range 自动走 O(1) 公式
range_sum(1, 100_000_001)   # 1 到 1 亿求和，瞬间出结果（公式，不循环）
dot([1,2,3], [4,5,6])       # 点积 → 32
mean([1,2,3])               # 平均值
median([1,2,3,4])           # 中位数
mode([1,2,2,3])             # 众数
variance([1,2,3])           # 方差
stdev([1,2,3])              # 标准差
clamp(15, 0, 10)            # 夹到区间内 → 10
is_prime(97)                # 是不是质数
gcd(12, 18)                 # 最大公约数
lcm(4, 6)                   # 最小公倍数
deep_sizeof(对象)            # 估算这个对象一共占多少字节
```

**关于 `fast_sum` 的诚实说明**：对普通 list，CPython 的 `sum()` 本身就有
整数专用快路径，用稳定 ABI 重写超不过它，实测略慢（9.2ms vs 15.3ms）。
`fast_sum` 真正的价值在传 `range` 时——直接走公式，1000 万次求和从 357ms 变成 0ms。

### 字符串（7 个）

```python
chunk(序列, 3)                    # 切成每 3 个一组
dedent("    a\n      b")          # 去掉公共缩进
is_palindrome("上海自来水来自海上")   # 回文判断（忽略标点和大小写）
slugify("Hello World!")           # → "hello-world"
snake_case("camelCaseName")       # → "camel_case_name"
truncate("abcdef", 4)             # → "abc…"
strip_ansi(带颜色的文本)            # 去掉终端颜色控制字符
```

### 其他（5 个）

```python
now()                    # 当前时间字符串
sleep(0.5)               # 睡 0.5 秒
timeit(函数, 参数)        # 测平均耗时 → (秒, 结果)
counter(列表)            # 计数 → {元素: 次数}
jit(函数)                # 把函数下沉到 C（见第三部分）
```

## 4. 命令行工具

```bash
python -m pythonpp                  # 交互式 REPL
python -m pythonpp script.py        # 运行脚本
python -m pythonpp -c "print(1)"    # 直接执行一段代码
python -m pythonpp --bench          # 跑基准测试
python -m pythonpp --info           # 环境自检
python -m pythonpp --help-builtins  # 列出所有内置函数
python -m pythonpp --trace 脚本.py   # 运行完顺带打印用时
python -m pythonpp --version        # 版本号
```

## 5. 中文报错

异常类型仍是**原始的 Python 异常**（`ZeroDivisionError` 还是 `ZeroDivisionError`），
只是额外挂了一个 `.pp_explained` 属性，里面是完整的中文说明。

```python
try:
    lang.run("1 / 0")
except ZeroDivisionError as e:
    print(e.pp_explained)    # 完整中文说明
    print(e)                 # 原始的英文信息也能拿到
```

支持的错误类型：`ZeroDivisionError`、`NameError`、`TypeError`、`ValueError`、
`KeyError`、`IndexError`、`AttributeError`、`ModuleNotFoundError`、
`FileNotFoundError`、`RecursionError`、`MemoryError`、`OverflowError`、
`IndentationError`、`SyntaxError` 等。

---

# 第三部分：它是怎么变快的

## 1. 实测数据

Python 3.12.14 / Windows x64，用 `python -m pythonpp --bench` 复现：

| 场景 | 原生 Python | Python++ | 倍数 |
|---|---|---|---|
| 2000 万次循环迭代（`@jit`） | 3143 ms | **32 ms** | **约 100x** |
| 1 到 1 亿求和 | 3242 ms | **约 0 ms** | O(1) 公式 |
| `sum(range(1000 万))` | 357 ms | **约 0 ms** | O(1) 公式 |
| 深度内存估算 | 15.2 ms | **4.1 ms** | 3.7x |
| 20 万次随机整数 | 175 ms | **146 ms** | 1.2x |
| 100 万整数 list 求和 | 9.2 ms | 15.3 ms | 0.6x（见上文说明） |

## 2. 三个层次的加速

### 第一层：C 内核（`_pythonpp_native.pyd`）

一个 180KB 的动态库，**稳定 ABI（`Py_LIMITED_API=3.8`）**：
同一个 `.pyd` 在 CPython 3.8 ~ 3.14 上都能直接加载，不需要为每个版本重编译。

提供：随机数、整数求和、点积、等差数列 O(1) 求和、深度 sizeof、
64 位哈希、整数循环虚拟机。

**装不上也不影响使用**：`pythonpp/_native.py` 会自动退化成纯 Python 实现，
接口和行为完全一致。想强制测试回退路径：

```bash
set PYTHONPP_NO_NATIVE=1
python test_pythonpp.py
```

### 第二层：C 级 JIT（`@jit`）

```python
from pythonpp import jit

@jit
def loop_sum(n):
    total = 0
    for i in range(n):
        total += i * 2
    return total

loop_sum(10)          # 太小，先走 Python（编译要 1 秒，不划算）
loop_sum(20_000_000)  # 自动编译成 DLL 并跑机器码
loop_sum.explain()    # 告诉你现在走的是哪条路
```

`explain()` 会给出类似这样的说明：

```
loop_sum 已编译成机器码（C 后端），编译耗时约 1 秒，缓存于 ...\pp_xxx.dll
```

或者，如果 C 编译不可用、退到了内置虚拟机：

```
loop_sum 走内置 C 虚拟机，已翻译成整数指令流（C 编译未启用：没有找到 C 编译器（pip install ziglang 即可））
```

**原理**：读函数源码 → `ast` 解析 → **只认纯整数子集** → 生成 C 源码 →
用 zig/gcc 编译成 DLL → `ctypes` 调用。

**正确性保证**：生成的 C 里每个算术都有溢出检查，一旦溢出/除零/步数超限，
立刻返回状态码，由外层用原函数重算。所以「快」永远不会以「错」为代价。

**支持**：`int` 变量与字面量、`+=` 等增强赋值、`for ... in range(...)`、
`while`、`if/elif/else`、`return`、`break/continue`、`and/or/not`、
`+ - * // % & | ^ << >>`、比较、三目表达式。

**不支持（自动回退，不会报错）**：浮点、字符串、容器、函数调用、生成器、
`try/except`、嵌套函数。

三种后端，按可用性自动挑选：

| 后端 | 什么时候用 | 速度 |
|---|---|---|
| `c` | 有 zig/gcc | 最快（机器码） |
| `vm` | 有 C 内核但没编译器 | 约 1.7x |
| `python` | 都没有 | 基准（不加速，但保证正确） |

### 第三层：语言层优化

`Lang.run()` 不再每次把所有变量搬进搬出。旧版每跑一次就 `pool.clear()`
然后对每个变量重算 `deep_sizeof`；新版只在变量真的变了时才更新，
并且 `deep_sizeof` 有 id 缓存（同一个大对象重复 add 不会重复遍历）。

## 3. MemPool：变量池

`Lang` 的变量存在 `MemPool` 里，默认 64MB，超了按 LRU 淘汰。

```python
from pythonpp import MemPool

pool = MemPool(max_bytes=1024 * 1024, high_watermark=0.8, low_watermark=0.5)
pool.add("x", 1)
pool.peek("x")        # 1
pool.takeout("x")     # 1（取出来并删除）
pool.stats()          # 统计信息
pool.keys()           # 所有变量名
```

`stats()` 返回的内容：

```
变量个数、已用字节、容量字节、占用比例、变量列表、缓存命中、淘汰次数
```

另一个入口（和旧版一致）：

```python
from pythonpp import Lang
L = Lang()
L.run("""
x = 1
y = 2
""")
print(L.ls())     # 列出变量
print(L.cat("x"))
```

## 4. 编译 C 内核

```bash
python build_native.py                # 自动找 zig/gcc/cl
python build_native.py --fetch-zig    # 没有编译器就自动下一个 zig（约 90MB）
python build_native.py --check        # 只检查现有产物
python build_native.py --clean        # 清理产物
```

不需要 Visual Studio，也不需要 CMake。**没有编译器也能跑**，只是慢一点。

`@jit` 找编译器的顺序：`PYTHONPP_CC` 环境变量 → `PP_ZIG` → PATH 里的
`zig` → 包目录下的 `.zig/` → `gcc` → `clang`。

## 5. 测试

```bash
python test_pythonpp.py
```

覆盖：随机数分布（卡方检验）、文件操作、MemPool 淘汰与缓存、Lang 执行、
错误翻译、JIT 正确性（**每个 JIT 函数都和纯 Python 逐项对比结果**）。

**109 项全部通过**，Python 3.12 与 3.14 上验证过。

如果环境里没有可用的编译器，JIT 相关检查会自动跳过并说明原因，
不会误报失败。

---

# 第四部分：参考

## 1. 目录结构

```
pythonpp/
├── __init__.py        对外 API
├── __main__.py        python -m pythonpp 入口
├── _core.py           Lang + MemPool
├── _native.py         C 内核加载 / 纯 Python 回退
├── _jit.py            @jit 装饰器（三后端调度）
├── _codegen.py        AST → C 源码
├── _cjit.py           C 源码 → 动态库 → ctypes
├── _jit_vm.py         AST → 内置虚拟机指令（无编译器时的后备）
├── _builtins.py       注入 Lang 的内置函数表
├── _rand.py           随机数
├── _io.py             文件操作
├── _calcs.py          数值计算
├── _strutil.py        字符串工具
├── _misc.py           杂项
├── _errors.py         中文报错
├── _bench.py          基准测试
├── _cli.py            命令行
├── pythonpp_native.c  C 内核源码（单文件）
└── _pythonpp_native.pyd  编译产物
build_native.py        构建脚本
test_pythonpp.py       测试
pythonpp.py            兼容层（旧入口）
pythonpp_v1_backup.py  旧版备份
```

## 2. 环境变量

| 变量 | 作用 |
|---|---|
| `PYTHONPP_NO_NATIVE=1` | 强制使用纯 Python 回退实现 |
| `PYTHONPP_CACHE` | JIT 编译缓存目录（默认在用户临时目录） |
| `PYTHONPP_CC` | 指定 JIT 用的 C 编译器命令 |
| `PP_ZIG` | 指定 zig 路径 |
| `PYTHONPP_JIT_MIN_WORK` | 触发编译的最小工作量（默认 20000） |

## 3. 常见问题

**Q：它会改变我原来的代码吗？**
不会。它不修改 Python 本身，也不改语法。你原来的 `.py` 文件照常运行。

**Q：我能直接在 Python++ 里用 numpy / pandas 吗？**
能。它就是 Python，正常 `import` 即可。

**Q：`@jit` 会算错吗？**
不会。所有算术都有溢出检查，一旦有风险就交回原函数重算。
测试里每个 JIT 函数都和纯 Python 逐项对比过结果。

**Q：为什么我加了 `@jit` 没变快？**
三种可能：① 你的函数不是纯整数计算（自动回退）；② 入参太小，
低于触发阈值（编译本身要 1 秒，不值得）；③ 没有编译器，退到了虚拟机后端。
用 `函数.explain()` 能看到具体是哪一种。

**Q：一定要装 C 编译器吗？**
不一定。没有编译器时，C 内核和 `@jit` 都会自动退到较慢但正确的实现。
想获得全部速度，`pip install ziglang` 是最省事的办法。

**Q：支持 Mac / Linux 吗？**
代码是跨平台的（C 内核用稳定 ABI），但目前只在 Windows x64 上完整验证过。

## 4. 版本与验证范围

- 当前版本：**1.0.0**
- Python 要求：**3.8+**

关于兼容性，说清楚哪些是验证过的、哪些只是设计目标：

| 项目 | 状态 |
|---|---|
| 在 Python 3.12 / 3.14 上跑完整测试 | **已实测**，109 项全过 |
| 单个 `.pyd` 同时被 3.12 和 3.14 加载 | **已实测** |
| 纯 Python 回退路径（`PYTHONPP_NO_NATIVE=1`） | **已实测**，109 项全过 |
| Python 源码兼容 3.8 语法 | 已用 `ast.parse(feature_version=3.8)` 静态检查通过 |
| 只依赖 3.8 就自带的标准库 | 已逐模块核对，无 3.9+ 专有 API |
| 在 3.8 ~ 3.11 上实际运行 | **未实测**（本机没有这些版本的解释器） |

结论：3.8 ~ 3.11 理论上兼容（语法、标准库、稳定 ABI 三条都对得上），
但请以实际运行结果为准。

## 5. 自己从源码构建

如果你想自己编译、自己打包：

```bash
python build_native.py            # 编译 C 内核（没编译器时加 --fetch-zig）
python test_pythonpp.py           # 自检
python setup.py bdist_wheel       # 打包
```

产物在 `dist/` 下，文件名形如 `pythonpp_plus-1.0.0-cp38-abi3-win_amd64.whl`。

`cp38-abi3` 表示「Python 3.8 及以上通用」，一个文件就能覆盖 3.8 ~ 3.14。
这个标签由 `setup.py` 里的 `get_tag()` 自动生成，不需要手动传参数 ——
传错了会打出 `cp312-cp312-...whl`，那就只有 3.12 能装了。
