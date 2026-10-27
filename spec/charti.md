# `charti_build_content` — 反编译规格说明 (macOS Swift 重写用)

> 来源: Python 3.11 字节码反汇编 `/tmp/toolx/dis/charti_build_content.py.txt` (1002 行)
> + marshalled code object `/tmp/toolx/raw/charti_build_content.py.bin`
> 原始文件 `charti_build_content.py`：模块级 4 个模板常量 (line 6/34/38/59)，
> 函数 `clean_name`(67), `twoname`(70), `process_info`(78), `process_file`(139), `generate_tex`(155)。
> 本文件按块递增撰写，未一次性读入全部反汇编。

---

## STEP 1 结果：模板常量映射 (已字节级提取)

用 `/tmp/uvpy/cpython-3.11.14-macos-aarch64-none/bin/python3.11` 从 raw code object 提取，
**不是**从反汇编文本手抄。

| 常量索引 | 名称 | 文件路径 | 字符长度 | MD5 |
|---|---|---|---|---|
| `co_consts[3]` | `ch_content_head` | `/tmp/toolx/charti_const_3.txt` | 675 | `aa162d919c9d5da41ad90693b5e8974e` |
| `co_consts[4]` | `ch_content_end` **和** `en_content_end` | `/tmp/toolx/charti_const_4.txt` | 31 | `e1d16e4d7b4b32658135533179f7c8b4` |
| `co_consts[5]` | `en_content_head` | `/tmp/toolx/charti_const_5.txt` | 565 | `1502445bf04e8e7ad6b26392752fe9cb` |

**关键发现**：`en_content_end` **不是**独立常量。模块级字节码在第 38 行把 const 5
存给 `en_content_head`，随后第 59 行 `LOAD_CONST 4` 把**同一个** const 4 再次
`STORE_NAME en_content_end`。因此中英文结束模板**完全等价**（同一对象，
`en_content_end is ch_content_end` 为 `True`）。

模块级 LOAD_CONST 引用（字节码 line 1–59）证实：

```
  6  LOAD_CONST 3  -> STORE_NAME ch_content_head
 34  LOAD_CONST 4  -> STORE_NAME ch_content_end
 38  LOAD_CONST 5  -> STORE_NAME en_content_head
 59  LOAD_CONST 4  -> STORE_NAME en_content_end      # 复用 const 4
 67  LOAD_CONST 6  -> MAKE_FUNCTION -> clean_name
 70  LOAD_CONST 7  -> MAKE_FUNCTION -> twoname
 78  LOAD_CONST 8  -> MAKE_FUNCTION -> process_info
139  LOAD_CONST 9  -> MAKE_FUNCTION -> process_file
155  LOAD_CONST 10 -> MAKE_FUNCTION -> generate_tex
```

### 三个可提取模板的首 3 行 / 末 3 行

**`/tmp/toolx/charti_const_3.txt` (ch_content_head)** — 首 3 行：
```
(空行)
\documentclass[b5paper,11pt,onecolumn,twoside]{ctexart}
\pagestyle{empty}
```
末 3 行：
```
{\large \bf{专栏：XXX}}
&&\textit{\large 主持人：XXX}
\\[5.0 ex]
```

**`/tmp/toolx/charti_const_4.txt` (ch_content_end == en_content_end)** — 首 3 行 (全文仅 3 行)：
```
(空行)
\end{tabularx}
\end{document}
```
末 3 行：与首 3 行相同（全文就 3 行）。

**`/tmp/toolx/charti_const_5.txt` (en_content_head)** — 首 3 行：
```
(空行)
\documentclass[b5paper,10pt,onecolumn,twoside]{ctexart}
\pagestyle{empty}%不显示页码
```
末 3 行：
```
\noindent\hrulefill{\rmfamily} \vspace{1.3ex}\\
\begin{tabularx}{\textwidth}{l@{\hspace{-1.2em}}r}
```
（注意：const 5 的**末行即表格起始行，其后没有换行符** —— 文件以 `r}` 结尾无 `\n`。
const 3 末行以 `\\[5.0 ex]\n` 结尾**有**换行符。）

### STEP 1b 现值台账（2026-10-27 模板升级后）

上表是 **2022 版 Windows 原版**的提取值（历史台账，保留溯源）。本仓内置模板此后经两次升级，
与 Windows 原版**不再逐字节一致**：`sources-mac/Features/ContentTemplates.swift` 是唯一源，
`Engine/slcn/content_templates.py` 由 `Engine/tools/gen_content_templates.py` 生成（生成物禁手改）。

| 模板 | Windows 原版提取（上表） | 现值 | 差异 |
|---|---|---|---|
| `ch_content_head` | 675 字符 / `aa162d91…` | **771 字符** | 升级为 2026 年第 3 期（88）中文版式：补 `fontset=windows`；刊名/卷期/出版日期改 88 期值；栏目行改占位 `XXX` |
| `en_content_head` | 565 字符 / `1502445b…` | **713 字符** | 升级为 88 期英文版式：补 `fontset=windows`；加 `amssymb`；页眉改 `Volume 19, Number 3, June 2026`；栏目行改占位 `XXX` |
| `ch_content_end`＝`en_content_end` | 31 字符 / `e1d16e4d…` | **31 字符（未变）** | — |

现值校验口径：① `Engine/tests/test_infocheck_toc.py` 的 `(n1)(n2)` 常驻断言 Swift 源 ↔ 生成物逐字节一致；
② golden 快照 `Engine/tests/fixtures/golden_toc/Chinese Content_GOLDEN.tex`（959 字节）与
`contents_GOLDEN.tex`（858 字节）；③ `Engine/tools/gen_content_templates.py --check`（不同步退出 1）。

---

## 2.1 `clean_name(name)` — 源 line 67-68

反汇编 (dis 行 54-82)。重建源码：

```python
def clean_name(name):
    return ''.join(filter(lambda x: '一' <= x <= '鿿', name))
```

字节码证据：
- line 68: `LOAD_CONST ''` + `LOAD_METHOD join` + `LOAD_GLOBAL filter` +
  `MAKE_FUNCTION 0`(lambda) + `LOAD_FAST name` + `CALL 2` + `CALL 1` + `RETURN_VALUE`。
  即 `'' . join( filter(<lambda>, name) )`。
- `<lambda>` (源 line 68 同一行): `LOAD_CONST '一'` / `LOAD_FAST x` / `SWAP 2` /
  `COPY 2` / `COMPARE_OP 1 (<=)` / `JUMP_IF_FALSE_OR_POP` / `LOAD_CONST '鿿'` /
  `COMPARE_OP 1 (<=)` —— 这是 CPython 对**链式比较 `'一' <= x <= '鿿'`** 的标准编译形式。
  即 `lambda x: '一' <= x <= '鿿'`。

**语义**：逐**字符**（Unicode 码位）过滤，只保留码位落在
`U+4E00`(一) ≤ c ≤ `U+9FFF`(鿿, CJK Unified Ideographs 结束) 的字符。
其余一切（ASCII 字母、空格、标点、括号、数字、全角标点如 `，` `（` 等）**全部丢弃**。
注意 `一` 是 U+4E00，`鿿` 是 U+9FFF —— 恰为 CJK 基本区首尾。
标点 `，`(U+FF0C)、`。`(U+3002)、`：`(U+FF1A) 属于 U+3000/U+FF00 区段，
**不在此范围内，会被剥除**。数字 0-9 也会被剥除。

⚠️ 注意这是**范围比较**而非白名单，扩展区汉字（如 U+3400 或 U+20000 以上）不保留。

## 2.2 `twoname(name)` — 源 line 70-75

反汇编 (dis 行 84-113)。重建源码：

```python
def twoname(name):
    name = clean_name(name)
    if len(name) == 2:
        return name[0] + '\hspace{1em}' + name[1]
    return name
```

字节码证据：
- line 71: `clean_name(name)` → `STORE_FAST name`（**重绑定参数**，原名被覆盖）
- line 72: `len(name)` `COMPARE_OP 2 (==)` `LOAD_CONST 2` → `POP_JUMP_FORWARD_IF_FALSE 110`
- line 73: `name[0]`(LOAD_CONST 0) `+ '\hspace{1em}'`(const 3) `+ name[1]`(LOAD_CONST 1) → RETURN
- line 75 (target 110): `LOAD_FAST name` → RETURN

**关键细节**：
- 判别条件严格是 `len == 2`（**不是** `<= 2`，`== 3` 也直接返回）。只有**恰好两字**才插入。
- `'\hspace{1em}'` 在 Python 源码里是普通字符串 `'\\hspace{1em}'`；由于 `\h` 不是
  合法转义，Python 保留反斜杠并给出 DeprecationWarning（3.12 起为 SyntaxWarning）。
  实际运行时值是 **字面 12 字符**：`\hspace{1em}`（反斜杠 + hspace{1em}）。
  Swift 中必须写作 `"\\hspace{1em}"`。
- 插入位置：**第一字与第二字之间**。两字名 `张三` → `张\hspace{1em}三`。
- 三字名 `张三丰` 原样返回（不加任何东西）；四字名同理原样返回。
- 入参若真为 2 字符但含非汉字，会先被 `clean_name` 剥成 0/1/2 字，再判断。
---

## 3. `process_info(info, flag)` — 输入解析规则 (源 line 78-137)

### 局部变量初始化 (bytecode line 79-83)
```python
page = ''                       # LOAD_CONST '' STORE_FAST page
engname1, engname2, engname3 = '', '', ''    # const 2 UNPACK_SEQUENCE 3
zhname1, zhname2, zhname3 = '', '', ''       # const 2 UNPACK_SEQUENCE 3
zhtitle = ''                    # 中文标题
entitle = ''                    # 英文标题
```
⚠️ 元组解包顺序（UNPACK_SEQUENCE 语义为从左到右 pop）：
`engname1=''`, `engname2=''`, `engname3=''`；zh 同理。

### 主循环 (bytecode line 84)
```python
for line in info:               # LOAD_FAST info; GET_ITER; FOR_ITER -> 870
```
`info` 是**可迭代的行序列**（由 `process_file` 传入 `readlines()` 的结果，
**每行末尾带 `\n`**，这一点对下面的 `line[7:]` 切片至关重要）。

### 逐行匹配规则（顺序 if/elif 链，用 `CONTAINS_OP 0` = `in` 子串判断）

| 源行 | 判据 `X in line` | 动作 |
|---|---|---|
| 85 | `'起始页码：'` | `page = int(line.split()[0][5:].strip())` |
| 87 | `'第一作者英文:'` | `engname1 = line[7:].strip()` |
| 89 | `'第一作者中文:'` | `zhname1 = twoname(line[7:].strip())` |
| 91 | `'第二作者英文:'` | `engname2 = line[7:].strip()` |
| 93 | `'第二作者中文:'` | `zhname2 = twoname(line[7:].strip())` |
| 95 | `'第三作者英文:'` | `engname3 = line[7:].strip()` |
| 97 | `'第三作者中文:'` | `zhname3 = twoname(line[7:].strip())` |
| 100 | `'中文标题:'` | `zhtitle = line.split('中文标题:')[-1].strip()` |
| 102 | `'英文标题'`（**无冒号**） | `entitle = line.split('英文标题:')[-1].strip()` |

**注意区分**：line 100 用 `'中文标题:'`（**带**全角冒号），line 102 用
`'英文标题'`（**不带**冒号，故 `英文标题：`/`英文标题:` 都能命中），
但 subsequent split 用的是 `'英文标题:'`（**半角冒号**）。
若输入写成全角 `英文标题：`，`split('英文标题:')` **不匹配**，`[-1]` 返回整行，
结果 `entitle` 会包含 "英文标题：" 前缀 —— 这是一个**潜在 bug**，移植时须原样保留或明确修正。

### 起始页码 (bytecode line 86) —— 最微妙的一行
```python
page = int(line.split()[0][5:].strip())
```
字节码展开：
```
LOAD_GLOBAL int
LOAD_FAST line
LOAD_METHOD split          # line.split()  —— 无参数，按任意空白切分
CALL 0
LOAD_CONST 4 (0)           # [0]
BINARY_SUBSCR
LOAD_CONST 5 (5) / LOAD_CONST 0 (None)   # BUILD_SLICE 2  =>  [5:]
BINARY_SUBSCR
LOAD_METHOD strip
CALL 0
CALL 1  (int)
STORE_FAST page
```
层层含义：
1. `line.split()` —— 空白切分（空格/制表/Tab），生成列表。
2. `[0]` —— 取**第一个 token**。因为输入行形如
   `起始页码：23 页码：45`（或类似），第一个 token 即 `起始页码：23`。
3. `[5:]` —— 从**第 6 个字符**（索引 5）开始切片。`'起始页码：'` 正好是
   **5 个字符**（起始/页/码/：= 4 个汉字 + 全角冒号 = 5 字符，UTF-16/码位长度 5）。
   所以 `[5:]` 剥掉前缀，保留 `23`。
4. `.strip()` 清多余空白后 `int()` 转整数。

⚠️ **`page` 初始为 `''`**；若循环中**没有任何行**命中 `'起始页码：'`，
`page` 保持字符串 `''`，随后在 `format` 中会被插成空串（**不会报错**，
因为 `str.format` 接受任何对象）。`int()` 只在命中分支中调用。

⚠️ 切片是**按 Python `str` 的码位（code point）**索引，`[5:]` 是 5 个字符，
与 UTF-8 字节数无关。Swift 中 `String` 默认非整数下标，必须用
`String(line.dropFirst(5))`（**字符**语义）而不是 `line.utf8.dropFirst(5)`。

### 作者行切片 `line[7:]`
`'第一作者英文:'` 等标签均为 **7 个字符**（4 汉字 + 全角冒号 + ... 实际计：
第/一/作/者 + `英文`/`中文`(2 汉字) + `:`(1 半角) = 7 字符）。
字节码 `LOAD_CONST 7 / LOAD_CONST None / BUILD_SLICE 2` = `line[7:]`。
注意这些作者标签用的是**半角冒号 `:`**，而 `起始页码：`/`中文标题：` 用**全角 `：`**。
---

## 4. `process_info` 作者分支与标题清洗 (bytecode line 106-137)

### 4.1 作者数量分支 (bytecode line 106-114)

```python
if zhname2 and zhname3:                                  # 三人
    authors    = '&\\textit{{\\large {}，{}，{}}}'.format(zhname1, zhname2, zhname3)
    en_authors = '\\textit{{{}, {}, {}}}'.format(engname1, engname2, engname3)
elif zhname2:                                            # 两人
    authors    = '&\\textit{{\\large {}，{}}}'.format(zhname1, zhname2)
    en_authors = '\\textit{{{}, {}}}'.format(engname1, engname2)
else:                                                    # 一人
    authors    = '&\\textit{{\\large {}}}'.format(zhname1)
    en_authors = '\\textit{{{}}}'.format(engname1)
```

**判据是 `zhname2` / `zhname3` 的真值（非空字符串）**，而非英文名。
字节码：`LOAD_FAST zhname2; POP_JUMP_FORWARD_IF_FALSE 972` 然后
`LOAD_FAST zhname3; POP_JUMP_FORWARD_IF_FALSE 972` —— 两者皆真才走三分支。

注意中文模板常量含 `{{` `}}` 转义（`'\textit{{...}}'` 里的 `\t` 是**制表符**吗？
→ **不是**。见下方 ⚠️）。

⚠️ **重要 Python 转义陷阱（双向）**：
- const 17 = `'&\\textit{{\\large {}，{}，{}}}'` —— 源码中写的是 `'&\\textit{{\\large {}，{}，{}}}'`，
  运行时值 = `&\textit{\large {}，{}，{}}`（`\\` → 单个 `\`，`{{`→`{`，`}}`→`}`）。
  **中文作者分隔符是全角逗号 `，`(U+FF0C)**。
- const 18 = `'\\textit{{{}, {}, {}}}'` —— **英文作者分隔符是半角 `, `（逗号+空格）**。
  运行时值 = `\textit{{}, {}, {}}`。
- 一人/两人版本同理（const 19/20/21/22）。

### 4.2 标题清洗 (bytecode line 116-117)

```python
zhtitle = zhtitle.replace('\\', '').replace('\\Large', '').replace('\\large', '').replace('{', '').replace('}', '').strip()
entitle = entitle.replace('\\', '').replace('\\Large', '').replace('\\large', '').strip()
```

字节码逐项（zhtitle，line 116, 5 个 replace + strip）：
`LOAD_CONST 23 ('\\\\')` → 运行时值 `\\`（2 字符 = 两个反斜杠）
`LOAD_CONST 24 ('\\Large')` → 运行时值 `\Large`（6 字符）
`LOAD_CONST 25 ('\\large')` → 运行时值 `\large`（6 字符）
`LOAD_CONST 26 ('{')` → `{`
`LOAD_CONST 27 ('}')` → `}`

⚠️ **顺序陷阱**：先 replace **两个反斜杠**（`\\`，LaTeX 换行符），
**再** replace `\Large` / `\large`。所以 `\Large`（单反斜杠）确实会被第二步清掉，
但输入中的 `\\Large`（双反斜杠）会在第一步被吃掉成 `Large`，第二步就不再匹配 ——
`Large` 字样会残留。这是真实行为，移植时必须**按同样顺序**执行。

`entitle` (line 117) 只有 **3 个 replace**（`\\`、`\Large`、`\large`）+ strip，
**不清除花括号** —— 与中文不同（中文额外清 `{` `}`）。

### 4.3 输出模板：`flag == 'ch'` 分支 (bytecode line 119-127)

```python
if flag == 'ch':
    if '——' in zhtitle:
        zhtitle1, zhtitle2 = zhtitle.split('——')
        zhtitle1 = zhtitle1.strip()
        zhtitle2 = '~~~~——' + zhtitle2.strip()
        return f'{{\\large {zhtitle1}}}\n{authors}\n& {page}\\\\\n{{\\large {zhtitle2}}}\n\n\\vdistance\n\n'
    return f'{{\\large {zhtitle}}}\n{authors}\n& {page}\\\\\n\n\\vdistance\n\n'
```

⚠️ **`zhtitle.split('——')` 是 `UNPACK_SEQUENCE 2`** —— 若标题含**两个及以上** `——`，
Python 抛 `ValueError: too many values to unpack`，该行**不做任何 try/except 保护**，
异常会向上传播到 `generate_tex` 的 try 块。

BUILD_STRING 9 段拼接（line 124-125）精确顺序：
`'{\\large '` + zhtitle1 + `'}\n'` + authors + `'\n& '` + page +
`'\\\\\n{\\large '` + zhtitle2 + `'}\n\n\\vdistance\n\n'`
→ 运行时字符串即 `{\large <T1>}\n<authors>\n& <page>\\\n{\large <T2>}\n\n\vdistance\n\n`
（其中 `\\\n` 是字面 2 个反斜杠 + 换行；`\vdistance` 是 `\` + `vdistance`）

**注意 line 123**：`zhtitle2 = '~~~~——' + zhtitle2.strip()`，即副标题前加
**4 个波浪号 + 破折号对**（`~~~~——`，4 个 `~` 加 2 个 `—`）。

无 `——` 时 (line 127) BUILD_STRING 7 段：省略副标题，且结尾是
`'\\\\\n\n\\vdistance\n\n'`（比副标题版**少一个 `{\large ...}` 段**）。

### 4.4 输出模板：`flag == 'en'` 分支 (bytecode line 128-136)

```python
if flag == 'en':
    if '---' in entitle:
        entitle1, entitle2 = entitle.split('---')
        entitle1 = entitle1.strip()
        entitle2 = '---' + entitle2.strip()
        return f'\\large {entitle1}\\\n\\large ~~~~---{entitle2}\\\n{en_authors}\n& {page}\\\\\n\n\n\\vdistance\n\n'
    return f'\\large {entitle}\\\n{en_authors}\n& {page}\\\\\n\n\\vdistance\n\n'
return None
```

英文分隔符是 **半角三连字符 `---`**（const 38），对应中文的 `——`（const 29）。
副标题前缀是 `'---' + strip()`（line 132，**不含**波浪号），
而模板里 BUILD_STRING 的常量段 const 40 = `'\\\\\n\\large ~~~~---'`
（运行时 `\\\n\large ~~~~---`）—— 即波浪号由**模板常量**提供，不是拼在 entitle2 上。
这与中文分支不同（中文的 `~~~~——` 是拼在 zhtitle2 上的）。

**无 `---` 时** (line 136) 结尾 `'\\\\\n\n\\vdistance\n\n'`；有 `---` 时
(line 133) 结尾 `'\\\\\n\n\n\\vdistance\n\n'`（**三个** `\n`）。

### 4.5 兜底
```python
return None       # bytecode line 128 target 1966: LOAD_CONST None; RETURN_VALUE
```
`flag` 既非 `'ch'` 也非 `'en'` 时返回 `None`。
---

## 5. `process_file(path, flag)` — 生成器 (源 line 139-153)

反汇编首指令是 `RETURN_GENERATOR` (line 552) —— **这是一个生成器函数**，
用 `yield` 逐个吐出每个条目的 LaTeX 片段。重建源码：

```python
def process_file(path, flag):
    with open(path, 'r', encoding='utf-8') as file:
        content = file.readlines()

    start_idx = 0
    end_idx = 0

    for i, line in enumerate(content):
        if '起始页码' in line and start_idx == 0:
            start_idx = i
        elif '起始页码' in line:
            end_idx = i - 1
            yield process_info(content[start_idx:end_idx + 1], flag)
            start_idx = i

    yield process_info(content[end_idx:], flag)
```

### 关键点
1. **`open(path, 'r', encoding='utf-8')`** (line 140)：文本模式 + 显式 UTF-8。
   字节码 `KW_NAMES 3` 对应 `co_consts[3] = ('encoding',)`，参数顺序
   `(path, 'r')` + 关键字 `encoding='utf-8'`。
   **没有 `newline=` 参数** → 使用 Python 通用换行（universal newlines），
   `\r\n` 会被**翻译成 `\n`**（见 §8 移植注意）。
   `with` 块保证关闭（字节码 BEFORE_WITH / WITH_EXCEPT_START）。
2. **`start_idx == 0` 哨兵**：`start_idx` 初值 `0`，用作"尚未找到第一条"的标志。
   ⚠️ **潜在 bug**：若第一条 `起始页码` 恰好出现在**第 0 行**，
   `start_idx` 被赋 `0`，与哨兵值相同，下一条 `起始页码` 会**再次**进入第一个分支
   而不是第二个 —— `end_idx` 不被更新，该轮不 yield。
   （实测常见布局下（报告首行是标题行，首条记录不在第 0 行）
   恰好得到一一对应、无重复无遗漏的结果，见 §12；但这是巧合性平衡。）
   移植若"修正"此行为会改变输出，须谨慎。
3. **两个 `CONTAINS_OP` 用的是 `'起始页码'`（无全角冒号）** (const 5，line 145/147)，
   比 `process_info` 里用的 `'起始页码：'`（**带**冒号，const 3）**宽松**。
   因此定位分隔行与解析页码行的判据**不完全一致**。
4. **切片 `content[start_idx:end_idx + 1]`** (line 150)：
   `BUILD_SLICE 2`，起点 `start_idx`，终点 `end_idx + 1`（字节码最后是
   `LOAD_FAST end_idx; LOAD_CONST 1; BINARY_OP 0 (+)`）。
   由于 `end_idx = i - 1`，其实际效果是 `content[start_idx:i]` —— **左闭右开，
   不含当前这条分隔行**。当前分隔行会成为下一条记录的起始行。
5. **尾部残留** (line 153)：循环结束后再 `yield process_info(content[end_idx:], flag)`，
   起点是 **`end_idx`（不是 `start_idx`）** —— 即最后一条 `起始页码` 行的**前一行**。
   ⚠️ 这会把最后一条分隔行**前一行**（通常是上一条记录的最后一行，如空行）
   也纳入最后一次解析输入（通常无害，因为该行不含任何被识别的字段标签）。
   当文件只有一条记录时 `end_idx` 仍为 `0`，尾部调用即 `content[0:]`
   （整个文件），该唯一记录由此产出。
   ⚠️ 由于 `end_idx = i - 1`（比理想值少 1）而末尾又用 `content[end_idx:]`
   （比理想值早 1），两处偏差**相互抵消**；在"每条记录前有空行"的常见布局下
   结果恰好正确（实测 3 条记录 → 3 个 yield，无重复无遗漏，见 §12）。
   但布局稍有变化即会失配 —— **移植时必须逐字复刻，不要"修正"**。
6. **异常表** (line 663-666)：仅覆盖 `with open(...)` 的清理逻辑，**无用户级 try**。
   文件不存在 → `FileNotFoundError` 直接抛出。

### 输入文件期望结构
输入是「检查tex信息」功能产出的**报告 txt**，由若干条记录串联，
每条记录以一行含 `起始页码` 的行**开头**（该行的存在同时意味着上一条记录结束），
例如（示意）：

```
起始页码：23
第一作者英文:Zhang San
第一作者中文:张三
第二作者英文:Li Si
第二作者中文:李四
中文标题:逻辑与论证
英文标题:Logic and Argument

起始页码：45
第一作者英文:Wang Wu
第一作者中文:王五
中文标题:—— 否定问题研究
英文标题:On Negation---A Study
```

**精确的字段布局**（`process_info` 实际读取的键）：
- `起始页码：<整数>` —— **全角冒号**；同一行若含其他空白分隔内容，
  只取第一个 token 的第 6 个字符起（`line.split()[0][5:]`）。
- `第一作者英文:` / `第一作者中文:` —— **半角冒号**，各取 `line[7:]`
- `第二作者英文:` / `第二作者中文:`（可选）
- `第三作者英文:` / `第三作者中文:`（可选）
- `中文标题:` —— **全角冒号**，取 `line.split('中文标题:')[-1].strip()`
- `英文标题:` / `英文标题：` —— 判据 `'英文标题' in line`（无冒号），
  但切分用 `split('英文标题:')`（**半角冒号**）

字段顺序在 `process_info` 中**不敏感**（逐行独立 `if/elif`），
但**记录边界**敏感，且**每条记录必须恰有一行含 `起始页码`**。
---

## 6. `generate_tex(path, flag, out_txt)` — 源 line 155-193

重建源码：

```python
def generate_tex(path, flag, out_txt):
    date_str = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    if flag == 'ch':
        run_info(out_txt, '开始生成中文目录')
        try:
            tex_path = os.path.abspath(
                os.path.join(os.path.dirname(path),
                             'Chinese Content_{}.tex'.format(date_str)))
            with open(tex_path, 'w', encoding='utf-8') as file:
                file.write(ch_content_head)
                file.write('\n\n%中间主体\n\n')
                for info in process_file(path, flag):
                    file.write(info)
                file.write(ch_content_end)
            run_info(out_txt, '中文目录TeX文件已生成\n')
        except Exception as e:
            run_info(out_txt, e)
            run_info(out_txt, '中文目录TeX文件生成失败\n')

    elif flag == 'en':
        run_info(out_txt, '开始生成英文目录')
        try:
            tex_path = os.path.abspath(
                os.path.join(os.path.dirname(path),
                             'contents_{}.tex'.format(date_str)))
            with open(tex_path, 'w', encoding='utf-8') as file:
                file.write(en_content_head)
                file.write('\n\n%中间主体\n\n')
                for info in process_file(path, flag):
                    file.write(info)
                file.write(en_content_end)
            run_info(out_txt, '英文目录TeX文件已生成\n')
        except Exception as e:
            run_info(out_txt, e)
            run_info(out_txt, '英文目录TeX文件生成失败\n')
```

字节码证据要点：
- line 156: `LOAD_GLOBAL datetime / LOAD_ATTR datetime / LOAD_METHOD now / CALL 0 /
  LOAD_METHOD strftime / LOAD_CONST '%Y-%m-%d_%H-%M-%S' / CALL 1` → `date_str`
- line 157 `flag == 'ch'`；line 177 `flag == 'en'`；
  两者皆否 → line 1548 `LOAD_CONST None; RETURN_VALUE`（**静默返回 None，不报错**）
- line 159/179 是 `NOP`（`try:` 语句的标记指令）
- `os.path.abspath(os.path.join(os.path.dirname(path), <模板>.format(date_str)))`
  —— 三层嵌套调用顺序由字节码 `abspath(join(dirname(path), ...))` 确认

## 7. 输出文件名、时间戳、目录、写入顺序、日志

### 输出目录
`os.path.dirname(path)` —— **与输入报告 txt 同一个目录**，再用 `os.path.abspath` 规范化。
不新建子目录，不检查目录是否存在（不存在则 `open` 抛异常）。

### 文件名与时间戳
- 时间戳格式：**`%Y-%m-%d_%H-%M-%S`**（strftime），例如 `2024-03-15_09-07-42`。
  共三段：`年-月-日` + `_` + `时-分-秒`。
  ⚠️ **时间部分用连字符而非冒号**（`%H-%M-%S` 而非 `%H:%M:%S`），
  因为 Windows 文件名不允许 `:`。macOS 上 HFS+/APFS 会把文件名中的 `:`
  显示/转换为 `/`，所以保持连字符同样是正确选择。
  移植时保持原格式即可。
- 中文：**`Chinese Content_<date_str>.tex`**（常量 `'Chinese Content_{}.tex'`）
  ⚠️ 文件名中**含一个空格**（`Chinese` 与 `Content` 之间），不是下划线。
- 英文：**`contents_<date_str>.tex`**（常量 `'contents_{}.tex'`）
  ⚠️ 全小写 `contents`。

**两个文件的 `date_str` 是同一次调用中生成的**（line 156 在分支之前），
所以 `ch` 与 `en` 分别调用时时间戳**不同**（各自调用一次 `generate_tex`）。

### 写入顺序（严格）
对 `flag == 'ch'`（`en` 完全对称，仅替换 head/end 常量）：
1. `ch_content_head`                       (line 163)
2. `'\n\n%中间主体\n\n'`                    (line 164) —— 字面 9 字符，前后各 2 个换行
3. 循环 `for info in process_file(path, flag): file.write(info)`  (line 166-167)
   —— 每次写入 `process_info` 返回的一个**条目片段字符串**
4. `ch_content_end`                        (line 169)

⚠️ 所有写入都在 `with open(..., 'w', encoding='utf-8')` 内，
**没有显式 `flush()`**；退出 `with` 时关闭并落盘。
`write` 的返回值被 `POP_TOP` 丢弃（不检查写入字节数）。

### 全部日志消息（`run_info(out_txt, msg)`）
| 触发点 | 消息 |
|---|---|
| flag=='ch' 进入 | `'开始生成中文目录'`（**无**结尾换行） |
| 中文成功 | `'中文目录TeX文件已生成\n'`（**带**换行） |
| 中文异常 e | `run_info(out_txt, e)` —— 直接传**异常对象**（非字符串） |
| 中文失败 | `'中文目录TeX文件生成失败\n'`（带换行） |
| flag=='en' 进入 | `'开始生成英文目录'`（无换行） |
| 英文成功 | `'英文目录TeX文件已生成\n'` |
| 英文异常 e | `run_info(out_txt, e)` |
| 英文失败 | `'英文目录TeX文件生成失败\n'` |

`run_info(out_txt, ss)` 实现为 `out_txt.insert('end', ss); out_txt.update(); out_txt.see('end')`
—— 即向 Tkinter Text 控件尾部插入文本。**传入异常对象 e 而非 `str(e)`**，
Tkinter 会对其调用 `str()`，所以界面上看到的是异常消息文本。

## 8. ★ macOS 移植注意

1. **`\r\n` vs `\n`（最重要）**
   - 读入：`open(path, 'r', encoding='utf-8')` **未指定 `newline=`** → Python
     启用 universal newlines，Windows 的 `\r\n` 被**翻译为 `\n`**。
     因此 `readlines()` 得到的行以 `\n` 结尾，**不含 `\r`**。
     后续所有 `line[7:]`、`.strip()` 因此不受 `\r` 干扰。
     Swift 的 `String(contentsOf:encoding:)` **不做**换行规范化；
     若直接 `components(separatedBy: "\n")`，Windows 报告的行尾会**残留 `\r`**，
     导致 `strip()` 等价的 `trimmingCharacters(in: .whitespacesAndNewlines)` 之外，
     `line[7:]` 切片结果末尾多一个 `\r`，进而污染 `.tex` 输出（LaTeX 可能报错或换行异常）。
     **必须显式处理**：先 `replacingOccurrences(of: "\r\n", with: "\n")` 再按 `\n` 切分，
     或统一 `trimmingCharacters(in: .whitespacesAndNewlines)`。
   - 写出：Python 在 Windows 上 `open(..., 'w')` 会把 `\n` 翻译为 `\r\n`；
     **在 macOS 上写出的是纯 `\n`**。所以本程序在 macOS 上生成的 `.tex`
     与 Windows 版**字节不同**（Windows 版为 CRLF）。这是**期望行为**，
     且对 LaTeX 无影响，但若要做回归对比测试需注意差异。
2. **路径分隔符 `\\`**
   源码用 `os.path.join` / `os.path.dirname` / `os.path.abspath`，
   这些都是跨平台的；`\\` 只出现在 **LaTeX 字符串常量**里（如 `'\\\\'` = LaTeX 的换行 `\\`），
   与文件系统无关。Swift 对应 `URL(fileURLWithPath:).deletingLastPathComponent()`
   与 `appendingPathComponent`，**不要**手工拼 `\`。
   ⚠️ 输出文件名 `'Chinese Content_{}.tex'` 含空格，拼 URL 时务必用
   `appendingPathComponent`（会正确编码）而非字符串拼接。
3. **盘符 / 硬编码 Windows 路径**
   本模块**没有**硬编码 `E:\Github\...` 之类路径 —— `path` 由调用方（GUI）
   传入，输出目录派生自 `os.path.dirname(path)`。若 GUI 层（`main.py`）中有
   `E:\` 默认路径，那属于**调用方**的问题，本模块无需处理。
   Swift 版应让 `path` 来自 NSOpenPanel 的 `url.path`。
4. **编码**：读写**均显式 `encoding='utf-8'`**（`co_consts` 证实）。
   Swift 用 `String(contentsOf:encoding:.utf8)` / `.write(to:atomically:encoding:.utf8)`。
   ⚠️ 输入报告含大量**中文全角标点**（`：`、`，`、`——`）与**波浪号 `~`**，
   必须确保 UTF-8 往返无损；**不要**用 `String.data(using:.ascii)` 或 Latin-1。
   ⚠️ 若输入含 BOM，Python 的 `encoding='utf-8'` **不剥离 BOM**（`utf-8-sig` 才剥离），
   首行会成为 `\ufeff起始页码...`，`'起始页码' in line` 仍为真（子串匹配），
   但 `line[7:]`／`split()[0][5:]` 的偏移会错位。Swift 需保持同样行为，
   或明确决定用 `.utf8` 而非 `.utf8BOM` 剥离。
5. **日期时间**：Python `datetime.now()` 是**本地时间**、无时区；
   `strftime('%Y-%m-%d_%H-%M-%S')` 生成**零填充**数字。
   Swift 对应 `DateFormatter`，需设 `dateFormat = "yyyy-MM-dd_HH-mm-ss"`、
   `locale = Locale(identifier: "en_US_POSIX")`（避免用户区域设置改变数字/日历）、
   `timeZone = .current`。
6. **`str.strip()` 语义**：Python `str.strip()` 无参数时剥除**所有 Unicode 空白**
   （含 `\u3000` 全角空格）。Swift 的 `trimmingCharacters(in: .whitespacesAndNewlines)`
   也包含 `\u3000`（属 `CharacterSet.whitespaces`），行为一致 —— 但仍建议
   显式加入 `.whitespacesAndNewlines.union(CharacterSet(charactersIn: "\u{3000}"))` 以求稳妥。
   ⚠️ `str.split()` **无参数**时按任意连续空白切分并**丢弃空串**，
   与 Swift `split(whereSeparator: \.isWhitespace)` 语义接近（后者默认也省略空子串）。
7. **`clean_name` 的字符范围**是**码位**比较，Swift 中 `Character` 是**字素簇**，
   与码位不等价。CJK 基本区字符通常是单码位字素，可直接比 `unicodeScalars`；
   但为与 Python **完全一致**，应遍历 `name.unicodeScalars` 并比较 `0x4E00...0x9FFF`。

## 9. 边界情况与错误处理

| 场景 | 行为 |
|---|---|
| 输入文件不存在 | `process_file` 内 `open` 抛 `FileNotFoundError`；被 `generate_tex` 的 `except Exception` 捕获 → 记录异常 + "生成失败" |
| 输出目录不可写 / 磁盘满 | `open`/`write` 抛 `OSError` → 同样被捕获并记录 |
| 未找到任何 `起始页码` 行 | `start_idx`/`end_idx` 均保持 `0`；循环不 yield；**仅**尾部 `yield process_info(content[0:], flag)` 处理整个文件 → 所有字段来自全局逐行扫描，`page` 可能为 `''` |
| 只有一条记录 | 循环中 `start_idx==0` 且首次命中时 `start_idx = i`；**该记录不会被 yield**（因为从未进入 `elif`），最后由尾部调用 `content[end_idx:]`（`end_idx` 仍为 `0`）处理 → 结果**恰好正确**，但也把第 0 行之前的内容包含了 |
| `起始页码` 出现在第 0 行 | 见 §5.2 哨兵 bug：`start_idx` 停在 `0`，该轮不 yield，记录被吞掉 |
| 标题含 **两个以上** `——`（中文） | `UNPACK_SEQUENCE 2` → `ValueError` → 被捕获，整个文件**一个条目都不写**，输出为 失败 |
| 标题含 **两个以上** `---`（英文） | 同上 `ValueError` |
| `int()` 转换失败（页码非数字） | `process_info` 中 `int(...)` 抛 `ValueError` → 生成器在迭代中途抛出 → 被 `except Exception` 捕获（`with` 块已写出部分内容，文件**残留半成品**） |
| `flag` 既非 `'ch'` 也非 `'en'` | `generate_tex` **静默返回 `None`**，不生成任何文件、不打日志 |
| `process_info` 返回 `None` | 理论上不会走到（`flag` 已限定），但若发生，`file.write(None)` 抛 `TypeError` → 被捕获 |
| `zhname2`/`zhname3` 非空但英文名为空 | 会生成 `\textit{{}, {}}` 之类的空占位 —— **不报错** |
| 异常后输出文件 | 已 `open(..., 'w')` 的文件被**截断并部分写入**，异常时不删除，留下不完整的 `.tex` |

## 10. 不确定之处

1. **`起始页码：` 行的完整实际格式**：`line.split()[0][5:]` 暗示该行可能形如
   `起始页码：23 页码：45`（含一个空格后的第二字段）。但仅凭字节码无法确定
   分隔符是空格、制表符还是全角空格，也无法确定是否真有第二个字段。
   `split()` 无参数按任意空白切分，**至少要求第 6 个字符起是纯数字**。
   建议用真实报告样例验证。
2. **报告 txt 中是否存在其他字段行**（如`关键词`、`摘要`、`基金项目`、`收稿日期`）——
   这些行会被 `process_info` 的 if/elif 链**忽略**，但其存在会影响
   `process_file` 的记录切分（不影响，因为只看 `起始页码`）。
   不影响正确性，但会影响"每条记录行数"的预期。
3. **`'起始页码'` 判据的双重性**：`process_file` 用无冒号版本，
   `process_info` 用带全角冒号版本。若某行形如 `起始页码:`（半角冒号），
   会被 `process_file` 当作记录边界，但 `process_info` **不会**从中提取 page
   （因为 `'起始页码：'` 带全角冒号不匹配）→ `page` 保持 `''`。
   这是**真实存在的行为不一致**，无法确定是原作者有意还是疏忽。
4. **`en_content_end` 与 `ch_content_end` 是同一对象**：字节码明确
   (line 59 `LOAD_CONST 4; STORE_NAME en_content_end`)，但源码中作者可能
   写了两份内容相同的字面量，被 Python 的常量折叠/驻留合并为同一个对象。
   **运行时语义完全相同**，移植时用同一个字符串即可。
5. **`'\\hspace{1em}'` 的源码转义**：字节码显示运行时值为 `\hspace{1em}`（12 字符）。
   但源码可能写作 `'\hspace{1em}'`（无效转义，Python 保留反斜杠并告警）
   或 `'\\hspace{1em}'`（明确转义）。**运行时结果一致**，无法从字节码区分。
6. **`run_info` 中异常对象的显示**：传的是异常**对象**，Tkinter 的 `insert`
   会调用 `str()`。Swift 版应插入 `error.localizedDescription`。
7. **`date_str` 在同一秒内重复调用**：两个 `.tex` 文件名可能相同吗？
   `ch` 与 `en` 文件名前缀不同（`Chinese Content_` vs `contents_`），
   故不会冲突。但**同一 flag 连续调用两次**若落在同一秒会**覆盖**前一个文件。
8. **`process_file` 尾部 `content[end_idx:]` 用 `end_idx` 而非 `start_idx`**
   —— 字节码明确 (`LOAD_FAST 5 (end_idx)`)，但这看起来像 bug（应为 `start_idx`）。
   保留原行为。

---

## 11. 完整重建源码（汇总）

```python
import datetime
import os
from run_info import run_info

ch_content_head = open('/tmp/toolx/charti_const_3.txt').read()   # 常量 3, 675 字符
ch_content_end  = open('/tmp/toolx/charti_const_4.txt').read()   # 常量 4, 31 字符
en_content_head = open('/tmp/toolx/charti_const_5.txt').read()   # 常量 5, 565 字符
en_content_end  = ch_content_end                                  # 复用常量 4

def clean_name(name):
    return ''.join(filter(lambda x: '一' <= x <= '鿿', name))

def twoname(name):
    name = clean_name(name)
    if len(name) == 2:
        return name[0] + '\hspace{1em}' + name[1]
    return name

def process_info(info, flag):
    page = ''
    engname1, engname2, engname3 = '', '', ''
    zhname1, zhname2, zhname3 = '', '', ''
    zhtitle = ''
    entitle = ''
    for line in info:
        if '起始页码：' in line:
            page = int(line.split()[0][5:].strip())
        elif '第一作者英文:' in line:
            engname1 = line[7:].strip()
        elif '第一作者中文:' in line:
            zhname1 = twoname(line[7:].strip())
        elif '第二作者英文:' in line:
            engname2 = line[7:].strip()
        elif '第二作者中文:' in line:
            zhname2 = twoname(line[7:].strip())
        elif '第三作者英文:' in line:
            engname3 = line[7:].strip()
        elif '第三作者中文:' in line:
            zhname3 = twoname(line[7:].strip())
        elif '中文标题:' in line:
            zhtitle = line.split('中文标题:')[-1].strip()
        elif '英文标题' in line:
            entitle = line.split('英文标题:')[-1].strip()

    if zhname2 and zhname3:
        authors    = '&\\textit{{\\large {}，{}，{}}}'.format(zhname1, zhname2, zhname3)
        en_authors = '\\textit{{{}, {}, {}}}'.format(engname1, engname2, engname3)
    elif zhname2:
        authors    = '&\\textit{{\\large {}，{}}}'.format(zhname1, zhname2)
        en_authors = '\\textit{{{}, {}}}'.format(engname1, engname2)
    else:
        authors    = '&\\textit{{\\large {}}}'.format(zhname1)
        en_authors = '\\textit{{{}}}'.format(engname1)

    zhtitle = (zhtitle.replace('\\\\', '').replace('\\Large', '')
                      .replace('\\large', '').replace('{', '').replace('}', '').strip())
    entitle = (entitle.replace('\\\\', '').replace('\\Large', '')
                      .replace('\\large', '').strip())

    if flag == 'ch':
        if '——' in zhtitle:
            zhtitle1, zhtitle2 = zhtitle.split('——')
            zhtitle1 = zhtitle1.strip()
            zhtitle2 = '~~~~——' + zhtitle2.strip()
            return f'{{\\large {zhtitle1}}}\n{authors}\n& {page}\\\\\n{{\\large {zhtitle2}}}\n\n\\vdistance\n\n'
        return f'{{\\large {zhtitle}}}\n{authors}\n& {page}\\\\\n\n\\vdistance\n\n'
    if flag == 'en':
        if '---' in entitle:
            entitle1, entitle2 = entitle.split('---')
            entitle1 = entitle1.strip()
            entitle2 = '---' + entitle2.strip()
            return f'\\large {entitle1}\\\\\n\\large ~~~~---{entitle2}\\\\\n{en_authors}\n& {page}\\\\\n\n\n\\vdistance\n\n'
        return f'\\large {entitle}\\\\\n{en_authors}\n& {page}\\\\\n\n\\vdistance\n\n'
    return None

def process_file(path, flag):
    with open(path, 'r', encoding='utf-8') as file:
        content = file.readlines()
    start_idx = 0
    end_idx = 0
    for i, line in enumerate(content):
        if '起始页码' in line and start_idx == 0:
            start_idx = i
        elif '起始页码' in line:
            end_idx = i - 1
            yield process_info(content[start_idx:end_idx + 1], flag)
            start_idx = i
    yield process_info(content[end_idx:], flag)

def generate_tex(path, flag, out_txt):
    date_str = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    if flag == 'ch':
        run_info(out_txt, '开始生成中文目录')
        try:
            tex_path = os.path.abspath(os.path.join(
                os.path.dirname(path), 'Chinese Content_{}.tex'.format(date_str)))
            with open(tex_path, 'w', encoding='utf-8') as file:
                file.write(ch_content_head)
                file.write('\n\n%中间主体\n\n')
                for info in process_file(path, flag):
                    file.write(info)
                file.write(ch_content_end)
            run_info(out_txt, '中文目录TeX文件已生成\n')
        except Exception as e:
            run_info(out_txt, e)
            run_info(out_txt, '中文目录TeX文件生成失败\n')
    elif flag == 'en':                       # ← 必须是 elif 而非 if（字节码已证实，见 §12）
        run_info(out_txt, '开始生成英文目录')
        try:
            tex_path = os.path.abspath(os.path.join(
                os.path.dirname(path), 'contents_{}.tex'.format(date_str)))
            with open(tex_path, 'w', encoding='utf-8') as file:
                file.write(en_content_head)
                file.write('\n\n%中间主体\n\n')
                for info in process_file(path, flag):
                    file.write(info)
                file.write(en_content_end)
            run_info(out_txt, '英文目录TeX文件已生成\n')
        except Exception as e:
            run_info(out_txt, e)
            run_info(out_txt, '英文目录TeX文件生成失败\n')
    # else: 静默返回 None
```

> 注：上面 `ch_content_head` 等的 `open(...).read()` 只是占位；
> 实际源码中是内联字符串字面量，字节级内容见 `/tmp/toolx/charti_const_{3,4,5}.txt`。

---

## 12. 重建源码的正确性验证（已完成）

重建源码保存在 `/tmp/toolx/reconstructed_charti_elif.py`（内容同 §11，
末分支用 `elif`）。验证方法：用同一个 Python 3.11 编译重建源码，
逐函数 `dis` 后**剥离源行号列**再与原字节码逐条比对。

结果（`/tmp/toolx/verify3.py`）：

```
  clean_name    : BYTECODE IDENTICAL (ignoring line numbers)
  <lambda>      : BYTECODE IDENTICAL (ignoring line numbers)
  twoname       : BYTECODE IDENTICAL (ignoring line numbers)
  process_info  : BYTECODE IDENTICAL (ignoring line numbers)
  process_file  : BYTECODE IDENTICAL (ignoring line numbers)
  generate_tex  : BYTECODE IDENTICAL (ignoring line numbers)
OVERALL BYTECODE EQUIVALENCE: True
```

模块级非常量常量（含 3 个模板）也 `==` 相等。

⚠️ **由验证发现的修正**：`generate_tex` 中第二个分支必须是 **`elif flag == 'en':`
而不是 `if flag == 'en':`**。字节码证据：`flag == 'ch'` 的
`POP_JUMP_FORWARD_IF_FALSE 355 (to 824)`，而 `824` 正是 `LOAD_FAST flag;
LOAD_CONST 'en'; COMPARE_OP 2 (==)` 的位置；用 `if` 写会偏移 2 条指令
（目标 820），因为 `if` 之后紧跟 `POP_TOP` 以丢弃 `False`。改回 `elif`
后两处分支的指令序列**完全一致**。

### 端到端行为测试（3 条记录，含 1/2/3 位作者）
输入 `/tmp/toolx/e2e/report.txt`，用 `run_info` 桩
(`out_txt.append(str(ss))`) 驱动。日志输出：
```
'开始生成中文目录'
'中文目录TeX文件已生成\n'
'开始生成英文目录'
'英文目录TeX文件已生成\n'
```
生成 `Chinese Content_2026-10-05_15-06-15.tex` (1158 B) 与
`contents_2026-10-05_15-06-15.tex` (899 B)。

产出正文实证了以下关键行为：
```
{\large 逻辑与论证}
&\textit{\large 张\hspace{1em}三，李\hspace{1em}四，王\hspace{1em}五}
& 23\\

\vdistance

{\large }
&\textit{\large 赵\hspace{1em}六}
& 45\\
{\large ~~~~——否定问题研究}

\vdistance

{\large （专栏）导言 Large 某某}
&\textit{\large 陈\hspace{1em}七，孙\hspace{1em}八}
& 67\\
```
1. **每条记录恰好产出一次，无重复**。`list(process_file(report,'ch'))` 返回
   **3 个元素**，分别对应 3 条记录（已实测）。原因（修正 §5 中较悲观的措辞）：
   记录 1 的 `start_idx` 保持 `0`（哨兵），故进入 `elif` 的是**记录 2 和 3**
   （两次 yield，切片分别为 `content[0:i2]`、`content[i2:i3]`），
   末尾 `content[end_idx:]` 即 `content[i3-1:]`（**恰好**从记录 3 起始行的
   前一行起）产出第 3 条 → **总计 3 条，一一对应**。
   即哨兵 `start_idx == 0` **恰好抵消**了 `end_idx = i - 1` 与
   末尾用 `content[end_idx:]`（而非 `content[start_idx:]`）两处偏移。
   ⚠️ 这是**巧合性的平衡**：若文件以 `起始页码` 行开头、或首条记录前有
   前置内容、或记录间分隔行数不同，就会失配（见 §9 哨兵 bug）。
   **Swift 移植不要"顺手修正"这些偏移**，必须逐字复刻。
2. 两字名 `张三` → `张\hspace{1em}三`；`李四`、`王五`、`赵六`、`陈七`、`孙八`
   全部正确插入；英文名不受影响。
3. **`（专栏）` 全角括号被保留**（因为是模板常量外的标题文本，只做
   `replace` 清理，不做 `clean_name`）—— 标题**不经** `clean_name`。
4. 标题中的 `{` `}` `\\` `\Large` 被清除，但 `\Large` 因先被 `\\`→'' 规则
   吃掉反斜杠而残留为 **`Large`**（输出中 `（专栏）导言 Large 某某`）——
   实证了 §4.2 的顺序陷阱。
5. 中文标题 `—— 否定问题研究` 走副标题分支，产出
   `{\large }`（空主标题）+ `{\large ~~~~——否定问题研究}`。
6. 英文副标题 `On Negation---A Study` → `\large On Negation\\` +
   `\large ~~~~------A Study`（注意 `~~~~` 后的 `---` 来自**模板常量**，
   加上 `entitle2` 前缀 `---`，故显示为 6 个连字符）。

### CRLF 输入测试
把同一报告的行尾改为 `\r\n` 后重新生成，输出文件
**`b'\r' in output == False` 且长度完全相同 (1158 B)** ——
证实 Python 的 universal-newlines 读入会静默规范化 `\r\n`。
**这是 macOS 移植时最容易踩的坑**：Swift 不会自动规范化。

## 13. Swift 移植检查清单

- [ ] 读入前把 `\r\n` 规范化为 `\n`（Python 靠 universal newlines，Swift 必须手动）。
- [ ] 按 **UTF-8** 读写，不用 ASCII/Latin-1；明确决定是否剥离 BOM。
- [ ] `line[7:]` 用**字符**切片（`dropFirst(7)`），`split()[0][5:]` 同理。
- [ ] `clean_name` 遍历 **unicodeScalars** 比较 `0x4E00...0x9FFF`（非 `Character`）。
- [ ] `twoname` 仅在 `count == 2` 时插入字面 `"\hspace{1em}"`（Swift 写 `"\\hspace{1em}"`）。
- [ ] 复现 `process_file` 的 `start_idx == 0` 哨兵与末尾 `content[end_idx:]`
     切片（**故意保留 bug**，否则输出会变）。
- [ ] 复现 `split('——')` 的**二元解包**语义：多于 2 段时抛错并整体失败。
- [ ] 复现标题 `replace` 链的**顺序**（`\\` 先于 `\Large`/`\large`）。
- [ ] 中文标题多清 `{` `}`，英文标题**不清**。
- [ ] 输出目录 = 输入文件所在目录；文件名 `Chinese Content_<ts>.tex`
     与 `contents_<ts>.tex`（注意前者的空格）。
- [ ] 时间戳 `yyyy-MM-dd_HH-mm-ss`，`en_US_POSIX` locale，本地时区。
- [ ] 写入顺序：head → `"\n\n%中间主体\n\n"` → 各条目 → end。
- [ ] 失败时保留**已部分写出**的 `.tex`（不删除）。
- [ ] `flag` 非 `ch`/`en` 时静默返回，不报错、不建文件。
- [ ] 日志消息逐字复刻（含/不含结尾 `\n` 的差异）。
