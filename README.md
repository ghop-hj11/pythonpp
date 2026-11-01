# Python++ 1.2

> Python 你会多少，Python++ 就会多少。它不改变语法，只是在旁边放了一箱更好用的工具，
> 并且能在你需要的时候，把慢代码真正编译成机器码。

## 安装

**推荐用 GitHub Release 直链安装**（v1.2.0 起提供 5 个平台的 C 加速 wheel，
外加 3 个平台的自由线程无 GIL wheel）：

```bash
pip install https://github.com/ghop-hj11/pythonpp/releases/download/v1.2.0/pythonpp_plus-1.2.0-cp38-abi3-win_amd64.whl
```

也可以打开 [Releases 页面](https://github.com/ghop-hj11/pythonpp/releases) 挑对应文件下载后安装：

| 你的情况 | 下哪个文件 |
|---|---|
| Windows 64 位 | `pythonpp_plus-1.2.0-cp38-abi3-win_amd64.whl` |
| Linux 64 位 | `pythonpp_plus-1.2.0-cp38-abi3-manylinux2014_x86_64.whl` |
| Linux ARM64 | `pythonpp_plus-1.2.0-cp38-abi3-manylinux2014_aarch64.whl` |
| macOS Apple Silicon (M 系列) | `pythonpp_plus-1.2.0-cp38-abi3-macosx_11_0_arm64.whl` |
| macOS Intel | `pythonpp_plus-1.2.0-cp38-abi3-macosx_11_0_x86_64.whl` |
| **Python 3.14t（自由线程，无 GIL）** | `pythonpp_plus-1.2.0-cp314-cp314t-<平台>.whl` |
| 其它平台 / 只要功能 | `pythonpp_plus-1.2.0-py3-none-any.whl` |

> `cp314t` 的文件只能装在 3.14t（free-threaded）解释器里，
> 那边 `GIL=False` 才是**真正的多核并行**；普通 GIL 版解释器请用 `cp38-abi3` 的文件。

> **包名和导入名不一样**，这是唯一需要留意的点：
>
> | | 名字 |
> |---|---|
> | 安装文件里写的 | `pythonpp_plus` |
> | 代码里 `import` 的 | `pythonpp` |
>
> 为什么？因为 PyPI 上的 `pythonpp` 这个名字已经被另一个同名项目占用了，
> 只能换个发布名，但导入名保持 `pythonpp` 不变，你写的代码完全不用迁就它。

装好之后验证一下：

```python
import pythonpp
print(pythonpp._native.describe())
```

看到 `C 内核 1.2.0（稳定 ABI，...）` 就说明原生加速已经生效；
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
Python++ 1.1.0  ——  Python 语法 + C 加速
内核：C 内核 1.1.0（稳定 ABI，Python 3.12.14）
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
├── pythonpp\            ← 真正的实现，整个文件夹
├── pyproject.toml       ← 项目元数据（名称、版本、许可证）
├── setup.py             ← 打包（塞二进制 + 平台标签）
├── build_native.py      ← 重新编译 C 内核时用
├── README.md            ← 你正在看的这份
└── test_pythonpp.py     ← 自检：python test_pythonpp.py
```

**建议阅读顺序**：先把上面的"四种玩法"都试一遍 → 再看第二部分有没有需要的函数 →
最后有需要再研究第三部分的原理。

---

# 第二部分：完整功能说明

## 1. 兼容性：你的老代码不用改

旧版单文件 `pythonpp.py` 的**每一个 API 都还在**，原有代码一行都不用改：

```python
from pythonpp import Lang
lang = Lang()
print(lang.touch("test.txt"))
```

只是实现从「单文件」搬进了 `pythonpp/` 包，导入名 `pythonpp` 保持不变。

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

## 6. GIL=False：把全局解释器锁管住

一行开关，默认开启：

```python
import pythonpp as pp

pp.configure(gil=False)     # 等价于 GIL=False
pp.configure(gil=True)      # 恢复默认
print(pp.gil_status())      # 看清到底"并行"到什么程度
```

`gil_status()` 会如实告诉你四件事：

```python
{
  'configured_gil': False,          # 你配置的值
  'gil_disabled_by_config': True,   # 配置层已关闭
  'interpreter_gil': False,         # 解释器本身还有没有 GIL（3.14t 上才是 False）
  'interpreter_free_threaded': True,
  'atomics': True, 'channels': True,
  'release_gil': True,              # C 内核是否在长耗时处释放 GIL
  'parallelism': 'full'             # full / native-only / none
}
```

**先把边界说清楚**（这决定了你能期待什么）：

| 解释器 | `GIL=False` 的实际效果 |
|---|---|
| 标准 CPython 3.8 ~ 3.14 | GIL 无法在运行期删除。Python++ 做的是"自身运行时无锁 + 关键路径主动释放 GIL"，多线程能重叠 I/O、原生通道阻塞、JIT 机器码计算 |
| 自由线程 CPython 3.13t / 3.14t | C 内核声明 `Py_MOD_GIL_NOT_USED`，多线程跑 Python 是**真正的多核并行** |
| 没有 C 内核（纯 Python 回退） | 用 `threading.Lock` 提供同语义实现，功能一致，但没有硬件原子指令 |

### 机制一：原子操作 + 内存屏障

CPU 的 `lock xadd` / `ldaxr-stlxr` 这类指令本身就是不可分割的，
不需要操作系统层面的锁，也就不会出现"两个线程同时改一个变量、差一微秒就错乱"。

```python
counter = pp.AtomicInt(0)

counter.add(1)                  # 8 个线程 × 5 万次 = 400000，一次都不会丢
counter.load()
counter.exchange(5)
ok, now = counter.compare_set(5, 6)   # CAS：成功返回 (True, 新值)，失败返回 (False, 当前值)
counter.or_(0x10); counter.and_(0xFF); counter.xor_(1)
```

- 底层是 `__atomic_*`（GCC/Clang）与 `_Interlocked*`（MSVC），不是 Python 锁。
- 内存序可选：`RELAXED < ACQUIRE / RELEASE < ACQ_REL < SEQ_CST`，
  默认 `SEQ_CST`，怕写错就用默认值。

```python
pp.fence(pp.SEQ_CST)            # 显式内存屏障：挡住 CPU/编译器的指令重排
```

**为什么需要它**：CPU 和编译器为了提速会偷偷调整指令顺序。
下面的"先写数据、再置标志位"如果没有 `RELEASE`，别的线程可能先看到标志位、后看到数据：

```python
data = {}
flag = pp.AtomicInt(0)

# 生产者线程
data["k"] = 1
flag.store(1, pp.RELEASE)       # 保证：flag=1 之前，data 一定已经写进去了

# 消费者线程
if flag.load(pp.ACQUIRE) == 1:  # 保证：读到 1 之后，一定能看到完整的 data
    print(data["k"])
```

### 机制二：消息传递（Go 风格 Channel）

线程之间**不共享内存**，只把对象"递过去"，从根上避免资源抢夺：

```python
ch = pp.Channel(16)             # 有界缓冲 16 条；Channel(0) 表示无界

ch.put(obj)                     # 通道满时阻塞
ch.put(obj, timeout=0.5)        # 带超时，超时抛 TimeoutError
item = ch.get()                 # 通道空时阻塞
item = ch.get(0.05)             # 带超时
ch.close()                      # 关闭后仍可取完剩余消息，取空再取抛 ChannelClosed

for item in ch:                 # 迭代到关闭为止
    ...
```

阻塞等待时 C 内核会 `PyEval_SaveThread()` 释放 GIL，
所以在标准 CPython 上"一个线程等通道"也不会把整个解释器卡死。

```python
def worker(tasks, results):
    for t in tasks:
        results.put(t * t)

tasks, results = pp.Channel(0), pp.Channel(0)
...
pp.spawn(worker, [1, 2, 3], results)      # 起一个后台线程
pp.parallel_map(lambda x: x * x, range(100), workers=4)
```

### 机制三：JIT 执行期释放 GIL

`@jit` 编译出的机器码是做纯整数运算的，全程不碰 Python 对象。
所以 `GIL=False` 时，Python++ 会在这段机器码执行期间释放 GIL：

```python
pp.configure(gil=False)

@pp.jit
def loop_sum(n):
    total = 0
    for i in range(n):
        total += i
    return total

# 4 个线程同时在跑机器码：在 3.14t 上是真并行，在标准 CPython 上也不再互相卡 GIL
```

### 数据竞争 / 顺序问题：Python++ 给了你工具，责任还在你

开了 `GIL=False` 之后，**普通的 `x += 1` 不再自动安全**——这正是它快的原因。

| 场景 | 该用什么 |
|---|---|
| 多个线程累加同一个计数器 | `pp.AtomicInt` |
| 多个线程改同一个容器 | `pp.Channel` 传消息，或自己加 `threading.Lock` |
| 一个线程写、另一个线程读一份数据 | `store(..., pp.RELEASE)` + `load(..., pp.ACQUIRE)` |
| 需要严格顺序、又不想用锁 | `pp.fence(pp.SEQ_CST)` 把重排挡住 |

`Lang` 与 `MemPool` 的内部状态在 1.2 里已经全部走原子路径/加锁路径，
多个线程同时跑 `Lang.run()` / `MemPool.add()` 是安全的（测试里 8 线程 × 5 万次验证过）。

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
python build_native.py                       # 自动找 zig/gcc/cl，编译到本机平台
python build_native.py --fetch-zig           # 没有编译器就自动装一个 zig
python build_native.py --check               # 只检查现有产物
python build_native.py --clean               # 清理产物
python build_native.py --target x86_64-linux-gnu.2.17   # 用 zig 交叉编译 Linux
python build_native.py --target aarch64-macos           # 用 zig 交叉编译 macOS arm64
python build_native.py --free-threaded       # 给 3.13t / 3.14t 编译无 GIL 版本
```

自由线程版必须用**自由线程解释器**来跑这个脚本（通常会自动识别，加不加 `--free-threaded` 都一样），
并且构建时会强制传入 `-DPy_GIL_DISABLED=1` —— Windows 的 `pyconfig.h` 里没有这个宏，
不显式传就会按"带 GIL"的对象布局编译，运行期直接段错误。

不需要 Visual Studio，也不需要 CMake。**没有编译器也能跑**，只是慢一点。

产物命名按平台自动选择（稳定 ABI，3.8+ 通用）：

| 平台 | 产物 | 说明 |
|---|---|---|
| Windows | `pythonpp/_pythonpp_native.pyd` | 链 `python3.lib` 导入库 |
| Linux | `pythonpp/_pythonpp_native.abi3.so` | 不链 libpython，符号运行期解析 |
| macOS | `pythonpp/_pythonpp_native.abi3.so` | `-undefined dynamic_lookup`，同上 |

自由线程（`3.14t`）的产物按解释器后缀命名，两种 ABI 互不干扰：

| 平台 | 产物 |
|---|---|
| Windows | `pythonpp/_pythonpp_native.cp314t-win_amd64.pyd`（链 `python314t.lib`） |
| Linux | `pythonpp/_pythonpp_native.cpython-314t-x86_64-linux-gnu.so` |
| macOS | `pythonpp/_pythonpp_native.cpython-314t-darwin.so` |

`@jit` 找编译器的顺序：`PYTHONPP_CC` 环境变量 → `PP_ZIG` → PATH 里的
`zig` → 包目录下的 `.zig/` → `pip install ziglang` 装的 zig → `gcc` → `clang`。
（1.1 起支持自动发现 `pip install ziglang` 的 zig，不在 PATH 里也能用。）

## 5. 测试

```bash
python test_pythonpp.py
```

覆盖：随机数分布（卡方检验）、文件操作、MemPool 淘汰与缓存、Lang 执行、
错误翻译、JIT 正确性（**每个 JIT 函数都和纯 Python 逐项对比结果**），
以及 1.2 新增的并发正确性：8 线程 × 5 万次原子加不丢更新、ACQUIRE/RELEASE 可见性、
通道 FIFO / 超时 / 关闭语义、生产者消费者、多线程 MemPool、`GIL=False` 下并发 JIT 结果一致。

**144 项全部通过**：标准 CPython 3.14、自由线程 3.14t、纯 Python 回退（`PYTHONPP_NO_NATIVE=1`）
三种条件各跑一遍。

如果环境里没有可用的编译器，JIT 相关检查会自动跳过并说明原因，
不会误报失败。

---

# 第四部分：参考

## 1. 目录结构

```
pythonpp/
├── __init__.py        对外 API（含控制台编码兜底）
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
├── _gil.py            GIL 开关 / AtomicInt / Channel / spawn / parallel_map
├── _console.py        控制台 UTF-8 兜底
├── _bench.py          基准测试
├── _cli.py            命令行
├── pythonpp_native.c  C 内核源码（单文件，含原子操作与通道）
└── _pythonpp_native.pyd / .abi3.so / .cp314t-*.pyd   编译产物（按平台）
pyproject.toml         项目元数据
setup.py               打包（塞二进制 + 平台标签）
build_native.py        构建脚本
_publish_pypi.py       PyPI 发布脚本
PUBLISHING.md          发布指南
test_pythonpp.py       测试
```

## 2. 环境变量

| 变量 | 作用 |
|---|---|
| `PYTHONPP_NO_NATIVE=1` | 强制使用纯 Python 回退实现 |
| `PYTHONPP_CACHE` | JIT 编译缓存目录（默认在用户临时目录） |
| `PYTHONPP_CC` | 指定 JIT 用的 C 编译器命令 |
| `PP_ZIG` | 指定 zig 路径 |
| `PYTHONPP_JIT_MIN_WORK` | 触发编译的最小工作量（默认 20000） |

## 3. 内核诊断（1.1 新增，1.2 扩充）

排查「到底加载了哪个内核 / 是不是稳定 ABI / 有没有真并行」这类问题，不用再猜：

```python
from pythonpp import _native
print(_native.diagnostics())
# {'backend': 'native', 'is_native': True, 'loaded_from': '...\\_pythonpp_native.pyd',
#  'platform': 'win32', 'arch': 'x86_64', 'version': '1.2.0',
#  'limited_api': 0x30008, 'native_platform': 'windows', 'native_arch': 'x86_64',
#  'free_threaded': False, 'atomics': True, 'channels': True, 'release_gil': True}
```

命令行更直观：

```bash
python -m pythonpp --info
# 原生内核：native v1.2.0（windows/x86_64）
# 内核文件：...\_pythonpp_native.cp314t-win_amd64.pyd
# ABI 一致性：内核架构 x86_64 == 解释器架构 x86_64 ✓
# 配置的 GIL  ：关（GIL=False）
# 解释器 GIL  ：关（自由线程构建）
# 原子/通道   ：atomics=True channels=True
# 并行能力    ：full  —— 真正多核并行（自由线程解释器 + 无 GIL 内核）
```

命令行也支持直接关 GIL：

```bash
python -m pythonpp --nogil script.py
```

`--info` 会对比「内核编译目标架构」和「当前解释器架构」，不一致会直接标 ⚠，
避免加载到错误平台二进制时一头雾水。

另外 1.1 起，JIT 的磁盘缓存按「平台-架构」分层（如 `win32-x86_64/`），
同一个缓存目录被多台机器/容器挂载也不会串加载到别人的二进制。

## 4. 常见问题

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

**Q：`GIL=False` 之后我的老代码会不会出问题？**
不会自动出问题，但也不再有"免费的线程安全"。默认是 `gil=True`，
你不主动关就完全保持原状；关了之后，多个线程共享可变对象时要按
「第 6 节」的表格选原语（`AtomicInt` / `Channel` / `threading.Lock`）。

**Q：为什么我在普通 Python 上开了 `GIL=False`，多线程还是不快？**
因为 GIL 是解释器层的，第三方包删不掉它——除非你用 3.13t / 3.14t。
在标准 CPython 上，Python++ 能做到的是「自身运行时无锁 + 阻塞时释放 GIL +
JIT 机器码执行期间释放 GIL」，能加速 I/O 重叠、通道等待、纯整数机器码计算，
但**不会**让两段纯 Python `for` 循环同时占用两个核。这是 CPython 的物理限制，不是配置问题。

**Q：支持 Mac / Linux 吗？**
支持。C 内核用稳定 ABI（`Py_LIMITED_API=3.8`），Windows 上是 `.pyd`、
Linux / macOS 上是 `.abi3.so`，同一个内核源码在三个平台各自编译一份即可。
官方 Release 提供 Windows x64 / Linux x64 / Linux ARM64 / macOS Apple Silicon /
macOS Intel 五个预编译 wheel，`pip install` 即得 C 加速，无需本机装编译器。
加载器会按平台自动识别正确的后缀，找不到就退回纯 Python（功能不变，只是慢）。

## 5. 版本与验证范围

- 当前版本：**1.2.0**
- Python 要求：**3.8+**（自由线程功能需要 **3.13t / 3.14t**）

关于兼容性，说清楚哪些是验证过的、哪些只是设计目标：

| 项目 | 状态 |
|---|---|
| 在 Python 3.14（标准 GIL）上跑完整测试 | **已实测**，144 项全过 |
| 在 Python 3.14t（自由线程）上跑完整测试 | **已实测**，144 项全过 |
| 单个 `.pyd` 同时被 3.12 和 3.14 加载 | **已实测** |
| 纯 Python 回退路径（`PYTHONPP_NO_NATIVE=1`） | **已实测**，144 项全过 |
| Windows x64 原生内核（`.pyd` / `.cp314t-*.pyd`） | **已实测** |
| Linux x64 / ARM64 原生内核（`.abi3.so`） | 由 CI 在 ubuntu runner 上编译 + 测试 |
| macOS Apple Silicon / Intel 原生内核 | 由 CI 在 macos runner 上编译 + 测试 |
| Linux / macOS 自由线程内核 | 由 CI 在 setup-python `3.14t` 上编译 + 测试 |
| Python 3.9 / 3.12 / 3.14 跨版本兼容 | 由 CI 的三平台 × 三版本矩阵覆盖 |
| Python 源码兼容 3.8 语法 | 已用 `ast.parse(feature_version=3.8)` 静态检查通过 |
| 只依赖 3.8 就自带的标准库 | 已逐模块核对，无 3.9+ 专有 API |
| 在 3.8 ~ 3.11 上实际运行 | **未实测**（本机没有这些版本的解释器） |

结论：3.8 ~ 3.11 理论上兼容（语法、标准库、稳定 ABI 三条都对得上），
但请以 CI 与你的实际运行结果为准。

## 6. 自己从源码构建

如果你想自己编译、自己打包：

```bash
python build_native.py            # 编译 C 内核（没编译器时加 --fetch-zig）
python test_pythonpp.py           # 自检
python setup.py bdist_wheel       # 打包
```

产物在 `dist/` 下，文件名形如 `pythonpp_plus-1.2.0-cp38-abi3-win_amd64.whl`；
用自由线程解释器打包则是 `pythonpp_plus-1.2.0-cp314-cp314t-win_amd64.whl`。

`cp38-abi3` 表示「Python 3.8 及以上通用」，一个文件就能覆盖 3.8 ~ 3.14。
这个标签由 `setup.py` 里的 `get_tag()` 自动生成，不需要手动传参数 ——
传错了会打出 `cp312-cp312-...whl`，那就只有 3.12 能装了。

要发布到 GitHub / PyPI 的完整流程，见 [PUBLISHING.md](PUBLISHING.md)。
