# outinfo.py — 逆向工程规格（「检查tex信息」功能）

> 来源：`/tmp/toolx/dis/outinfo.py.txt`（Python 3.11 `dis` 反汇编，1267 行）
> 目的：为 macOS 原生 Swift 重写提供**逐字节忠实**的规格。
> 状态：**已完成**（全部 5 个函数重构 + 端到端实测验证）
> 验证方式：按重构源码在合成夹具上实际执行，逐字节比对 `repr()` 输出（见 §13）

## 0. 顶层模块结构

```python
# 行 1-6
import os
import glob
import PyPDF2
from run_info import run_info

import re

# 行 9  def no_chinese(s)
# 行 25 def extract_title(text)
# 行 45 def process_folders(...)
# 行 157 def get_line_num(...)
# 行 163 def main()
```

顶层只有 `import` 与 5 个 `MAKE_FUNCTION` / `STORE_NAME`，末尾 `RETURN_VALUE None`。
`run_info(out_txt, ss)` 的实现（`run_info.py`）：

```python
def run_info(out_txt, ss):
    out_txt.insert('end', ss)
    out_txt.update()
    out_txt.see('end')
```

即：向 Tkinter 文本/日志控件追加字符串并滚动到底部。**注意：`run_info` 只负责 GUI 显示，不写文件。**

---

## 1. `no_chinese(s)` — 行 9–13

```python
def no_chinese(s):
    if not re.search('[一-龥]', s):
        return True
    return False
```

**逐字节说明**

- `re.search(pattern, s)`：`pattern = '[一-龥]'`（U+4E00 至 U+9FA5 CJK 统一表意文字基本区）。
- `POP_JUMP_FORWARD_IF_NOT_NONE 48`：结果 **不是 None**（即匹配到中文）→ 跳到 48，`return False`。
- 结果 **是 None**（未匹配到中文）→ 落空到 44，`return True`。
- 返回 `True` 表示**不含中文**，`False` 表示**含中文**。

> Swift 等价：`s.range(of: "[\\u{4E00}-\\u{9FA5}]", options: .regularExpression) == nil`

---

## 2. `extract_title(text)` — 行 25–42

```python
def extract_title(text):
    flag = 0
    for i, char in enumerate(text):
        if char == '{':
            if i == 0:
                continue
            flag += 1
        elif char == '}':
            flag -= 1
        if flag == -1:
            return text[1:i].replace('\n', '')

    pattern = '{([^{}]+)}'
    match = re.search(pattern, text)
    if match:
        return match.group(1)
    return None
```

**逐字节说明（非常重要，容易被"改进"错）**

| 字节码 | 含义 |
|---|---|
| `LOAD_CONST 1 (0)` → `STORE_FAST flag` | 计数器初值 `0`（行 26） |
| `enumerate(text)` → `UNPACK_SEQUENCE 2` → `i, char` | 行 27；顺序是 **先 i 后 char** |
| 行 28 `char == '{'` → `POP_JUMP_FORWARD_IF_FALSE to 84` | 若为 `{` 走 29–31 分支 |
| 行 29 `i == 0` → `POP_JUMP_FORWARD_IF_FALSE to 72` | |
| 行 30 `JUMP_BACKWARD to 36` | **`continue`**：跳过位置 0 的 `{`（LaTeX 标题首字符必是 `{`） |
| 行 31 `flag = flag + 1` | 注意：**只有非首字符的 `{` 才 +1** |
| 行 32 `char == '}'` → `else` 分支 | `elif`（因为到达此处说明不是 `{`） |
| 行 33 `flag = flag - 1` | **所有** `}` 都 -1 |
| 行 34 `flag == -1` → `POP_JUMP_FORWARD_IF_FALSE to 182` | 未命中则 `JUMP_BACKWARD to 36`（继续循环） |
| 行 35 `return text[1:i].replace('\n', '')` | 切片 **从索引 1 开始**（去掉开头 `{`），**不含** i 处的 `}`；`BUILD_SLICE 2`；删除所有换行 |

**关键点**
- 触发返回的条件是 `flag == -1`（不是 `0`）。由于位置 0 的 `{` 被 `continue` 跳过而**没有 +1**，因此第一个 `}` 就会把 `flag` 从 `0` 打到 `-1` → 立即返回第一层括号内的内容。
- 若循环中从未出现 `flag == -1`（例如 text 为空、或首字符不是 `{` 却没有配平的 `}`），则退回到正则。
- 行 37：`pattern = '{([^{}]+)}'`（**注意：`[^{}]` 中不含反斜杠转义，是原样的字符类**）。
- 行 38–40：`re.search` 命中则返回 `match.group(1)`。
- 行 42：未命中返回 **`None`**（不是空串）。

> 注：循环内没有 `break`；`for` 正常结束后直接进入 184 行（正则分支）。

---

## 3. `process_folders(path, output_dir, out_txt)` — 行 45–155

> 这是本功能的主体。签名从 `ExceptionTable` 与字节码推断：3 个位置参数，
> `MAKE_CELL 0 (path)` 说明 `path` 被**内部生成器表达式闭包捕获**；
> 行 151 用 `output_dir` 拼接输出路径；行 154/3126 用 `out_txt` 调 `run_info`。
> 参数顺序：**(1) `path`（被扫描的期目录）, (2) `output_dir`（报告输出目录）, (3) `out_txt`（Tkinter 日志控件）**。

```python
def process_folders(path, output_dir, out_txt):
    try:
        # 行 48
        folders = sorted(x for x in os.listdir(path) if os.path.isdir(os.path.join(path, x)) and x[0].isdigit())
        # 行 49
        output = ''
        # 行 50
        for folder in folders:
            # 行 51
            folder_path = os.path.join(path, folder)
            # 行 52
            folder_name = folder.split()[-1]          # 注意：按空白切分后取最后一个元素！
            # 行 53-54
            if folder_name == 'Contents':
                continue
            # 行 55
            output += f'{folder_name}\n'
            # 行 56
            tex_file = os.path.join(folder_path, folder_name + '.tex')
            # 行 57
            if os.path.exists(tex_file):
                # 行 58
                with open(tex_file, encoding='utf-8') as f:
                    # 行 59
                    content = f.read()
                    # 行 60
                    lines = content.splitlines()
                    # 行 61
                    vol_num = get_line_num(lines, '输入卷号')
                    # 行 62-63
                    if vol_num:
                        output += f'卷号：{vol_num}  '
                    # 行 64
                    issue_num = get_line_num(lines, '输入当年期号')
                    # 行 65-66
                    if issue_num:
                        output += f'期号：{issue_num}  '
                    # 行 67
                    year_num = get_line_num(lines, '输入出版年份')
                    # 行 68-69
                    if year_num:
                        output += f'年份：{year_num}\n'
                    # 行 70
                    start_page = get_line_num(lines, '输入起始页码')
                    # 行 71-72
                    if start_page:
                        output += f'起始页码：{start_page}  '
                    # 行 73
                    end_page = get_line_num(lines, '输入终止页码')
                    # 行 74-75
                    if end_page:
                        output += f'终止页码：{end_page}  '
                    # 行 76
                    total_pages = get_line_num(lines, '输入页数')
                    # 行 77-78
                    if total_pages:
                        output += f'页数：{total_pages}\n'
                    # 行 79
                    for i, line in enumerate(lines):
                        # 行 80
                        if '请输入论文题目' in line:
                            tmptext = lines[i + 1] + '\n' + lines[i + 2]
                            output += f'中文标题: {extract_title(tmptext)}\n'
                            continue
                        # 行 83
                        if 'Insert Chinese title' in line:
                            tmptext = lines[i + 1] + '\n' + lines[i + 2]
                            output += f'中文标题: {extract_title(tmptext)}\n'
                            continue
                        # 行 86
                        if '请输入英文标题' in line:
                            tmptext = lines[i + 1] + '\n' + lines[i + 2]
                            output += f'英文标题: {extract_title(tmptext)}\n'
                            continue
                        # 行 89
                        if 'Insert the title of your paper.' in line:
                            tmptext = lines[i + 1] + '\n' + lines[i + 2]
                            output += f'英文标题: {extract_title(tmptext)}\n'
                            continue
                        # 行 92
                        if '第一作者姓名' in line:
                            author_name = lines[i + 1]           # 注意：不是 [i+2]
                            if no_chinese(author_name):
                                output += f'第一作者英文: {author_name}\n'
                            else:
                                output += f'第一作者中文: {author_name}\n'
                            continue
                        # 行 98
                        if 'Insert the first author name.' in line:
                            author_name = lines[i + 1]
                            if no_chinese(author_name):
                                output += f'第一作者英文: {author_name}\n'
                            else:
                                output += f'第一作者中文: {author_name}\n'
                            continue
                        # 行 104
                        if '第二作者姓名' in line and line[0] != '%':
                            author_name = lines[i]               # 注意：是 [i]，即当前行本身
                            if no_chinese(author_name):
                                output += f'第二作者英文: {author_name}\n'
                            else:
                                output += f'第二作者中文: {author_name}\n'
                            continue
                        # 行 110
                        if 'Insert the second author if' in line and line[0] != '%':
                            author_name = lines[i]
                            if no_chinese(author_name):
                                output += f'第二作者英文: {author_name}\n'
                            else:
                                output += f'第二作者中文: {author_name}\n'
                            continue
                        # 行 116
                        if '第三作者姓名' in line and line[0] != '%':
                            author_name = lines[i]
                            if no_chinese(author_name):
                                output += f'第三作者英文: {author_name}\n'
                            else:
                                output += f'第三作者中文: {author_name}\n'
                            continue
                        # 行 122
                        if 'Insert the third author if' in line and line[0] != '%':
                            author_name = lines[i]
                            if no_chinese(author_name):
                                output += f'第三作者英文: {author_name}\n'
                            else:
                                output += f'第三作者中文: {author_name}\n'
                            continue
                        # 行 128
                        if 'documentclass' in line:
                            # 行 129-133
                            try:
                                zihao = line.split(',')[1]
                                output += f'字号 {zihao}\n'
                            except Exception:
                                pass
                            continue
            # 行 134
            sty_file = glob.glob(f'{folder_path}/*.sty')
            # 行 135
            if sty_file:
                # 行 136
                with open(sty_file[0], encoding='utf-8') as f:
                    # 行 137
                    content = f.read()
                    # 行 138
                    baselinestretch = get_line_num(content.splitlines(), '全文行间距')
                    # 行 139-140
                    if baselinestretch:
                        output += f'全文行距: {baselinestretch}\n'
            # 行 141
            pdf_file = os.path.join(folder_path, folder_name + '.pdf')
            # 行 142
            num_pages = 0
            # 行 143
            if os.path.exists(pdf_file):
                # 行 144
                with open(pdf_file, 'rb') as f:
                    # 行 145
                    pdf = PyPDF2.PdfReader(f)
                    # 行 146
                    num_pages = len(pdf.pages)
                    # 行 147
                    output += f'--------------------------实际pdf页数: {num_pages}\n'
            # 行 148
            output += '\n'

        # 行 149
        from datetime import datetime      # 注意：函数内部 import（局部变量 datetime）
        # 行 150
        now = datetime.now()
        # 行 151
        with open(os.path.join(output_dir, '信息检查' + str(now.strftime('%Y_%m_%d_%H_%M_%S')) + '.txt'), 'w', encoding='utf-8') as f:
            # 行 152
            f.write(output)
    # 行 153
    except Exception as e:
        # 行 154
        run_info(out_txt, e)
        # 行 155
        run_info(out_txt, '请确认目录格式为例如[03 Ming_Xiao]，内部文件为[Ming_Xiao.tex]的格式')
```

### 3.1 文件夹筛选（行 48 的生成器表达式）

`<genexpr> at line 48` 反汇编：

```python
folders = sorted(
    x for x in os.listdir(path)
    if os.path.isdir(os.path.join(path, x)) and x[0].isdigit()
)
```

- `os.listdir(path)` → 迭代 `x`
- 条件 1：`os.path.isdir(os.path.join(path, x))`
- 条件 2：`x[0].isdigit()`（**首字符**必须是数字）
- `sorted(...)` 对结果做默认字符串排序（非自然排序！`'10...' < '2...'`）

### 3.2 极易出错的三处

1. **`folder_name = folder.split()[-1]`（行 52）**：`split()`（无参数）按任意空白切分，取**最后**一段。
   - `'03 Jie_Zhou'` → `'Jie_Zhou'`
   - `'03 Jie Zhou'` → `'Zhang'`
   - `'03Jie_Zhou'`（无空格）→ `'03Jie_Zhou'`（整串，仅一段）
   然后 `tex_file = folder_path + '/' + folder_name + '.tex'`，即**目录名最后一段**必须等于 `.tex` 主文件名。
   `output` 里写入的也是 `folder_name`（**不带编号、不带路径**）。

2. **作者行取值下标不一致**：
   - 第一作者：`lines[i + 1]`（下一行）
   - 第二/第三作者：`lines[i]`（**当前行本身**）

3. **`line[0] != '%'`**：仅第二/第三作者分支有此额外条件；第一作者与标题分支**没有**注释判断。

---

## 4. `get_line_num(lines, keyword)` — 行 157–161

```python
def get_line_num(lines, keyword):
    for line in lines:
        if keyword in line:
            return ''.join(filter(str.isdigit, line))
    return ''
```

逐字节说明：

- 行 158：`for line in lines`（`GET_ITER` / `FOR_ITER`）
- 行 159：`keyword in line` → `CONTAINS_OP 0`（左操作数是 `keyword`，右是 `line`）
- 行 160：`''.join(filter(str.isdigit, line))`
  - `LOAD_CONST ''` → `LOAD_METHOD join`（`str.join`）
  - `LOAD_GLOBAL filter`，参数 `str.isdigit`（**未绑定方法**，作为谓词）与 `line`
  - `CALL 1`(join) → `SWAP 2; POP_TOP; RETURN_VALUE`（Python 3.11 的 `join` 快速路径形态）
- 行 161：**未找到时返回空字符串 `''`**（不是 `None`）

**语义**：返回该行中**所有数字字符按原顺序拼接**得到的字符串。
例如 `% 卷号 = 12` → `'12'`；`\setcounter{page}{105}` → `'105'`；
`\baselinestretch{1.25}` → `'125'`（**小数点被丢弃！**）。若无数字，返回 `''`
（`''` 为假 → 调用方 `if vol_num:` 不追加）。

> ⚠️ `filter(str.isdigit, line)` 中 `str.isdigit` 是 `str` 类的**未绑定方法**，
> 对每个字符调用。因 `line` 是 `str`，等价于逐字符 `c.isdigit()`。

---

## 5. `main(script_path, out_txt)` — 行 163–171

```python
def main(script_path, out_txt):
    try:
        # 行 165
        run_info(out_txt, '当前目录为：' + str(script_path) + '\n')
        # 行 166
        process_folders(script_path, script_path, out_txt)
        # 行 167
        run_info(out_txt, '检查文件生成成功，请查看。\n')
    except Exception as e:
        # 行 170
        run_info(out_txt, e)
        # 行 171
        run_info(out_txt, '请确认目录格式为例如[03 Ming_Xiao]，内部文件为[Ming_Xiao.tex]的格式')
```

**关键**：行 166 `process_folders(script_path, script_path, out_txt)` ——
第 1、2 个实参**都是 `script_path`**（`LOAD_FAST 0` 两次）。
即 **扫描目录 == 报告输出目录**：报告 `.txt` 写在**期目录内部**。

`main` 的 `script_path` 由外部调用者（`main.py` 的 GUI）传入，**不是本模块计算**的；
本模块没有 `if __name__ == '__main__'` 块。

---

## 6. ★ 生成报告 txt 的 EXACT 格式（CRITICAL — 生成目录 会回读此文件）

### 6.1 文件路径 / 文件名

```python
os.path.join(output_dir, '信息检查' + str(now.strftime('%Y_%m_%d_%H_%M_%S')) + '.txt')
```

- 打开模式：`open(..., 'w', encoding='utf-8')`
- **`'w'`（文本模式）** → **Windows 上 `\n` 会被翻译成 `\r\n`**！
  源码字符串里全部是 `'\n'`；落盘后（Windows）实际字节是 **CRLF**。
- 文件名形如：`信息检查2026_05_12_14_30_05.txt`
- `strftime` 是**本地时间**（`datetime.now()`），**无分隔符**，直接拼接。
- 注意 `str(...)` 包了一层但 `strftime` 本就返回 `str`，**冗余无副作用**。
- 文件名内**没有**任何空格、连字符或下划线分隔 `信息检查` 与日期。

> **精确模式**：`信息检查` + `%Y_%m_%d_%H_%M_%S` + `.txt`
> 例：`信息检查2026_05_12_14_30_05.txt`

### 6.2 编码与换行

| 项目 | 值 |
|---|---|
| 编码 | `utf-8` **无 BOM** |
| 换行（源码中的字符串字面量） | 一律 `'\n'`（`LOAD_CONST 5 ('\n')`） |
| 换行（Windows 落盘实际字节） | **`\r\n`**（文本模式翻译） |
| 是否追加 | 否，`'w'` 每次**截断覆盖** |

> ★ **这是移植最大的坑**：Python 在 Windows 下写出的文件是 CRLF。
> 若「生成目录」用 Python 在 Windows 上读回，它得到的是 CRLF 内容；
> Swift 必须**复刻 CRLF** 才能保证下游解析行为一致（见 §9）。

### 6.3 报告整体结构（`output` 变量的拼接顺序）

```
[对每个文件夹 folder（已按 sorted(os.listdir) 且首字符为数字、是目录、名字 != 'Contents'）]
    "{folder_name}\n"                                  # 行 55，folder_name = folder.split()[-1]
    [若 folder_name + '.tex' 存在]                      # 行 57
        卷号行（条件）                                   # 行 62-63
        期号行（条件）                                   # 行 65-66
        年份行（条件）                                   # 行 68-69
        起始页码行（条件）                               # 行 71-72
        终止页码行（条件）                               # 行 74-75
        页数行（条件）                                   # 行 77-78
        [逐行扫描 tex 的 lines，命中即追加，一行一条]
            中文标题行 / 英文标题行 / 第一/二/三作者行 / 字号行
    [若存在 *.sty]                                      # 行 134-135
        全文行距行（条件）                               # 行 139-140
    [若 folder_name + '.pdf' 存在]                      # 行 143
        "--------------------------实际pdf页数: {num_pages}\n"   # 行 147
    "\n"                                                # 行 148 —— 每个文件夹后一个空行
```

### 6.4 逐条字面量（**逐字符精确，注意全角冒号 `：` 与半角冒号 `:` 的区别**）

| # | 行 | Python 表达式 | 精确字面量 | 结尾 | 注 |
|---|---|---|---|---|---|
| 1 | 55 | `f'{folder_name}\n'` | `<folder_name>` | `\n` | 无标签 |
| 2 | 63 | `f'卷号：{vol_num}  '` | `卷号：` | **两个半角空格** | 全角冒号 |
| 3 | 66 | `f'期号：{issue_num}  '` | `期号：` | 两个空格 | 全角冒号 |
| 4 | 69 | `f'年份：{year_num}\n'` | `年份：` | `\n` | 全角冒号，**行末换行** |
| 5 | 72 | `f'起始页码：{start_page}  '` | `起始页码：` | 两个空格 | 全角冒号 |
| 6 | 75 | `f'终止页码：{end_page}  '` | `终止页码：` | 两个空格 | 全角冒号 |
| 7 | 78 | `f'页数：{total_pages}\n'` | `页数：` | `\n` | 全角冒号，**行末换行** |
| 8 | 82/85 | `f'中文标题: {extract_title(tmptext)}\n'` | `中文标题: ` | `\n` | **半角冒号 + 一个空格** |
| 9 | 88/91 | `f'英文标题: {extract_title(tmptext)}\n'` | `英文标题: ` | `\n` | 半角冒号 + 空格 |
| 10 | 95/101 | `f'第一作者英文: {author_name}\n'` | `第一作者英文: ` | `\n` | 半角冒号 + 空格 |
| 11 | 97/103 | `f'第一作者中文: {author_name}\n'` | `第一作者中文: ` | `\n` | 半角冒号 + 空格 |
| 12 | 107/113 | `f'第二作者英文: {author_name}\n'` | `第二作者英文: ` | `\n` | 半角冒号 + 空格 |
| 13 | 109/115 | `f'第二作者中文: {author_name}\n'` | `第二作者中文: ` | `\n` | 半角冒号 + 空格 |
| 14 | 119/125 | `f'第三作者英文: {author_name}\n'` | `第三作者英文: ` | `\n` | 半角冒号 + 空格 |
| 15 | 121/127 | `f'第三作者中文: {author_name}\n'` | `第三作者中文: ` | `\n` | 半角冒号 + 空格 |
| 16 | 131 | `f'字号 {zihao}\n'` | `字号 ` | `\n` | **标签后是空格，无冒号** |
| 17 | 140 | `f'全文行距: {baselinestretch}\n'` | `全文行距: ` | `\n` | 半角冒号 + 空格；标签是「行距」而关键词是「行间距」 |
| 18 | 147 | `f'--------------------------实际pdf页数: {num_pages}\n'` | `--------------------------实际pdf页数: ` | `\n` | **26 个半角连字符 `-`** |
| 19 | 148 | `'\n'` | — | `\n` | 每个文件夹块末尾的空行 |

**分隔符要点**
- 卷号 / 期号 / 起始页码 / 终止页码 四项用 **两个半角空格 `'  '`** 连接，**不换行**；
  它们共同组成**第 1 行**（`卷号：X  期号：Y  起始页码：A  终止页码：B  `）。
- 年份 与 页数 各自以 `\n` 结尾 → **第 2 行 = 年份，第 3 行 = 页数**。
- 之后每个 tex 提取项**各占一行**。

### 6.5 一个「正常」文章块的完整样例

假设目录 `2026年第5期(90)/03 Jie_Zhou/`，内含 `Jie_Zhou.tex` / `Jie_Zhou.pdf` / `journal.sty`。
（`folder_name = 'Jie_Zhou'`）

tex 中 `get_line_num` 命中：
- 某行含 `输入卷号` 且该行数字为 `12`
- 含 `输入当年期号` → `5`；含 `输入出版年份` → `2026`
- 含 `输入起始页码` → `105`；含 `输入终止页码` → `118`；含 `输入页数` → `14`

则 `output` 增长为（为清晰起见把 `\n` 显式写出）：

```text
Jie_Zhou\n
卷号：12  期号：5  起始页码：105  终止页码：118  年份：2026\n
页数：14\n
中文标题: 论指称的模糊性\n
英文标题: On the Vagueness of Reference\n
第一作者英文: Jie Zhou\n
第二作者中文: 张晓明\n
字号 11pt\n
全文行距: 125\n
--------------------------实际pdf页数: 14\n
\n
```

> 注意：第 2 行的字段顺序由**代码执行顺序**决定（卷/期/**年**/起/终/页），
> 所以实际上是：
> `卷号：12  期号：5  年份：2026\n` 然后
> `起始页码：105  终止页码：118  页数：14\n`
> **年份在起始页码之前，且年份以 `\n` 收行**。修正后的真实版：

```text
Jie_Zhou\n
卷号：12  期号：5  年份：2026\n
起始页码：105  终止页码：118  页数：14\n
中文标题: 论指称的模糊性\n
英文标题: On the Vagueness of Reference\n
第一作者英文: Jie Zhou\n
第二作者中文: 张晓明\n
字号 11pt\n
全文行距: 125\n
--------------------------实际pdf页数: 14\n
\n
```

**这正是 生成目录 回读时必须解析的布局：**
1. 行 1：文章目录名（`split()[-1]` 后的结果）
2. 行 2：`卷号：…  期号：…  年份：…`
3. 行 3：`起始页码：…  终止页码：…  页数：…`
4. 行 4：`中文标题: …`
5. 行 5：`英文标题: …`
6. 行 6+：作者行（`第一作者中文/英文: …`、`第二作者…`、`第三作者…`）
7. `字号 …`
8. `全文行距: …`
9. `--------------------------实际pdf页数: N`
10. 空行

### 6.6 缩进结构（已按字节码逐跳核实）

字节码跳转证据：

| 位置 | 字节码 | 结论 |
|---|---|---|
| 388 | `POP_JUMP_FORWARD_IF_FALSE to 2146`（行 57 `if os.path.exists(tex_file)`） | `if` 体 = `390..2098`；**2146 是 `if` 之后的汇合点** |
| 2094/2096 | `JUMP_BACKWARD to 860`（行 128 `if 'documentclass' in line:` 的 fall-through） | 行 128 的 `if` 体结束后**跳出本次循环迭代** → 行 128 是 `continue` 的又一处 |
| 2098 | 行 79 `for` 的循环结束点（`NOP`） | 2098 是 tex 行扫描 for 循环的终点 |
| **2100–2122** | `LOAD_CONST None ×3; PRECALL 2; CALL 2; POP_TOP`（行 58 的 `WITH` 清理 `__exit__(None,None,None)`） | **行 134 在 `with open(tex_file...)` 之外！** |
| 2146 | 行 134 `glob.glob(...)` | `if os.path.exists(tex_file)` 之后无条件执行 |
| 2734–2746 | 行 148 `output += '\n'`；`JUMP_BACKWARD to 104` | 跳回行 50 的 `FOR_ITER` |

**修正后的嵌套结构（确定）**：

```python
for folder in folders:                      # 行 50  ← 104
    folder_path = ...                       # 行 51
    folder_name = folder.split()[-1]        # 行 52
    if folder_name == 'Contents': continue  # 行 53-54
    output += f'{folder_name}\n'            # 行 55
    tex_file = ...                          # 行 56
    if os.path.exists(tex_file):            # 行 57  ← 388 -> 2146
        with open(tex_file, encoding='utf-8') as f:   # 行 58
            content = f.read()              # 行 59
            lines = content.splitlines()    # 行 60
            ...                             # 行 61-78
            for i, line in enumerate(lines):# 行 79
                ...                         # 行 80-133
    # ← 回到外层（tex if 之外）
    sty_file = glob.glob(f'{folder_path}/*.sty')      # 行 134  ← 2146
    if sty_file: ...                        # 行 135-140
    pdf_file = ...                          # 行 141
    num_pages = 0                           # 行 142
    if os.path.exists(pdf_file): ...        # 行 143-147
    output += '\n'                          # 行 148  ← 2734（循环体最后一句）
# 行 149-152 循环之后
```

**因此：**
- `*.sty` 与 `.pdf` 的处理**不受 `tex_file` 是否存在的影响**，对**每个**通过的文件夹都执行。
- `output += '\n'`（行 148）**无条件执行**：即使该文件夹既无 tex、无 sty、无 pdf，
  也会在 `{folder_name}\n` 之后补一个空行 → 该块共两行。

---

## 7. ★ 交互式提示串（逐字，含全角冒号）

### ⚠️ 首先纠正一个常见误解

任务描述称"通过 console input() 逐个询问编辑"。**但从字节码看，`outinfo.py` 中没有任何
`input()` 调用，也没有 `print()`**。所有交互都发生在**被扫描的 `.tex` 文件内部**：

`get_line_num(lines, keyword)` 的 `keyword` 就是这些"提示串"，
它们是**模板 tex 文件中的提示性注释/命令**，编辑在**写论文 tex 时**就已经填好。
`process_folders` 是在**离线读取**这些文件，而不是实时提问。

字节码证据：`get_line_num` 的全部实现就是 `for line in lines: if keyword in line: ...`，
其中 `lines` 来自 `open(tex_file, encoding='utf-8').read().splitlines()`。
`outinfo.py` 的 `LOAD_GLOBAL` 表中**没有 `input`、没有 `print`**（仅 `os`、`glob`、`PyPDF2`、
`run_info`、`re`、`sorted`、`enumerate`、`filter`、`open`、`str`、`len`、`Exception`、
`datetime`、`get_line_num`、`extract_title`、`no_chinese`、`process_folders`）。

> 若 GUI 侧（`main.py`）另有 `input()`，那不在本模块内，本规格不臆测。

### 7.1 六个 `get_line_num` 关键词（行 61–76，**按代码顺序**）

全部针对 **`.tex` 文件**的 `lines`，用 `keyword in line` 子串匹配，
命中后取 `''.join(filter(str.isdigit, line))`：

| 序 | 行 | keyword（**逐字**） | 结果变量 | 若有值写入报告的标签 |
|---|---|---|---|---|
| 1 | 61 | `输入卷号` | `vol_num` | `卷号：{v}  ` |
| 2 | 64 | `输入当年期号` | `issue_num` | `期号：{v}  ` |
| 3 | 67 | `输入出版年份` | `year_num` | `年份：{v}\n` |
| 4 | 70 | `输入起始页码` | `start_page` | `起始页码：{v}  ` |
| 5 | 73 | `输入终止页码` | `end_page` | `终止页码：{v}  ` |
| 6 | 76 | `输入页数` | `total_pages` | `页数：{v}\n` |

**这六个关键词全部是简体中文，无冒号，无空格。** 实际 tex 中它们通常出现在类似
`% 输入卷号 12` / `\newcommand{\volumnum}{12}` 之类的行上——**本模块不关心**，
只做子串包含 + 抽取数字。

### 7.2 一个 `get_line_num` 关键词（行 138，针对 **`.sty` 文件**）

| 序 | 行 | keyword | 结果变量 | 写入标签 |
|---|---|---|---|---|
| 7 | 138 | `全文行间距` | `baselinestretch` | `全文行距: {v}\n` |

注意：**关键词是「全文行间距」，输出的标签却是「全文行距」**（差一个「间」字）。这是原代码的真实写法。

### 7.3 十个 `in line` 内容扫描关键词（行 79 的 for 循环，**按代码顺序**）

对 tex 的每一行 `line` 依次判断，**第一个命中即 `continue`**（各分支互斥）：
从字节码看，每个分支结尾都是 `JUMP_BACKWARD to 860`（`continue`），
且每个 `if` 的 `POP_JUMP_FORWARD_IF_FALSE` 跳到**下一个 `if`**，构成 `if/elif` 链。

| 序 | 行 | 匹配串（**逐字**） | 附加条件 | 取值 | 输出标签 |
|---|---|---|---|---|---|
| 1 | 80 | `请输入论文题目` | — | `lines[i+1] + '\n' + lines[i+2]` → `extract_title()` | `中文标题: ` |
| 2 | 83 | `Insert Chinese title` | — | 同上 | `中文标题: ` |
| 3 | 86 | `请输入英文标题` | — | 同上 | `英文标题: ` |
| 4 | 89 | `Insert the title of your paper.` | — | 同上 | `英文标题: ` |
| 5 | 92 | `第一作者姓名` | — | **`lines[i+1]`** | `第一作者英文/中文: ` |
| 6 | 98 | `Insert the first author name.` | — | **`lines[i+1]`** | `第一作者英文/中文: ` |
| 7 | 104 | `第二作者姓名` | **`line[0] != '%'`** | **`lines[i]`** | `第二作者英文/中文: ` |
| 8 | 110 | `Insert the second author if` | **`line[0] != '%'`** | **`lines[i]`** | `第二作者英文/中文: ` |
| 9 | 116 | `第三作者姓名` | **`line[0] != '%'`** | **`lines[i]`** | `第三作者英文/中文: ` |
| 10 | 122 | `Insert the third author if` | **`line[0] != '%'`** | **`lines[i]`** | `第三作者英文/中文: ` |
| 11 | 128 | `documentclass` | — | `line.split(',')[1]` | `字号 ` |

> 表中 11 项，其中第 11 项不属于"作者/标题"族，单独列出。

**字面量精确拼写（区分大小写、含尾部标点）**
- `'请输入论文题目'` — 6 个汉字，无标点
- `'Insert Chinese title'` — 尾部**无句点**
- `'请输入英文标题'`
- `'Insert the title of your paper.'` — 尾部**有句点 `.`**
- `'第一作者姓名'`
- `'Insert the first author name.'` — 尾部**有句点**
- `'第二作者姓名'`
- `'Insert the second author if'` — **截断式**，尾部无句点，是长句前缀
- `'第三作者姓名'`
- `'Insert the third author if'` — **截断式**，尾部无句点
- `'documentclass'` — 小写，无 `\`

### 7.4 中文 / 英文判定规则

对第一/二/三作者，用 `no_chinese(author_name)` 决定标签：

```python
if no_chinese(author_name):     # 不含中文 → True
    output += f'第一作者英文: {author_name}\n'
else:                            # 含中文
    output += f'第一作者中文: {author_name}\n'
```

即：**同一篇文章里，第一作者只有一个标签**（要么"英文"要么"中文"），不会两者都出现。

### 7.5 标题分支的 `lines[i+1] + '\n' + lines[i+2]`

标题取**紧邻的两行**用 `'\n'` 拼接后送入 `extract_title`。
这与 `extract_title` 内部 `text[1:i].replace('\n', '')` 配合：
换行会被删除，因此最终标题是**两行拼接**（若标题只有一行则第二行为空）。
`i+2` 可能**越界**（`IndexError`）—— 见 §10。

---

## 8. ★ 读取 `.tex` / `.sty` 的所有匹配规则（逐条精确定义）

### 8.1 正则表达式（全文只有两处）

| # | 位置 | 模式字面量 | Python 源码 | 含义 |
|---|---|---|---|---|
| R1 | 行 10 | `'[一-龥]'` | `re.search('[一-龥]', s)` | 字符类，范围 **U+4E00–U+9FA5**（CJK 统一表意文字基本区）。含匹配→非中文返回 False |
| R2 | 行 37 | `'{([^{}]+)}'` | `re.search(pattern, text)` | 捕获 **第一组最内层 `{...}`** 的内容（组 1）。`[^{}]+` 不允许嵌套花括号 |

- R1 与 R2 **都不使用 `re.compile`**，每次调用 `re.search(pattern, string)`（模块级缓存）。
- R2 无 `^`/`$`，是**搜索**不是**全匹配**。
- R2 的 `+` 要求至少 1 个非花括号字符，所以 `{}` 空括号**不匹配**。

### 8.2 纯字符串扫描（无正则，全部为 `in` / 切片 / `split`）

| # | 行 | 代码 | 说明 |
|---|---|---|---|
| S1 | 80/83/86/89 | `'...' in line` | 子串包含 |
| S2 | 92/98/104/110/116/122 | `'...' in line` | 子串包含 |
| S3 | 104/110/116/122 | `line[0] != '%'` | **首字符**不是百分号（LaTeX 注释行被跳过） |
| S4 | 128 | `'documentclass' in line` | 子串包含 |
| S5 | 130 | `line.split(',')[1]` | 按**半角逗号**切分，取**索引 1** |
| S6 | 46/61-76/138 | `keyword in line` | `get_line_num` 的子串匹配 |
| S7 | 46/160 | `''.join(filter(str.isdigit, line))` | 取出该行**所有数字字符**并拼接 |
| S8 | 52 | `folder.split()[-1]` | 按**任意空白**切分，取最后一段 |
| S9 | 56/141 | `folder_name + '.tex'` / `+ '.pdf'` | 直接拼接扩展名 |
| S10 | 134 | `glob.glob(f'{folder_path}/*.sty')` | glob 通配，取 `[0]`（**第一个**，顺序由 `glob` 决定，通常无序/字典序） |
| S11 | 48 | `x[0].isdigit()` | 目录名**首字符**是数字 |
| S12 | 60/138 | `content.splitlines()` | 按**通用换行**切分（`\n`、`\r\n`、`\r`、`\v`、`\f`、`\x1c`-`\x1e`、`\x85`、`\u2028`、`\u2029`），**不保留行尾**，**末尾空行不产生额外元素** |

### 8.3 每个提取值的含义

| 值 | 来源 | 含义 |
|---|---|---|
| `vol_num` | tex 行含 `输入卷号` | 卷号（纯数字拼接） |
| `issue_num` | tex 行含 `输入当年期号` | 该年期号 |
| `year_num` | tex 行含 `输入出版年份` | 出版年份 |
| `start_page` | tex 行含 `输入起始页码` | 起始页码 |
| `end_page` | tex 行含 `输入终止页码` | 终止页码 |
| `total_pages` | tex 行含 `输入页数` | 声明页数（**非** PDF 实测页数） |
| 中文标题 | 含 `请输入论文题目` 或 `Insert Chinese title` 的行的**后两行** → `extract_title` | 中文标题（`{}` 内容） |
| 英文标题 | 含 `请输入英文标题` 或 `Insert the title of your paper.` 的行的**后两行** → `extract_title` | 英文标题 |
| 第一作者 | 含 `第一作者姓名` / `Insert the first author name.` 的行的**下一行** | 作者姓名原文；由 `no_chinese` 决定"英文/中文"标签 |
| 第二作者 | 含 `第二作者姓名` / `Insert the second author if` 的**当前行本身**（且首字符非 `%`） | 同上 |
| 第三作者 | 含 `第三作者姓名` / `Insert the third author if` 的**当前行本身**（且首字符非 `%`） | 同上 |
| `zihao` | 含 `documentclass` 的行按 `,` 切分取 `[1]` | **字号字符串——但见下方警告：实际取到的是 `11pt]{article}` 而非 `11pt`** |
| `baselinestretch` | **`.sty` 文件**中含 `全文行间距` 的行的数字 | 全文行间距（如 `1.25` 会被抽成 `125`） |
| `num_pages` | PDF | 实际页数 |

> ⚠️ **`字号` 取到的值被验证为「不干净」**（实测，非推测）：
>
> | `line` | `line.split(',')[1]` | 报告实际输出 |
> |---|---|---|
> | `\documentclass[a4paper,11pt]{article}` | `'11pt]{article}'` | `字号 11pt]{article}` |
> | `\documentclass[11pt]{article}` | `IndexError` | （无输出，被静默吞掉） |
>
> 因为 `split(',')` 只按**半角逗号**切分，**右花括号 `}` 不属于分隔符**，
> 所以取到的第二段**连带后面的 `]...{...}` 一起被取走**。
> **原代码没有做任何清洗**（没有 `strip(']')`、没有再次 `split`）。
> **移植时必须原样复刻这个「脏」输出**，否则下游解析会不一致。
>
> 若该行没有逗号或只有一个逗号项 → `IndexError`，被 `try/except Exception: pass` **静默吞掉**
> （行 129–133 的 `NOP` / `PUSH_EXC_INFO` / `POP_TOP` / `POP_EXCEPT` 结构），
> 该行不产生「字号」输出，**扫描继续**。

### 8.4 `.sty` 文件名的读取（行 134–138）

```python
sty_file = glob.glob(f'{folder_path}/*.sty')   # 行 134，返回 list
if sty_file:                                    # 行 135，非空列表
    with open(sty_file[0], encoding='utf-8') as f:   # 行 136，取第一个
        content = f.read()                      # 行 137
        baselinestretch = get_line_num(content.splitlines(), '全文行间距')  # 行 138
```

- `folder_path` 与 `'/*.sty'` 用 **`f-string` 拼接**（`FORMAT_VALUE 0` + `BUILD_STRING 2`），
  **硬编码正斜杠 `/`** —— 在 Windows 上 `os.path.join` 用反斜杠，但这里用 `/`。
  Windows 的 glob 同时接受 `/` 和 `\`，故可用；**macOS 同样接受 `/`**。
- 若目录下有多个 `.sty`，取 `glob` 返回的**第一个**（顺序不确定，非排序）。

---

## 9. ★ 如何获得 PDF 页数（行 141–147）

```python
pdf_file = os.path.join(folder_path, folder_name + '.pdf')   # 行 141
num_pages = 0                                                 # 行 142
if os.path.exists(pdf_file):                                  # 行 143
    with open(pdf_file, 'rb') as f:                           # 行 144  ← 二进制模式
        pdf = PyPDF2.PdfReader(f)                             # 行 145
        num_pages = len(pdf.pages)                            # 行 146
        output += f'--------------------------实际pdf页数: {num_pages}\n'   # 行 147
```

**要点**

1. `open(..., 'rb')` —— **二进制模式**，无 `encoding` 参数（字节码 `LOAD_CONST 51 ('rb')`，
   且 `PRECALL 2 / CALL 2` 无 `KW_NAMES`，即位置参数）。
2. `PyPDF2.PdfReader(f)` 接受**已打开的文件对象**（不是路径）。
3. `len(pdf.pages)` —— `pdf.pages` 是可迭代的 `_VirtualList`，`len()` 返回**页数**。
4. **只有 PDF 存在时才输出那一行**；PDF 不存在时 `num_pages` 保持 `0` 且**不输出任何行**。
5. 这一行**带 26 个连字符前缀**，是报告里最显著的分隔标记。
6. `PyPDF2` 是**顶层 import**（行 3）→ 若未安装，整个模块 import 失败。
   （新版 PyPDF2 已更名为 `pypdf`，但代码里写死 `PyPDF2`。）

### Scala/Swift 等价

Swift 无 PyPDF2 等价物内建；需用 **PDFKit**：

```swift
import PDFKit
if let doc = PDFDocument(url: pdfURL) { numPages = doc.pageCount }
```

> ⚠️ **可能不等价**：`len(PdfReader.pages)` 与 `PDFDocument.pageCount` 在
> **损坏/加密/带增量更新的 PDF** 上可能给出不同数字。
> PyPDF2 的 `pages` 属性会**跳过**某些损坏对象或触发不同解析路径。
> 建议对现有样本逐一比对两种实现的输出。

---

## 10. ★ macOS 移植注意

### 10.1 `\r\n` vs `\n`（**最重要**）

| 环节 | Windows 行为 | macOS Swift 必须做什么 |
|---|---|---|
| 写报告（行 151） | `open(..., 'w', encoding='utf-8')` **文本模式** → 所有 `\n` 翻译为 `\r\n` | 若要求字节级兼容，须**显式写出 `\r\n`**。Swift 的 `String.write` / `FileHandle` **不做**换行翻译，会写出纯 `\n` |
| 读 `.tex`（行 58） | `open(..., encoding='utf-8')` **文本模式** + `f.read()` → `\r\n` 被**通用换行**翻译为 `\n` | Swift `String(contentsOf:encoding:)` **保留** `\r` 原样 |
| `splitlines()`（行 60/138） | Python 的 `splitlines()` 按 **12 种**行边界切分（含 `\v \f \x1c-\x1e \x85 \u2028 \u2029`），去掉行尾 | Swift **没有**等价 API。`.split(separator:"\n")` 只处理 `\n`；`.components(separatedBy:.newlines)` 只处理 `\n`/`\r`/`\r\n` 等，**且不去除 `\x0b`/`\x0c`/`\u{2028}` 等** |

**建议**：macOS 上用 `String(contentsOf:encoding:.utf8)` 读入后**先统一换行**：

```swift
let normalized = content
    .replacingOccurrences(of: "\r\n", with: "\n")
    .replacingOccurrences(of: "\r", with: "\n")
```

再按 `\n` 切分。**并注意**：Python 的 `splitlines()` 在**末尾有换行时不产生尾部空字符串**，
而 Swift 的 `split(separator:)` 默认 `omittingEmptySubsequences: true`
（也会丢弃**中间**的空行！）—— 这与 Python **不一致**。
必须用：

```swift
let lines = normalized.split(separator: "\n", omittingEmptySubsequences: false)
                       .map(String.init)
```

再手动去掉末尾因最后一个 `\n` 产生的空元素，才能与 `splitlines()` 对齐。
（**这是最容易产生 off-by-one 的地方**，因为本模块大量使用 `lines[i+1]`、`lines[i+2]`。）

### 10.2 `\\` 路径分隔符 vs `/`

- `os.path.join(path, folder)` 在 Windows 产生 `a\b`；**macOS 产生 `a/b`**。
  报告内容里**不包含**路径，所以对报告无影响。
- **行 134 硬编码 `/`**：`f'{folder_path}/*.sty'`。macOS 上是正确的。
- 行 56/141 用 `os.path.join(folder_path, folder_name + '.tex'/'.pdf')` → macOS 用 `/`。
- **无盘符参与字符串拼接**，故 §10.3 的盘符问题只影响 GUI 传参。

### 10.3 盘符 / 硬编码示例路径 `E:\Github\...`

- `outinfo.py` **内部没有**任何 `E:\` 之类的硬编码路径字面量。
  `main(script_path, out_txt)` 的 `script_path` 由调用方（`main.py`）传入。
- 但错误提示串**模拟**了这类路径：
  `'请确认目录格式为例如[03 Ming_Xiao]，内部文件为[Ming_Xiao.tex]的格式'`
  —— 示例目录名 `03 Ming_Xiao`（数字+空格+下划线姓名），文件名 `Ming_Xiao.tex`。
- **`E:\Github\...` 之类的字面量出现在同项目的其他模块**（如 `main.py`），
  **不在 `outinfo.py` 中**。移植时需在 `main.py` 的规格文档中单独处理。
- macOS 上应改为 `NSOpenPanel` 选择目录，得到 `file://` URL → `url.path`。

### 10.4 编码

| 位置 | 编码 | macOS 注意 |
|---|---|---|
| 读 `.tex` | `open(tex_file, encoding='utf-8')` → **强制 UTF-8，无 BOM 容忍** | Swift `String(contentsOf:encoding:.utf8)` 同样严格。若 tex 是 GBK 或含 BOM，**Python 会抛 `UnicodeDecodeError`**（被外层 `except Exception` 捕获 → 提示目录格式错误！） |
| 读 `.sty` | 同上，`encoding='utf-8'` | 同上 |
| 读 `.pdf` | `'rb'` 二进制 | 无关 |
| 写报告 | `open(..., 'w', encoding='utf-8')` → **无 BOM** | Swift 默认 UTF-8 无 BOM，一致 |

> **BOM 陷阱**：`encoding='utf-8'`（非 `'utf-8-sig'`）**不会**去掉 BOM。
> 若 tex 带 BOM，首个 `line` 会以 `\ufeff` 开头 → 影响 `line[0] != '%'` 判断
> （BOM 行的 `line[0]` 是 `\ufeff` 而非 `%`）以及 `x[0].isdigit()`。
> Swift 需**复刻**此行为（**不要**用 `.utf8` 之外的去 BOM 逻辑）。

### 10.5 控制台 `input()`

- **本模块零 `input()`、零 `print()`**。
- 所有「交互」本质是**读取 tex 模板里预置的提示性文本行**。
- 原 GUI 通过 `run_info(out_txt, msg)` 把消息追加到 Tkinter `Text`/`ScrolledText`：
  `insert('end', ss)` + `update()` + `see('end')`。
- **macOS Swift 映射**：`run_info` → 向 `NSTextView` 追加：

```swift
func runInfo(_ textView: NSTextView, _ s: String) {
    textView.textStorage?.append(NSAttributedString(string: s))
    textView.scrollToEndOfDocument(nil)
}
```

### 10.6 其他移植点

| 项 | Python | Swift 对应 |
|---|---|---|
| `sorted(...)` | **字典序**（Unicode 码点） | `.sorted()` 对 `String` 也是 Unicode 序，但**与字节序在非 ASCII 下不同**；目录名以数字开头，前几位是 ASCII，通常一致 |
| `os.listdir` | 返回**任意顺序** | `FileManager.contentsOfDirectory(atPath:)` 也是任意顺序 → 必须显式 `sorted` |
| `glob.glob` | 顺序不保证 | 建议用 `FileManager` + 过滤 `.sty` 并**排序**后再取 `[0]`（会改变行为！见 §11） |
| `str.isdigit` | Unicode 数字（含全角 `１２`、上标 `²`？——`'²'.isdigit()` 为 **True**） | Swift `Character.isNumber` **不等价**；应用 `isASCII && isNumber` 或精确复刻 `str.isdigit` 语义 |
| `datetime.now()` | 本地时区 | `Date()` + 本地 `Calendar`/`DateFormatter`，注意 `yyyy` 用 `en_US_POSIX` |
| `strftime('%Y_%m_%d_%H_%M_%S')` | 本地时区、无前导零选项、年份 4 位 | `DateFormatter` with `dateFormat = "yyyy_MM_dd_HH_mm_ss"`，**必须 `locale = Locale(identifier: "en_US_POSIX")`** 以免历法/数字本地化（如阿拉伯数字） |
| 写文件 `'w'` | 截断覆盖 | `try output.write(to: url, atomically: true, encoding: .utf8)` — **注意 `atomically: true` 会先写临时文件再重命名，可能改变文件权限/元数据** |
| 整数转字符串 | Python 3 无 `L` 后缀 | Swift 一致 |

---

## 11. ★ 边界情况与错误处理

### 11.1 显式异常处理

| 位置 | 结构 | 行为 |
|---|---|---|
| 行 129–133（`documentclass` 分支） | `try: ... except Exception: pass` | `line.split(',')[1]` 的 `IndexError` 被**静默吞掉**，不写「字号」行，**继续扫描下一行**（`JUMP_BACKWARD to 860` 即 `continue`） |
| 行 153–155（`process_folders` 全体） | `except Exception as e:` | `run_info(out_txt, e)` 然后 `run_info(out_txt, '请确认目录格式为例如[03 Ming_Xiao]，内部文件为[Ming_Xiao.tex]的格式')`，然后 `return None`（**不重新抛出**） |
| 行 169–171（`main` 全体） | `except Exception as e:` | 同上的两条 `run_info`，然后 `return None` |

**关键后果**：`process_folders` 的 `try` 从 `offset 6` 覆盖到 `422`（行 48 起）——
即**整个函数体**都在 try 内。任何一个文件夹的异常都会**中止整个扫描**，
**已累积在 `output` 中的内容全部丢弃**（因为 `f.write(output)` 在 try 内、异常之后）。
报告文件**不会被创建/被截断为 0 字节**（`open(..., 'w')` 尚未执行）。

> 若异常发生在 `open(..., 'w')` **之后**（如 `f.write` 失败），文件已被截断，可能留下不完整文件。

### 11.2 索引越界风险（**真实存在**）

| 位置 | 表达式 | 越界条件 | 后果 |
|---|---|---|---|
| 行 81/84/87/90 | `lines[i + 2]` | 匹配行位于**倒数第 1 或第 2 行** | `IndexError` → 被外层 `except Exception` 捕获 → **整个扫描中止**，报告不生成 |
| 行 81/84/87/90 | `lines[i + 1]` | 匹配行是**最后一行** | 同上 |
| 行 93/99 | `lines[i + 1]` | 「第一作者」行是最后一行 | 同上 |
| 行 105/111/117/123 | `lines[i]` | 不会越界（i 来自 `enumerate`） | — |
| 行 152 | `line[0]` | 行 104/110/116/122 分支 | **空行**（`''`）→ `IndexError`！`splitlines()` 会保留中间空行 | 被外层捕获 → **整个扫描中止** |
| 行 48 | `x[0]` | `os.listdir` 返回**空字符串名**（不可能） | 实际不会发生 |
| 行 130 | `line.split(',')[1]` | 无逗号 → 列表长度 1 | `IndexError`，被**内层** `try/except` 吞掉 |
| 行 52 | `folder.split()[-1]` | `folder` 为空串 → `[]` | `IndexError`，被外层捕获 |

> ⚠️ **行 104 的空行陷阱最隐蔽**：`if '第二作者姓名' in line and line[0] != '%'`
> 中 Python 会**先**求 `'第二作者姓名' in line`；若为 `False` 则**短路**，
> 不会求值 `line[0]`。所以空行只有在**同时包含**该子串时才崩——但空行不可能包含，
> 因此实际安全。**Swift 必须复刻短路求值顺序**，否则空行会崩。

### 11.3 `no_chinese` 的边界

- `no_chinese('')` → `re.search` 返回 `None` → **返回 `True`**（空串被视为"无中文"）
  → 会输出 `第一作者英文: \n`（空值）。
- `no_chinese('张 San')` → 匹配到 `张` → `False` → 输出「中文」标签。

### 11.4 `extract_title` 的边界

**以下全部经实际运行重构代码验证（非推测）**：

| 输入 | 返回 | 路径 | 原因 |
|---|---|---|---|
| `'{标题}'` | `'标题'` | 循环 | i=0 `{` 被 `continue`（flag 仍 0）；i=3 `}` → flag=-1 → `text[1:3]` |
| `'{a{b}c}'` | `'a{b}c'` | 循环 | i=0 `{` 跳过；i=2 `{`→flag=1；i=4 `}`→flag=0；i=6 `}`→flag=**-1** → `text[1:6]` |
| `'{A}\n{B}'` | `'A'` | 循环 | 第一个 `}`（i=2）即触发 → `text[1:2]='A'`；**跨行内容被截断** |
| `'中文{a}b'` | `'a'` | **正则回退** | 首字符 `中` 不是 `{`，故永不 `continue`；i=2 `{`→flag=1，i=4 `}`→flag=0，flag 从未到 -1 → 循环走完 → 正则 `{([^{}]+)}` 命中 `{a}` → `'a'` |
| `'{}'` | **`''`** | 循环 | i=0 `{` 跳过；i=1 `}` → flag=-1 → `text[1:1]` = `''`。**注意：不是 `None`！** |
| `'}'` | **`''`** | 循环 | 单字符，i=0 就是 `}` → flag=-1 → `text[1:0]` = `''` |
| `''`（空串） | `None` | 正则回退 | 循环不执行；`{([^{}]+)}` 不匹配 → 返回 `None` |
| `'abc'` | `None` | 正则回退 | 无 `{`/`}`，flag 恒 0；正则不匹配 |
| `'{abc'`（未闭合） | `None` | 正则回退 | 循环中 flag=1，永不 `-1`；正则不匹配 |

> ★ **两条返回路径的判定法则**（务必按此实现，不要用"提取花括号内容"的直觉）：
> 1. **循环路径**触发当且仅当：从索引 1 起**首次**出现 `}` 时，其**之前**的非首位 `{` 数量为 0。
>    此时返回 `text[1 : 该 } 的下标]`，并 `replace('\n','')`。
>    → 结果是**第一个 `}` 之前从索引 1 开始的原文**。
> 2. **正则路径**仅在循环**完整跑完且从未返回**时执行，
>    即 `flag` 从未降到 -1（典型情况：首字符不是 `{`，或花括号完全配平）。
>    → `re.search('{([^{}]+)}', text)` 的组 1。
> 3. 两条路径都失败 → `None`。
>
> 注意路径 1 的**副产品**：首字符不是 `{` 时，`text[1:i]` 会**从索引 1 开始**截断，
> 丢掉索引 0 的字符。例如 `'X{A}B}'` 这类输入需按规则实机核对。

> ★ **`'{}'` 返回空串 `''` 而非 `None`**（因为 `flag` 在 `}` 处变 -1，走的是**循环返回**路径，
> 不是正则路径）。这一细节会直接影响报告输出：
> `f'中文标题: {""}\n'` → `中文标题: \n`（**行存在但值为空**）；
> 而 `None` → `中文标题: None`。两者**不同**。

> ⚠️ `extract_title` 返回 `None` 时，报告里会写成字面量 **`None`**
> （`f'中文标题: {None}\n'` → `中文标题: None`）—— Swift 需**复刻**此行为，
> 不能输出空串或 `null` 的不同拼写。

### 11.5 其他

| 情况 | 行为 |
|---|---|
| `path` 不存在 | `os.listdir` 抛 `FileNotFoundError` → 外层捕获 → 提示目录格式 |
| 目录下无「首字符为数字的子目录」 | `folders = []` → `output = ''` → **仍会写出一个 0 字节的 `信息检查<时间>.txt`** |
| 文件夹名为 `Contents` | `continue`，完全跳过（**注意是 `split()[-1]` 之后比较**，所以 `03 Contents` 也会被跳过） |
| tex 存在但 pdf 不存在 | 不输出「实际pdf页数」行，但**仍有**行 148 的空行 |
| tex 不存在 | 跳过 tex 解析与行 128 分支，**仍执行** sty / pdf 处理 |
| 多个 `.sty` | 取 `glob` 返回的第一个（顺序**不保证**） |
| 同时有 PDF 与同名 `.tex` | 二者独立处理 |

---

## 12. 不确定之处（genuinely ambiguous）

1. **`process_folders` 的参数名**
   - 确认：3 个位置参数；第 3 个是 `out_txt`（行 154/3126 传给 `run_info`）；
     第 1 个是 `path`（`MAKE_CELL 0 (path)`，被 `<genexpr>` 闭包通过 `LOAD_DEREF 0 (path)` 捕获）；
     第 2 个在第 151 行 `os.path.join` 中与 `'信息检查...'` 拼接。
   - `dis` 输出的 `LOAD_FAST 1` / `LOAD_FAST 2` **未标注名字**，因此
     **`output_dir` 与 `out_txt` 的形参名是推断的**，但**顺序确定**（1=path, 2=output_dir, 3=out_txt）。
   - **推断依据**：`main` 用 `process_folders(script_path, script_path, out_txt)`
     且第 2 个实参来自 `LOAD_FAST 0`（= `$script_path`），与 `output_dir` 用途吻合。

2. **`main` 的参数名**
   - 第 1 个 `LOAD_FAST 0 (script_path)`，第 2 个 `LOAD_FAST 1 (out_txt)`。
   - `out_txt` 名来自 `run_info` 的形参名（`run_info.py` 中确为 `out_txt`），**高度可信**。
   - `script_path` 名是**推断**（dis 显示变量名时保留了 `co_varnames`，此处
     `LOAD_FAST 0 (script_path)` 已由 dis 打印出名字 → **实际是确定的**）。

3. **`3.2` 中 `folder.split()` 的语义**
   - `LOAD_METHOD 5 (split)` + `PRECALL 0` + `CALL 0` → **无参数** `split()`，
     即按任意空白（含连续空白、制表符）切分并丢弃空段。**确定**。

4. **行 148 的缩进层级**
   - 已由字节码跳转**确定**（见 §6.6）：在 `for folder` 循环体内、
     `if os.path.exists(tex_file)` 块**之外**，且**无条件执行**。

5. **`stdout` 交互是否存在**
   - 本模块内**确无** `input()`/`print()`。若产品行为上编辑确实被"询问"，
     那是在 tex 模板生成阶段（其他模块），**不在本文件**。

6. **`字号` 是否真的等于字号**
   - 代码取 `documentclass` 行 `split(',')[1]`。在标准 `article` 模板中
     `\documentclass[a4paper,11pt]{article}` 的索引 1 是 `11pt`（a4paper 为索引 0）。
     但若模板写成 `\documentclass[11pt]{article}`（无逗号）→ `IndexError` → 静默跳过。
     **若模板把 `11pt` 放在索引 0**，则取到的是别的值。本模块**不验证**。

7. **`PyPDF2` 版本**
   - 代码用 `PyPDF2.PdfReader`（PyPDF2 ≥ 2.0 的 API）。旧版是 `PyPDF2.PdfFileReader`。
     移植到 macOS 时无法直接复用 Python 库，须用 PDFKit 并逐样本校核页数。

8. **报告是否真的需要 CRLF**
   - 这取决于「生成目录」读取端的实现。**若读取端也在 Windows 上以文本模式读取**，
     则 CRLF 会被自动翻译回 `\n`，无所谓。
   - **但若读取端按字节/正则处理**，则换行符必须一致。
     **建议**：先检查「生成目录」模块（`charti_build_content.py`）的读取方式再定夺。
     本规格**如实记录** Windows 下写出的是 CRLF。

9. **`glob.glob` 顺序与 `sorted` 的稳定性**
   - `glob.glob` 在现代 Python 中**不保证**排序（早期版本依赖 `os.scandir` 顺序）。
     若要确定性，需 `sorted(glob.glob(...))`，但**原代码没有**——移植时若加排序会改变行为。
     建议**保留**原语义（取第一个）并在测试中记录实际文件名。

10. **`output` 中不存在任何标题/分隔头**
    - 报告**没有**总标题、没有期刊名、没有生成时间戳写入正文。
      第一行就是第一个文章的 `folder_name`。**确定**（行 49 `output = ''`，
      行 55 是第一次追加）。

---

## 13. ★ 端到端实测验证（参考夹具）

用 §1–§5 重构的代码在合成目录上实际运行，确认了全部关键行为。

### 13.1 输入夹具

```text
2026年第5期(90)/
├── Contents/                     # folder_name == 'Contents' → 被 continue 跳过
├── 01 Wang_Li/                   # 无 .tex / .sty / .pdf
└── 03 Jie_Zhou/
    ├── Jie_Zhou.tex
    └── journal.sty
```

`03 Jie_Zhou/Jie_Zhou.tex`（内容刻意包含各种边界）：

```latex
% 输入卷号 12
% 输入当年期号 5
% 输入出版年份 2026
% 输入起始页码 105
% 输入终止页码 118
% 输入页数 14
\newcommand{\cntitle}{请输入论文题目}
{论指称的模糊性}
{}
\newcommand{\entitle}{Insert the title of your paper.}
{On the Vagueness of Reference}
{}
第一作者姓名
Jie Zhou
第二作者姓名 Zhang, Xiaoming
Insert the third author if
第三作者中文: % commented
\documentclass[a4paper,11pt]{article}
```

`03 Jie_Zhou/journal.sty`：

```latex
\baselinestretch{1.25}
```

### 13.2 实际输出（`repr`，确认精确换行与空格）

```python
'Wang_Li\n\nJie_Zhou\n'
'卷号：12  期号：5  年份：2026\n'
'起始页码：105  终止页码：118  页数：14\n'
'中文标题: 论指称的模糊性\n'
'英文标题: On the Vagueness of Reference\n'
'第一作者英文: Jie Zhou\n'
'第二作者中文: 第二作者姓名 Zhang, Xiaoming\n'
'第三作者英文: Insert the third author if\n'
'字号 11pt]{article}\n'
'\n'
```

### 13.3 实测确认的六条关键行为

1. **`01 Wang_Li` 只有一行**：`Wang_Li\n` + `\n`（`output += '\n'` **无条件执行**，
   即使该目录既无 tex 也无 pdf）→ 证实 §6.6 的结论。
2. **`Contents` 完全不出现在报告中**（`continue`）。
3. **字段顺序确认为**：`卷号 → 期号 → 年份\n` 然后 `起始页码 → 终止页码 → 页数\n`。
   §6.5 的"修正后的真实版"是**正确的**。
4. **`字号 11pt]{article}`**：证实 §8.3 的警告——`split(',')[1]` 会带走右花括号之后的内容。
   **原代码不做任何清洗**。
5. **`第二作者中文: 第二作者姓名 Zhang, Xiaoming`**：因为第二/第三作者分支取的是
   **`lines[i]`（当前行本身，即含关键词的那一行）**，而不是下一行。
   所以输出里**把提示关键词自己也带进去了**。这是原代码的既有行为，
   下游「生成目录」必须能容忍。
6. **`第三作者英文: Insert the third author if`**：同理，取的是**关键词行本身**。
   并且该行首字符是 `I`（非 `%`），所以通过了 `line[0] != '%'` 检查。
   → 若 tex 模板中该提示行**未**以 `%` 注释掉，就会被当成作者名输出。
7. **`全文行距` 未输出**：因为 `.sty` 里写的是 `\baselinestretch{1.25}`，
   不含关键词 `全文行间距`，`get_line_num` 返回 `''`（假）→ 跳过。
   若模板写成 `% 全文行间距 1.25`，则输出 `全文行距: 125`（**小数点被 `isdigit` 过滤掉**）。

### 13.4 用于 Swift 回归测试的建议

移植时，把 §13.1 夹具与 §13.2 的 `repr` 输出做成**黄金用例**，
逐字节比对 Swift 实现产出的字节（含 `\r\n` → `\n` 归一化后再比，或直接比 CRLF 版本）。

---

## 14. 移植检查清单（Checklist）

- [ ] 报告文件名 = `信息检查` + `yyyy_MM_dd_HH_mm_ss`（**本地时区**）+ `.txt`
- [ ] 报告写在**传入的期目录内**（`main` 用同一个 `script_path` 作两个实参）
- [ ] 写文件用 UTF-8 **无 BOM**；换行按 §10.1 决定是否 CRLF
- [ ] 目录筛选：是目录 + 名字**首字符是数字** + `sorted` 字典序
- [ ] `folder_name = folder.split()[-1]`（**空白切分取最后一段**）
- [ ] `folder_name == 'Contents'` → 跳过
- [ ] tex 路径 = `<folder_path>/<folder_name>.tex`；pdf 同理
- [ ] 六个中文 `get_line_num` 关键词顺序：卷号/当年期号/出版年份/起始页码/终止页码/页数
- [ ] `get_line_num` 返回 `''` 而非 `nil`；数字抽取 = 该行所有数字字符拼接
- [ ] 标签分隔：卷/期/起/终 → 两个半角空格；年份/页数 → `\n`
- [ ] 十一个 `in line` 关键词**逐字**正确（注意 `Insert the title of your paper.` 与
      `Insert the third author if` 一个有句点一个没有）
- [ ] 标题取 `lines[i+1] + "\n" + lines[i+2]`；作者第一取 `lines[i+1]`，第二三取 `lines[i]`
- [ ] 第二/三作者需 `line[0] != '%'`（**短路求值顺序**）
- [ ] `no_chinese` 用 U+4E00–U+9FA5 判定「英文/中文」标签
- [ ] `extract_title` 两条返回路径 + `None`
- [ ] `documentclass` 行做 `split(',')[1]` 且**不清洗** + `try/catch` 静默忽略
- [ ] `.sty` 用 glob 取第一个，读 `全文行间距`，标签输出 `全文行距: `（**关键字与标签不同**）
- [ ] PDF 用 `PDFDocument.pageCount`，**逐样本与 PyPDF2 对数**
- [ ] 页数行前缀 = 26 个 `-` + `实际pdf页数: `
- [ ] 每个文件夹块末尾无条件一个空行
- [ ] 顶层 `try/except Exception` 两条 `run_info` 提示，**不重抛**
- [ ] `run_info` → 追加到 `NSTextView` 并滚到底
