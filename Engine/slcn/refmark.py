"""给编辑（和他们各自的 AI）的**参考文献核实任务书**。

为什么不再用 CrossRef（2026-10-14 按编辑部要求移除）
----------------------------------------------------
1. **中文文献覆盖差**：全库实测「未查到」里绝大多数是中文文献、集刊、学位论文。
2. **会给出错配的「建议」**：09 Jiawei_Chu 的 18 条里，`Eemeren2010`（书）被匹配到
   Informational Logic 的期刊论文（2012）、`Rieke1984`（书）被匹配到 1997 年会议论文
   （页码 `8--8`）。编辑照着改就是照错改。
   （全库受控对比：同一份语料里，跨类型错配占「有出入」的 **228/1301 = 17.5%**。）
3. **不该绑定某一家 AI**：编辑部每个人用的模型不同，工具只负责**把材料准备好**。

所以改为：生成一份 markdown —— 里面写清**编辑部的 bib 规范**、**查证来源优先级**、
**每条文献缺什么**、以及**固定回答格式**；编辑把它交给自己的 AI（或自己查），
把回答粘回来即可。想自动化的，仍可用 `SLCN_LLM_*`（OpenAI 兼容，见 `slcn/llm.py`），
工具会按**同一份规范**调用它。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import bibcheck

# ── 编辑部 bib 规范（写进任务书，也是给 AI 的 system prompt）────────────────
#
# 每一条都来自本项目实测过的规则（README 第 20/21/32/34 条）或《排校手册》原文，
# 不是凭印象写的；括号里的数字就是实测口径。
SPEC_MD = """\
## 一、编辑部 bib 规范（**必须遵守**）

### 1. 条目类型按文献实际类型选
| 文献 | 用什么 | 必备字段（缺一不可） |
|---|---|---|
| 期刊论文 | `@article` | author, title, year, journal, volume, number, pages |
| 专著 | `@book` | author, title, year, publisher, address（再版另加 edition） |
| 书中章节 / 论文集里的文章 | `@incollection` | author, title, booktitle, year, publisher, pages |
| 会议论文 | `@inproceedings` | author, title, booktitle, year, pages |
| 中文学位论文 | `@thesis` | author, title, type={博士学位论文}/={硕士学位论文}, institution, date |
| 英文学位论文 | `@phdthesis` / `@mastersthesis` | author, title, school, year |

- `@conference` 一律改成 `@inproceedings`。
- 类型判错是最常见的问题：**书被当成期刊论文**、**书中章节被当成书**都要改。

### 2. 字段写法（照抄这些惯例）

- **（核实范围：**全字段**都要对官方来源核一遍）**：author / title / 类型 /
  journal 或 booktitle / volume / number / pages / publisher / address / edition / year
  —— **一个字段都不许跳过**，包括你「觉得没问题」的那些。
  为什么要把范围写死：编辑与 AI 都习惯只盯一两个显眼项（实测最常见的是只看卷/期），
  而 **address（出版地）、edition（版次）、类型**恰恰是书籍与章节条目最容易错的地方
  （全库实测：`@book` 的 address 出现率只有 61%），类型判错还会把整条的必备字段表换掉
  （本表第 1 条）。**核实 ≠ 补全**：只写来源确认过的值；来源里查不到的字段**照旧省略**，
  并在条目上方写 `% ⚠ 待核实：<字段>（原因）`（第三节第 6 条）——「编错的卷号比空缺更糟」。
- **每条核实过的条目，都要在它上方写一行来源标注**：
  `% 来源：<官方页面 URL 或来源说明>`（与「待核实」注释同一落点：bibtex 代码块**内部**、
  紧挨条目的上一行，见第三节第 11 条）。
  为什么必须有它：**没有这一行，「查过官方来源」与「凭记忆写的」在产物里长得一模一样** ——
  编辑事后完全分不出哪些条目可以信、哪些要重查。
  URL 拿不到时写来源说明（`% 来源：WorldCat 书目页（未记下 URL）`），
  **绝不许编一个看起来像 URL 的字符串**。
- **页码用双短横**：`pages = {24--43}`（《排校手册》：页码范围用双短横，不是短横）
- **期号去掉前导零**：`number = {3}`，不要写 `03`
- **月份保留三字母缩写**：`month = {nov}`，不要写 `11`
- **中文文献必须带 `userf = {cn}`**：`SCLN-MIX.sty` 据此把标点改成中文样式。
  判定只看 `author` 与 `title`：含汉字 → `cn`；含日文假名 → `jp`；纯英文 → **不填**。
  （全库实测：cn 1038 条、jp 45 条、不填 3551 条；人工已标注的 1642 条里判定一致 1640 条 = 99.9%。
  ⚠️ **不要**用 note、publisher 等出版信息去做这个判定。）
- **英文作者 / 编者一律倒装成「姓, 名」**（编辑部 2026-10-15 定，**硬规则**）：
  `author = {Dillard, J. P. and Segrin, C. and Harden, J. M.}`。
  · **源稿写「名 姓」也要转过来**——这不是「修改」，是套用体例（见下条「非必要不修改」的例外）。
    实测依据：`2026年第5期(90)/01 Jingru_Kou` 人工成品里英文条目 **20/20 全倒装**。
  · 全名缩成**首字母**，**中名首字母全部保留**：`Jonathan L. Kvanvig` → `Kvanvig, J. L.`；
    `A. J. Ayer` → `Ayer, A. J.`；`Jerry L Morgan` → `Morgan, J. L.`。
  · 多作者用 `and` 连接、**每一个**都倒装；**同一条里不能混用**两种写法。
  · ⚠️ BibTeX 里**逗号是「姓, 名」的倒装符**：写成 `{J. P. Dillard, C. Segrin and J. M. Harden}`
    会编译成「**C. S. J. P. Dillard and J. M. Harden**」这种错名（编辑部实测）。
  · 虚词 `van`/`von`/`de` **小写**、随其后的姓氏参与排序：`B. C. van Fraassen` → `van Fraassen, B. C.`；
    要按 V 排序就写波浪号 `van~Fraassen, B. C.`（《排校手册》）。
  · 编者标记写在姓后的花括号里：`J. Salerno (ed.)` → `{Salerno (ed.)}, J.`。
  · **中文、日文姓名不倒装**（`党学哲`、`太田方`、`焦循` 原样）。
- **中文多作者仍用 `and`**：`author = {刘淘宁 and 顿新国}`（源稿的顿号/逗号要改）；
  **但中文译著例外，见下一条**。
- **中文译著写「原作者（著），译者（译）」**（《排校手册》源码 `reference.bib` 的实例：
  `焦循（著），沈文倬（点校）`、`黑格尔（著），杨一之（译）`）：
  `author = {阿斯海姆（著），张建军、万林（译）}`、`{克里普克（著），梅文（译）}`、
  `{沃斯、费布鲁赫、库伊（著），黄海婷（译）}`。
  ⚠️ 这里**保留中文逗号与顿号**，**不要**改成 `and`——`（著）`/`（译）` 是姓名的一部分，
  这个逗号不是多作者分隔符；多个原作者之间用顿号，多个译者之间也用顿号。
  （引擎侧的自动解析只到「作者字段不倒装中文姓名」这一步，译者的补全靠这一条人工/AI 判断。）
- **出版地写进 `address`**：书籍尤其重要（实测人工 bib 里 61% 的书带 address）
- **斯坦福哲学百科（SEP）词条有固定形状**（编辑部 2026-10-15 定；成品 `10 Bo_Chen` 的
  `Steinberger2022` 就是这一形状）：**整条照下面的样子写，不要一条一个样**。

  ```bibtex
  @incollection{Steinberger2022,
    author    = {Steinberger, F.},
    title     = {The Normative Status of Logic},
    booktitle = {The Stanford Encyclopedia of Philosophy},
    editor    = {Zalta, E. N. and Nodelman, U.},
    edition   = {Winter 2022},
    publisher = {Metaphysics Research Lab, Stanford University},
    year      = {2022},
    url       = {https://plato.stanford.edu/archives/win2022/entries/logic-normative/}
  }
  ```

  - **类型**：`@incollection`（不要写成 `@article` / `@misc` / `@online` / `@InProceedings`）。
  - **`booktitle` 固定写 `The Stanford Encyclopedia of Philosophy`**：不要带 `(Winter 2022)` 这类尾巴——
    季节与年份归 `edition` 与 `url`。
  - **`editor` 照该词条页当前给的写**：近年是 `Zalta, E. N. and Nodelman, U.`，
    更早的存档只有 `Zalta, E. N.`。**照你引用的那一版存档页写**，不要照抄别的年份。
  - **`edition` = 存档季节 + 年份**：`Winter 2022` / `Fall 2025` / `Spring 2016`。
    ⚠️ **往期刊物不统一：有的条目有这个字段、有的没有**——**官网上写了就写，没写就不补**，
    更不要从 URL 里「推」一个出来（第 3 条：不确定的值不要猜）。
  - **`url` = 带存档路径的永久链接**（`…/archives/win2022/entries/logic-normative/`）：
  它与 `edition` 的季节/年份**必须对得上**；不要写 `…/entries/logic-normative/` 这种「当前版」短链接。
  - **`publisher` 固定写 `Metaphysics Research Lab, Stanford University`**；
   **不写 `address`**（官网 bib 没有这个字段），**不写 `pages`**（词条没有页码）。
  - **这些值去哪里拿**：词条页面自带官方的 **BibTeX / 引用**块，照它抄，再按下面这张表改成我们的形状：

    | 官网 biblatex 里 | 我们写成 | 为什么 |
    |---|---|---|
    | `howpublished = {\\url{…}}` | `url = {…}` | 我们的样式只认 `url`，`howpublished` 印不出来 |
    | `booktitle = {The {Stanford} Encyclopedia of Philosophy}` | `booktitle = {The Stanford Encyclopedia of Philosophy}` | 本刊 bib 不写保护花括号 |
    | `edition = {{F}all 2025}` | `edition = {Fall 2025}` | 同上 |
    | `urldate = {…}` | **删掉** | 本刊不写访问日期 |
    | `key = sep-logic-preference` | **换成我们自己的 key**（第三节第 1 条） | 正文 `\\parencite{…}` 用的是我们的 key——**改 key 必须同时确认正文那一处也改了**，否则引用会悬空 |

  - 官网给的 `author` / `editor` 是「名 姓」，**按本表第一条照旧倒装**
    （`Florian Steinberger` → `Steinberger, F.`；`Edward N. Zalta and Uri Nodelman` →
    `Zalta, E. N. and Nodelman, U.`）。
    ⚠️ 你可能会在往期成品里看到不倒装的写法（`10 Bo_Chen` 那份 bib 就是）：**两种写法印出来完全一样**
    ——SLCN 样式统一按「名首字母. 姓」排印（2026-10-15 用成品 PDF 核对过），
    所以这里按编辑部已定的硬规则走，不必为此改样例。
- **英文条目的标题会被排成「句首大写」——专名、副标题首字母等必须用花括号保护**（编辑部 2026-10-15 定；
  机制在**模板源码** `编辑笔记/模板文件/SLCN-MIX.sty` 第 733 行——不是风格偏好）：

  ```latex
  \\DeclareFieldFormat[article,inproceedings,incollection,misc]{title}
      {\\cegen{“#1”}{``\\MakeSentenceCase{#1}''}}   % userf=cn 走中文分支（不改大小写），否则**句首大写**
  ```

  也就是说：**不带 `userf = {cn}` 的** `@article` / `@incollection` / `@inproceedings` / `@misc` 条目，
  标题会被 `\\MakeSentenceCase` 处理，**没被保护的大写字母会被压成小写**
  （成品 PDF 实证：`{Two Dogmas of {E}mpiricism}` 漏掉那个 `{E}` 之后印成 `…of empiricism`）。
  必须保护的有四类：

  | # | 保护什么 | 怎么写 | 例子 |
  |---|---|---|---|
  | 1 | **专名 / 术语的首字母** | 用 `{}` **只裹住那个字母** | `title = {Two Dogmas of {E}mpiricism}` |
  | 2 | **副标题（冒号后）的首字母** | 同上 | `title = {A Cognitive Approach to Human Communication: {A}n Action Assembly Theory}` |
  | 3 | **罗马数字 / 编号 / 单字母变量** | 同上 | `Part {I}`、`Generalizing {L}`、`{M}aps of {b}ounded {r}ationality` |
  | 4 | **标题里不想被当成标题正文的括号说明** | `\\textup{(}…\\textup{)}` | `Introduction to Logic \textup{(}14th Edition \textup{)}` |

  ⚠️ **`@book` 的标题不做句首大写**（模板对 `[book]` 只加斜体），书名的保护可有可无；
  **中文条目（`userf = {cn}`）也一律不需要**保护。
  ⚠️ **只裹住需要保护的那一个字母，不要整段加括号**：`{Two Dogmas Of Empiricism}` 会把整句锁死。
- **英文书籍的版次写进 `title`，不单列 `edition`**（编辑部 2026-10-15 定；模板 `reference.bib` 里
  一个 `edition` 字段都没有）：

  ```bibtex
  @book{a7,
    author    = {Toulmin, S. E.},
    year      = {2003},
    title     = {The Uses of Argument, (Updated Edition)},
    address   = {Cambridge},
    publisher = {Cambridge University Press},
  }
  ```

  · 版次写在书名之后、**放进括号**，与书名之间**加一个逗号**：`… Argument, (Updated Edition)`。
  · **工具会自动搬**：`convert.py` 产出 `reference.bib` 时，凡是**英文 `@book`** 单列的 `edition`
    都会被并进书名（并在改动说明里记一笔）——你照规范写就行，漏了也不会印错位置。
  · ⚠️ **例外：SEP 词条**的 `edition` 是「存档季节 + 年份」，**照旧单列**（见上一条）。
  · ⚠️ 往期成品里出现过把版次塞进 `address` 的写法（`address = {Second Edition, London}`），
    那是权宜之计，**不要再学**。
- **只有编者、没有作者的书（编文集）：编者写进 `author` 并加 `(eds.)`**（编辑部 2026-10-15 定）；

  ```bibtex
  @book{a17,
    author    = {{F. H. van Eemeren and P. Wu~(eds.)}},
    year      = {2017},
    title     = {Contextualizing Pragma-dialectics},
    address   = {Amsterdam},
    publisher = {John Benjamins},
  }
  ```

  · **外面多套一层花括号**（`{{…}}`）：让 BibTeX 把这串当成**一个整体名**，不去拆「名/姓」；
  · 编者**超过 3 人时缩为 2–3 人**再加 `(eds.)`（全列太长）；
  · `(eds.)` 与最后一位编者之间用**不断行空格 `~`**：`P. Wu~(eds.)`；单人编者写 `(ed.)`；
  · **单列 `editor` 只用于「有作者、另有编者」的情形**（例如 SEP 词条：`author` 是词条作者、
    `editor` 是百科主编）。
- **作者 / 编者人数过多**（《排校手册》§校对规范·参考文献 第 2 条）：**作者 > 4 人、或编者 > 3 人**时
  写 `{D. Lewis and others}`（手册原文「只需在 bib 文件对应的括号里填写为 `{D. Lewis and others}`」）。
  ⚠️ 与上一条的分工：**编文集**（只有编者、没有作者）用 `(eds.)` 那种形态；
  **普通多作者条目**的长名单用 `and others`。
- **不要写 `doi` / `isbn`**（编辑部 2026-10-14 定）：这两个编号**只用来核实**——
  拿它们去查官方页面、确认卷期页码出版地，但**最终 bib 里不保留这两个字段**。
- **不要编造**：查不到就写 `未查到`；**同一篇文章的标准要统一，非必要不修改**（《排校手册》原话）。
  ⚠️ **作者字段是这条的例外**：英文作者一律倒装是编辑部已定的体例，
  源稿写成「名 姓」时必须转换（见本表第一条），改字段写法本身不算「不必要的修改」。
- **逐条清单里给了「源稿原文」就以它为准**（2026-10-15，第 66/68 条的教训）：
  若**源稿原文**与「解析得到的条目」不一致（原文里有刊名/卷期/页码/作者而条目缺，
  或同一字段的值不同），按**源稿原文**补全或纠正字段——我们解析源稿时可能丢字段，
  那是工具的错，不是源稿的错。
  ⚠️ **原文里没有的信息仍然不许编**（查不到就写 `未查到` / 省略该字段，见上一条）：
  「以原文为准」是**上限**，不是「可以拿常识补全」的许可。
  没有「源稿原文」的条目（例如作者自带的 `.bib`）按原样核实，不要凭空造一段原文出来。

### 3. 这一批文献的已知问题（离线核对结果，见下面逐条清单）
"""

SOURCES_MD = """\
## 二、查证来源（按优先级，请优先用**官方**来源）

1. **期刊官网 / 出版社官网** —— 卷、期、页码、DOI 以官网为准（最权威）。
2. **中文文章 → 知网（CNKI）**：https://www.cnki.net
   查中文期刊文章的卷、期、页码、作者单位。
3. **书籍 → WorldCat**：https://search.worldcat.org/
   查**出版地**、**版本**（第几版）、出版年、出版社全名——这几项最容易错。
4. 其他可用：DOI 解析（https://doi.org/…）、出版社书目页、国家图书馆、Google Books。
5. **斯坦福哲学百科（SEP）词条**：直接用它**词条页自带的官方 BibTeX / 引用块**
   （一次给全：存档 URL、季节版次、编者、出版者），再按第一节第 2 条那张表改成我们的形状。
   这是本条里**唯一**「照着抄就够」的文献类型——不要凭记忆拼 SEP 的字段。

⚠️ 聚合站、二手转载、AI 记忆里的字段都不可靠，**不要**作为唯一依据；
   AI 若无法联网核实，请明确写「未查到」，不要猜。
⚠️ **能用检索工具就必须用**：若你（或调你的工具）有联网检索能力，请**先检索**上面这些官方
   来源，再逐字段比对；把**实际打开过**的页面 URL 写进每条上方的 `% 来源：` 标注
   （第三节第 11 条）。检索不到就照旧省略字段 + `% ⚠ 待核实：…`，**不要**用记忆顶替检索结果。
"""

FORMAT_MD = """\
## 三、请交付**完整、可直接编译的 .bib 文本**

不要输出「`key: field = value`」这种差异清单——编辑要的是**能直接替换 `reference.bib`
并立刻编译的整份 BibTeX**。请把所有条目（**包括没有改动的**）完整写在**一个**代码块里：

````
```bibtex
@article{JPDillard1989,
  author  = {Dillard, J. P. and Segrin, C. and Harden, J. M.},
  title   = {Primary and Secondary Goals in the Production of Interpersonal Influence Messages},
  journal = {Communication Monographs},
  volume  = {56},
  number  = {1},
  pages   = {19--38},
  year    = {1989},
  doi     = {10.1080/03637758909390247}
}
```
````

硬性要求：

1. **key 一律不改**——正文里的 `\\parencite{key}` 靠它对应，改 key 会让全文引用悬空。
2. **英文作者 / 编者一律倒装**（第一节第 2 条）：`{Dillard, J. P. and Segrin, C.}`。
   源稿是「名 姓」也要转；多作者用 `and`、**每个**都倒装、**不要混用**两种写法。
   中文译著写 `{原作者（著），译者（译）}`（保留中文逗号/顿号，**不要**改成 `and`）。
3. **每条写全所有已知字段**，顺序照上面的规范（author → title → journal/booktitle/editor
   → publisher → address → edition → volume → number → pages → year → userf）。
   **`doi` 与 `isbn` 一律不写**——它们只用于核实。
   ⚠️ **核实范围见第一节第 2 条第一项：所有字段都要对官方来源核一遍**（含 address /
   edition / 类型），不能只看卷/期/页码。
4. **中文条目必须带 `userf = {cn}`**；纯英文条目**不填**。
5. 页码用 `--`；期号去前导零；月份用三字母缩写（`nov`）。
6. **查不到的字段就省略该字段**，并在该条目上方加一行注释：
   `% ⚠ 待核实：<字段>（原因）`。
   ⚠️ **这行注释必须写在那个 bibtex 代码块内部、紧挨着该条目的上一行**：

   ```bibtex
   % ⚠ 待核实：volume（官网只有在线优先版，期号两说）
   @article{X2020,
     author  = {…},
     title   = {…},
     year    = {2020}
   }
   ```

   写到代码块**外面**（例如写在条目后面的 `}` 之后）工具读不到那条说明：
   回读校验只把「条目上方的注释」当成这条的一部分，于是会把同一个缺失字段报成
   **问题**而不是**提示**——工具看着像在反复挑错，其实是说明放错了地方。
7. **不确定的值不要猜**：宁可保留原值，并用注释写出两种可能，例如
   `% ⚠ pages 有 13--23 / 14--24 两说，建议核原刊`（同样写在**代码块内部、条目上方**）。
8. 若改动了**条目类型**（如 `@book` → `@incollection`），在条目上方（**代码块内部**）加
   `% 类型已改：<原类型> → <新类型>（理由）`。
9. 不要写解释性段落；需要说明的，一律写成 `%` 注释放在对应条目**上方、且在代码块内部**。
10. 输出**只包含那个 bibtex 代码块**，不要额外的前言后语。
11. **每条你核实过的条目，上方都要有一行来源标注**（与第 6 条的「待核实」同一落点：
    代码块**内部**、紧挨条目的上一行）：

    ```bibtex
    % 来源：https://www.worldcat.org/title/… （WorldCat 书目页，2026-10-15 查）
    @book{Fodor1983,
      author    = {Fodor, J. A.},
      title     = {The Modularity of Mind},
      publisher = {MIT Press},
      address   = {Cambridge, MA},
      year      = {1983}
    }
    ```

    · **写你实际打开过的页面 URL**（期刊官网 / 出版社官网 / WorldCat / CNKI…）；
      拿不到 URL 就写来源说明（`% 来源：WorldCat 书目页（未记下 URL）`）——
      **绝不许编一个看起来像 URL 的字符串**。
    · 这一行是**给编辑的审计线索**：没有它，「查过官方来源」与「凭记忆写的」在产物里
      长得一模一样（这也是本工具的纪律：不许让「核实过」这件事看不见）。
    · 只有**真的核过**的条目才写。凭记忆顺手补的条目写 `% 来源：` 会比不写更糟 ——
      那等于给编辑一个假的信任信号。
"""


# ── 「本次真的可以联网检索」时追加给模型的一节（**只在用户开了检索开关时**）──────
#
# 为什么单独成一节、而不是并进 SOURCES_MD：任务书（交给人/AI 的那份 markdown）与
# 「自动预跑」的 system prompt 用的是同一份 SOURCES_MD；而这节的措辞只在**确实带上了
# `tools:[{"type":"web_search"}]`** 时才成立。开关关着时说「你有检索工具」就是撒谎，
# 而撒谎的后果是模型以为查过、编辑以为查过（本项目最忌讳的「静默假装」）。
EVIDENCE_MD = """\
## 二·六、本次**工具已预先检索**（只依据下列证据，勿编造来源）

运行这条通道的工具**已经用公开搜索**为这一条文献查过候选来源，并把它附在逐条请求里
（「【本次自动检索证据】」）。请遵守：

1. **只能依据这些证据判断**（题名/作者/年份是否对得上、来源是否权威）；
   不要把「模型记忆」当成新证据，也不要假装自己打开过证据以外的页面。
2. `% 来源：<URL>` **只能写证据里出现过的 URL**（原样照抄；合并前会校验，
   不在证据里的 URL 视为**编造**，该条会被挡下不并入）。
3. 证据不足以确认的字段**照旧省略**并写 `% ⚠ 待核实：…`；证据说「未查到」时
   **不要**补字段、也不要写 `% 来源：`。
4. 证据只是**线索**，仍须编辑人工核对（见第三节第 11 条「% 来源：」的用法）。
"""

SEARCH_MD = """\
## 二·五、本次**已启用联网检索**（请真的去查，并留下来源）

调用你的这条通道**带上了服务端联网检索工具**。所以：

1. **必须先用检索工具查**上面第二节列出的官方来源，再逐字段比对——**不许**凭记忆作答。
   没有检索结果支撑的字段，按第三节第 6 条**省略**并写 `% ⚠ 待核实：…`。
2. **每条核实过的条目上方写 `% 来源：<你实际打开过的页面 URL>`**（第三节第 11 条）；
   检索到的来源要能在这一行里对得上。
3. 若检索工具**没有返回任何结果**（端点忽略了这个工具、或网络查不到）：**如实照常处理**
   （省略查不到的字段 + 写 `% ⚠ 待核实：…`），**不要**假装检索过，也不要写 `% 来源：`。
4. ⚠️ **已知边界**：内置 `web_search` 是否真检索由端点与模型共同决定（DeepSeek 的
   `deepseek-flash` 会静默忽略该工具、`deepseek-v4-pro` 才真检索）。所以「开了开关」
   **不等于**「真的查到了」——编辑仍须抽查（见本工具文档里的「残余风险」）。
"""


# ── 核实范围与成本闸门（第 128 条，2026-10-18）───────────────────────────────
#
# 期 87 子案例 1 实测：源 `.bib` **377 条**、正文只引用 **31 个**唯一键，但任务书 +
# 检索 + AI 把 **376 条**全核了（检索 418 次请求 ≈ ¥1.845、AI 376 次调用 /
# 2,538,740 prompt tokens；且模型 deepseek-flash 忽略 web_search → 0 条真实检索证据）。
# 要防的不是「核实」本身，而是**为正文根本没用到的条目付钱**。
#
# 所以口径改为：
#   · 默认只核**正文引用到的键**（LaTeX 用 `bibcheck.cited_keys`；`\nocite{*}` = 全量）；
#   · Word 通道以**产物 `.tex`** 的正文为准（引用在 `build_docx` 里替换完才落到 tex；
#     `docxsource.link_citations()` 的 `used` 集合只是局部变量、没有对外暴露，
#     `BuildResult.bib_keys_used` 在 Word 通道又只是「文末参考文献区的全部键」而非引用键，
#     都不能直接复用——见 `docs/GUIDE.md` §3.10）；
#   · 正文一个引用键都解析不出来时**退回全量 + 警告**（不能静默「什么都不核」）；
#   · 本次核实条数默认上限 `DEFAULT_VERIFY_MAX` 条，超了先退出 2 看账；全量必须显式
#     `--verify-all-refs` 承认（`--verify-max 0` = 不限）。
#
# 判定只有 `resolve_verify_scope()` 这一份实现：convert.py 只负责读 tex、打印与闸门。
DEFAULT_VERIFY_MAX = 120

MODE_EMPTY = "empty"        # bib 一条条目都没有（无从核实）
MODE_CITED = "cited"        # 只核正文引用到的键（默认）
MODE_NOCITE = "nocite"      # 正文 `\nocite{*}` → 全量
MODE_ALL_REFS = "all-refs"  # --verify-all-refs 显式全量
MODE_FALLBACK = "fallback"  # 解析不出引用键 → 退回全量（旧行为）+ 警告


@dataclass(frozen=True)
class VerifyScope:
    """「本次核实哪些条目」的判定结果（纯数据；判定见 `resolve_verify_scope`）。"""

    n_bib: int                    # 源 bib 条数
    n_cited: int                  # 正文引用到、且 bib 里存在的条数
    n_cited_missing: int          # 正文引用到、但 bib 里没有的键数
    n_scope: int                  # 本次核实条数
    mode: str
    #: None = **全量**核实；frozenset = 只核这些键（`MODE_EMPTY` 时是空集）
    scope_keys: frozenset[str] | None
    verify_max: int               # 0 = 不限
    explicit_all: bool            # 用户是否显式加了 --verify-all-refs
    over_limit: bool              # 未显式全量、且 n_scope 超上限 → 调用方退出 2
    cited_missing: tuple[str, ...] = ()

    @property
    def skipped(self) -> int:
        """被跳过的「正文未引用」条数。"""
        return self.n_bib - self.n_scope

    def keeps(self, key: str) -> bool:
        """这个 bib 键是否在本次核实范围内。"""
        return self.scope_keys is None or key in self.scope_keys


def cited_keys_from_body(latex: str) -> tuple[set[str], bool]:
    r"""从**产物正文**取正文引用到的 bib 键：返回 (键集合, 是否有 `\nocite{*}`)。

    `\nocite{*}` 是 BibTeX 的「列出全部条目」指令，语义就是**全量引用** → 返回空键集 +
    True，由调用方走全量；`\nocite{a,b}` 与普通 cite 一样只算 a、b（`bibcheck.cited_keys`
    的抽取口径不变，这里只多判一个 `*`）。
    """
    keys = bibcheck.cited_keys(latex)
    if "*" in keys:
        return set(), True
    return keys, False


def resolve_verify_scope(
    bib_keys,
    cited_keys,
    *,
    nocite_all: bool = False,
    explicit_all: bool = False,
    verify_max: int = DEFAULT_VERIFY_MAX,
) -> VerifyScope:
    """判定本次核实范围（纯函数；convert.py 与测试共用这一份）。

    判定顺序（每一步的理由都来自期 87 子案例 1 的实测，见上面的块注释）：
      ① bib 空 → 什么都不核（`MODE_EMPTY`）；
      ② `\nocite{*}` 或 `--verify-all-refs` → 全量（`MODE_NOCITE` / `MODE_ALL_REFS`）；
         其中 `--verify-all-refs` 是**显式承认**，越过上限闸门；`\nocite{*}` 只是稿件
         的语义要求，仍受闸门约束（它最可能出现在「377 条 bib + 全量 nocite」的稿子里，
         正是这条闸门要挡的成本）。
      ③ 正文引用到的键在 bib 里存在 → 只核这些（`MODE_CITED`，默认）；
      ④ 一个都对不上 → **退回全量**（`MODE_FALLBACK`）：宁可多花钱并**大声警告**，
         也不能因为解析不出引用键就静默「一条都不核」（那才是「静默假装」）。
    """
    if verify_max < 0:
        raise ValueError("verify_max 必须 ≥ 0（0 = 不限）")
    bib = {str(k) for k in bib_keys}
    cited = {str(k) for k in cited_keys}
    in_bib = cited & bib
    missing = tuple(sorted(cited - bib))
    n_bib, n_cited = len(bib), len(in_bib)

    if n_bib == 0:
        return VerifyScope(0, n_cited, len(missing), 0, MODE_EMPTY,
                           frozenset(), verify_max, explicit_all, False, missing)

    if nocite_all or explicit_all:
        mode = MODE_ALL_REFS if explicit_all else MODE_NOCITE
        # `\nocite{*}` 的语义就是「正文引用了全部条目」→ n_cited 按全量报。
        n_cited_reported = n_bib if nocite_all and not explicit_all else n_cited
        over = (not explicit_all) and verify_max > 0 and n_bib > verify_max
        return VerifyScope(n_bib, n_cited_reported, len(missing), n_bib, mode, None,
                           verify_max, explicit_all, over, missing)

    if in_bib:
        n_scope = len(in_bib)
        over = verify_max > 0 and n_scope > verify_max
        return VerifyScope(n_bib, n_cited, len(missing), n_scope, MODE_CITED,
                           frozenset(in_bib), verify_max, explicit_all, over, missing)

    # ④ 退回全量（旧行为）+ 警告：调用方必须把 fallback 原因说出来。
    over = verify_max > 0 and n_bib > verify_max
    return VerifyScope(n_bib, 0, len(missing), n_bib, MODE_FALLBACK, None,
                       verify_max, explicit_all, over, missing)


def scope_log_line(scope: VerifyScope) -> str:
    """跑前量化那一行：「源 bib N 条；正文引用 M 条 → 本次核实 M 条（跳过 N-M 条未引用）」。

    正常路径（`MODE_CITED`）的措辞是需求原话；其余模式各自把**为什么不是这个数字**
    写在同一行里——只报一个数字、不解释来源，正是「看不见的跳过」要防的东西。
    """
    skipped = scope.skipped
    if scope.mode == MODE_EMPTY:
        n_cited = scope.n_cited + scope.n_cited_missing
        return (f"源 bib 0 条；正文引用 {n_cited} 条 → 本次核实 0 条"
                "（bib 里没有条目，无从核实）")
    if scope.mode == MODE_CITED:
        return (f"源 bib {scope.n_bib} 条；正文引用 {scope.n_cited} 条 → "
                f"本次核实 {scope.n_scope} 条（跳过 {skipped} 条未引用）")
    if scope.mode == MODE_NOCITE:
        return (f"源 bib {scope.n_bib} 条；正文含 `\\nocite{{*}}`（语义 = 引用全部）→ "
                f"本次核实 {scope.n_scope} 条（跳过 0 条）")
    if scope.mode == MODE_ALL_REFS:
        return (f"源 bib {scope.n_bib} 条；正文引用 {scope.n_cited} 条 → "
                f"本次核实 {scope.n_scope} 条（--verify-all-refs 显式全量；跳过 0 条）")
    # MODE_FALLBACK：把「为什么退回全量」放在同一行，避免编辑以为这是默认的「只核引用」。
    why = ("正文没有引用键" if not scope.cited_missing
           else f"正文引用的 {scope.n_cited_missing} 个键都不在 bib 里")
    return (f"源 bib {scope.n_bib} 条；{why} → 本次**退回全量**核实 {scope.n_scope} 条"
            "（与旧行为相同；见下面警告）")


def limit_guidance(scope: VerifyScope, *, search_enabled: bool = False) -> str:
    """超上限时的退出 2 文案：说清拦了什么、为什么、以及三条可操作的路。

    写进 `convert.die()`（stderr + 退出码 2）。措辞刻意给**三条**互斥的出路，
    而不是替用户选一条：是否全量是编辑部的成本决定，工具只负责先拦下。
    """
    n = scope.n_scope
    max_s = "不限" if scope.verify_max == 0 else str(scope.verify_max)
    why = {
        MODE_NOCITE: f"正文 `\\nocite{{*}}` 要求核实**全部** {n} 条；",
        MODE_FALLBACK: f"正文没能解析出引用键，退回**全量**核实 {n} 条；",
    }.get(scope.mode, "")
    lines = [
        f"本次核实 {n} 条 > 上限 {max_s} 条（--verify-max，默认 {DEFAULT_VERIFY_MAX}；"
        "0 = 不限）：已**拦下**，未生成任务书、未检索、未调用 AI（先看账再花钱）。",
        f"（{why}源 bib {scope.n_bib} 条，正文引用 {scope.n_cited} 条。）",
        "三条路，按你的意图选一条：",
        f"  1) 只核正文引用到的 {scope.n_cited} 条就够 → 调高上限："
        f"--verify-max {n}（或 --verify-max 0 不限）；",
        f"  2) 这次就是要**全量**核 {scope.n_bib} 条 → 显式加 --verify-all-refs"
        "（承认全量、越过闸门）；",
        "  3) 先不花钱看账 → --verify-plan（只打印条数与预计检索/AI 次数后退出 0）。",
    ]
    if search_enabled:
        n_q = 2 * n
        lines.append(f"预计检索：≤ {n_q} 次查询（每条最多 2 次；"
                     f"¥5/1000 次成功 → ≤¥{n_q * 5 / 1000:.2f}）。")
    else:
        lines.append("预计检索：0 次（本次未启用 --search-refs）。")
    lines.append(f"预计 AI：≤ {n} 次调用（每条一次请求；token 费按模型计费）。")
    return "\n".join(lines)


def _entry_block(
    etype: str,
    key: str,
    body: str,
    issues: list[bibcheck.Issue],
    raw: str = "",
    evidence: dict | None = None,
) -> str:
    """一条文献的核实小块：原文 + 离线核对发现的问题。

    `raw` = 这一条的**源稿原文**（`DocxDoc.ref_raw_by_key`，第 68 条）。给了就在解析结果
    下面附一行「源稿原文」，让编辑与 AI 当场看见「我们解析出来的东西」与「源稿写了什么」
    的差别；没给（例如作者自带的 `.bib`）就**一个字都不多写**，与从前逐字节相同。

    `evidence` = 这条文献的自动检索证据包（P1，第 71 条起）。给了就追加
    「🔎 自动检索证据」两三行；`None`（默认）时**一个字都不多写**。
    """
    lines = [f"### `{key}` — `@{etype}`", "", "```",
             f"@{etype}{{{key},\n{body.strip()}}}", "```"]
    if raw:
        lines.append(f"- **源稿原文**（解析可能有遗漏，请以它为准）：{_code_span(_norm_raw_line(raw))}")
    must = [i for i in issues if i.key == key and i.severity == "must"]
    nice = [i for i in issues if i.key == key and i.severity == "nice"]
    if must:
        lines.append(f"- ❗ **必补**：{'、'.join(i.detail for i in must)}")
    if nice:
        lines.append(f"- 建议核对：{'、'.join(i.detail for i in nice)}")
    if not must and not nice:
        lines.append("- 离线核对未发现问题；请核对官方来源是否一致（尤其卷/期/页码/出版地）")
    if evidence:
        lines += _evidence_lines(evidence)
    lines.append("")
    return "\n".join(lines)


#: 检索证据在任务书里每行的截断上限（题名/URL/站点名）。任务书是给人读的，
#: 太长会被 markdown 折行成一片；完整 URL 交给编辑复制即可（测试只断言前缀）。
_EV_VERDICT_CN = {
    "evidence": "检索到来源（**仍须人工核对字段**）",
    "check": "有候选来源，**需人工判断**",
    "missing": "**未查到**（检索证据不足，请人工查证或交给联网 AI）",
}


def _one_line(text: str, limit: int) -> str:
    """压成一行并截断（超出加 `…`，**标注**不静默丢）。"""
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _evidence_lines(pack: dict) -> list[str]:
    """一条文献的「🔎 自动检索证据」2–4 行（P1，第 71 条起；默认不出现）。

    证据只是**线索**：不给模型提示词、不改转换产物；「未查到」≠ 文献不存在。
    """
    verdict = str(pack.get("verdict") or "missing")
    head = f"- 🔎 **自动检索证据**（DeepSeek Search API）：{_EV_VERDICT_CN.get(verdict, verdict)}"
    score = pack.get("score")
    if verdict != "missing" and isinstance(score, (int, float)):
        head += f"（最佳匹配 {float(score):.2f}）"
    lines = [head]
    top = pack.get("top") or []
    for i, row in enumerate(top[:3], 1):
        title = _one_line(row.get("title") or "(无题名)", 60)
        url = _one_line(row.get("url") or "", 160)
        site = _one_line(row.get("site_name") or row.get("category") or "", 24)
        auth = row.get("authority")
        sim = row.get("score")
        bits = [x for x in (
            site,
            f"authority {auth}" if auth is not None else "",
            f"匹配 {float(sim):.2f}" if isinstance(sim, (int, float)) else "",
        ) if x]
        lines.append(f"  - {i}. 《{title}》 — {url}（{'｜'.join(bits)}）" if url
                     else f"  - {i}. 《{title}》（{'｜'.join(bits)}）")
    if verdict == "missing" or not top:
        qs = "、".join(f"`{_one_line(q, 80)}`" for q in (pack.get("queries") or [])[:2])
        err = _one_line(pack.get("error") or "", 80)
        tail = f"用过的查询：{qs}" if qs else ""
        if err:
            tail += ("；" if tail else "") + f"检索错误：{err}"
        if tail:
            lines.append(f"  - {tail}")
    return lines


# 源稿原文在任务书/提示词里最多显示多少字符（超出截断并**标注**，绝不静默丢掉后半截）。
# 300 足够装下最长的一条编号清单条目（全库实测最长的中文条目约 120 字），
# 又能挡住「一整段被当成一条文献」的异常输入把任务书刷爆。
RAW_LINE_MAX = 300


def _norm_raw_line(raw: str) -> str:
    """把源稿原文压成**一行**、去首尾空白，超长截断并标注。

    为什么必须归一（第 68 条）：
      · 原文可能跨行（Word 软换行、编号清单里手工换行），直接塞进 markdown 会破坏
        「一行一个字段」的形状，任务书里那行 `- **源稿原文**：…` 会被截成两行；
      · 原文可能带 `\\u3000`（全角空格）与制表符，`\\s+` 一并压成单个半角空格；
      · 超长必须**标注**「已截断」——静默截断正是这一轮要消灭的那种「看不见的丢失」。
    """
    t = re.sub(r"\s+", " ", str(raw or "")).strip()
    if len(t) > RAW_LINE_MAX:
        t = t[:RAW_LINE_MAX] + "…（已截断）"
    return t


def _code_span(text: str) -> str:
    """把文本包成 markdown 行内代码，且**不被文本里的反引号提前关掉**。

    与 `_fence_for` 同一个思路（那里的注释解释了为什么必须**严格更长**）：分隔符取
    「比文本里最长的反引号串再长一个」。文本以反引号开头/结尾时按 CommonMark 补一个空格，
    否则渲染出来首尾会各多出/少掉一个反引号。
    """
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    d = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{d}{pad}{text}{pad}{d}"


# ⑪ 逐条请求里「源稿原文」的引导行：与任务书逐条清单用的是**同一条口径**
# （原文优先，但原文没有的不许编）。测试逐字断言这一行，改文案要连测试一起改。
RAW_PROMPT_HEADER = "⚠ 源稿原文（解析可能漏字段，**以它为准**；原文没有的仍不许编）："

# ⑪ 逐条请求里「自动检索证据」的引导头（**只在 P1 检索证据存在时**追加）。
# 与任务书里的「🔎 自动检索证据」同源（都来自 `slcn/search.py`），但提示词要**完整 URL**
# （不截断——模型会照抄进 `% 来源：`，截断的 URL 会让白名单校验误判为编造）。
EVIDENCE_PROMPT_HEADER = (
    "【本次自动检索证据】（工具预先检索；`% 来源：` 只能写这里出现过的 URL，不得编造）：")

_EV_VERDICT_PROMPT = {
    "evidence": "检索到候选来源",
    "check": "有候选来源，需人工判断",
    "missing": "未查到",
}


def evidence_prompt_lines(pack: dict) -> list[str]:
    """把证据包摊成提示词里的几行（纯函数，测试逐字断言）。

    `missing` 时明确告诉模型「按查不到处理」——否则它会用记忆把字段补齐，
    那正是「编错的卷号比空缺更糟」要防的。
    """
    verdict = str(pack.get("verdict") or "missing")
    q = (pack.get("queries") or [])
    lines = [f"- 判词：{_EV_VERDICT_PROMPT.get(verdict, verdict)}"
             f"（用过的查询：{'、'.join('`%s`' % x for x in q) if q else '（无）'}）"]
    top = pack.get("top") or []
    if top and verdict != "missing":
        for i, row in enumerate(top[:3], 1):
            bits = [row.get("site_name") or row.get("category") or ""]
            if row.get("authority") is not None:
                bits.append(f"authority {row['authority']}")
            if isinstance(row.get("score"), (int, float)):
                bits.append(f"匹配 {float(row['score']):.2f}")
            lines.append(f"- 候选{i}：《{row.get('title') or '(无题名)'}》 "
                         f"{row.get('url') or '（无 URL）'}（{'｜'.join(b for b in bits if b)}）")
        if len(top) > 3:
            lines.append(f"- （另有 {len(top) - 3} 条候选未列出）")
    else:
        lines.append("- 本次**未检索到**候选来源：请按「查不到」处理"
                     "（省略字段 + `% ⚠ 待核实：…`，不要写 `% 来源：`）。")
        err = (pack.get("error") or "").strip()
        if err:
            lines.append(f"- 检索错误：{err[:160]}")
    return lines


# ⑪ 逐条请求里「本次已联网检索」的引导行（**只在开了检索开关时**追加）。
# 为什么逐条还要再说一遍：system prompt 离得远，模型容易只顾着格式化当前这条；
# 逐条请求是它**最后**看到的东西，把「先检索、写 `% 来源：`」放在这里最有效。
# ⚠️ 开关关着时**一个字都不追加** —— `entry_prompt` 的返回值必须与从前逐字节相同。
SEARCH_PROMPT_LINE = ("⚠ 本次已启用联网检索：请先用检索工具核对官方来源，再给修订后的条目；"
                      "并在条目上方写一行 `% 来源：<你实际打开过的页面 URL>`（"
                      "没有检索结果就照旧省略该字段 + `% ⚠ 待核实：…`，不要假装查过）。")


def entry_prompt(etype: str, key: str, body: str, raw: str = "",
                 search: bool = False, evidence: dict | None = None) -> str:
    """⑪ 「AI 预跑」逐条请求的提示词。

    `raw` 为空时返回值与从前**逐字节相同**（`@type{key,\\n…}`），保证没原文的条目
    （LaTeX 通道、作者自带的 `.bib`）行为一字不变；给了原文就把它一并交给模型——
    第 66 条的教训就是：解析器丢了刊名，模型只能「如实」回「缺期刊信息」，
    看上去像模型不行，其实是我们喂了残缺件。

    `search` 默认 False：只有开了联网检索开关才追加 `SEARCH_PROMPT_LINE`
    （同样是「关着时逐字节相同」这条硬要求的一部分）。

    `evidence` 默认 None：只有 P1 检索证据存在时才追加「【本次自动检索证据】」块
    （P2，第 72 条起）。它是**数据**（候选 URL/权威度/匹配分），不是指令；
    `% 来源：` 的 URL 会在合并前被白名单校验（见 `slcn/aibib.py`）。
    默认 None 时返回值与 P1 之前**逐字节相同**（硬要求）。

    请求形状仍是「一条请求 = 一条条目」：回复解析（`slcn/aibib.py`）只认 bibtex 文本，
    多出来的说明行不影响它，也不需要第二个解析口径。
    """
    one = f"@{etype}{{{key},\n{body.strip()}}}"
    tail = ""
    if evidence is not None:
        tail += "\n" + EVIDENCE_PROMPT_HEADER + "\n" + "\n".join(evidence_prompt_lines(evidence))
    if search:
        tail += "\n" + SEARCH_PROMPT_LINE
    if not raw and not tail:
        return one
    if not raw:
        return one + tail
    base = (f"条目 key：{key}\n"
            f"解析得到的条目：\n{one}\n"
            f"{RAW_PROMPT_HEADER}\n{_norm_raw_line(raw)}")
    return base + tail


def _fence_for(text: str) -> str:
    """选一个**不会被正文提前关掉**的代码围栏。

    围栏必须比原文里最长的一串反引号更长：一样长时原文那行就会被 markdown 当成收尾
    围栏，后面的内容全掉出代码块（AI 从此处起看到的都不是原文，等于白贴）。
    所以取 `max(3, 最长反引号串 + 1)`——「够用就行」在这里不成立，必须严格更长。
    """
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _bibdb_source_text(record: dict, *, limit: int = 2) -> str:
    """库记录的来源摘要：`2025年第6期(85)/01 Xiaoyang_Wang/name.bib`（多份时截断）。

    为什么写来源：任务书这一节的用途是「编辑一眼能核对库版本从哪来」——只写「已核实」
    而不给出处，就等于要求编辑无条件相信一个黑盒。
    """
    places: list[str] = []
    for source in record.get("sources") or []:
        if not isinstance(source, dict):
            continue
        where = "/".join(str(source.get(k)) for k in ("issue", "slug", "file")
                         if source.get(k))
        if where and where not in places:
            places.append(where)
    if not places:
        return ""
    text = f"库内来源：{'、'.join(places[:limit])}"
    if len(places) > limit:
        text += f" 等 {len(places)} 份"
    return text


def _bibdb_hits_section(hits: list[dict]) -> list[str]:
    r"""任务书「五、库内已核实（无需再查）」一节（第 129 条，2026-10-18）。

    只列**安全命中**（DOI 精确 / 题名高置信+作者年份相容+已核实）的条目。这一节存在
    本身就是「本次为什么没花钱」的审计材料：这些键**没有**调检索/AI，`reference.bib`
    也**没改**；库版本与稿件条目的字段差异只作提示，由编辑裁决是否统一。
    """
    lines = [
        "",
        "## 五、库内已核实（无需再查）",
        "",
        f"> 下列 **{len(hits)} 条**已在编辑部引用库中核实（往期已出版期的成品 bib / 人工核实），"
        "本次**未调用检索/AI**，`reference.bib` **未改动**。",
        "> 下方「库版本字段差异」只是提示：库版本与稿件版本不一致时是否统一，由编辑裁决"
        "（本工具不改稿）。",
        "",
    ]
    for hit in hits:
        key = str(hit.get("key") or "")
        record = hit.get("record") or {}
        record_key = str(hit.get("record_key") or record.get("key") or "")
        via = {
            "doi": "DOI 精确命中",
            "title": "题名高置信 + 第一作者姓/年份相容",
        }.get(str(hit.get("hit") or ""), "库内命中")
        head = f"- `{key}`"
        if record_key and record_key != key:
            head += f"（库内键 `{record_key}`）"
        head += f"：{via}"
        source = _bibdb_source_text(record)
        if source:
            head += f"；{source}"
        lines.append(head)
        diff = hit.get("field_diff") or {}
        if diff:
            pieces = []
            for name, values in list(diff.items())[:6]:
                library = str(values.get("library") if isinstance(values, dict) else "")
                manuscript = str(values.get("manuscript")
                                 if isinstance(values, dict) else "")
                pieces.append(f"`{name}`：库 {library or '（空）'}"
                              f" / 稿件 {manuscript or '（空）'}")
            more = f"；…另有 {len(diff) - 6} 个字段差异" if len(diff) > 6 else ""
            lines.append(f"  - ⚠️ 库版本字段差异（待编辑裁决）：{'；'.join(pieces)}{more}")
        else:
            lines.append("  - 字段与库内记录一致（无差异）")
    lines.append("")
    return lines


def _raw_refs_section(raw_ref_lines: list[str]) -> list[str]:
    """第五节：文献区的**原文**（只在一条都没解析出来时追加）。

    ⚠️ 编号是**五**不是四：任务书本来就有「## 四、逐条清单」这一节，若这里也用「四、」
       就会出现两个第四节（2026-10-15 实测踩到）。这一节排在逐条清单**之后**——
       清单此时是空的，正好接着给编辑原文材料。

    ⚠️ 这一节刻意**不做任何解析**：编辑部口径是「编错的卷号比空缺更糟」，而文献核实
       本来就交给编辑各自的 AI（README 第 41 条）。工具只负责把材料原样、清楚地交出去，
       并指明「按第一节规范结构化、遵守第三节全部要求」。
    """
    body = "\n".join(raw_ref_lines)
    fence = _fence_for(body)
    return [
        "## 五、未解析的文献区原文（供结构化）",
        "",
        "**为什么会有这一节**：本刊的文献区解析器只认「`作者. 年. 题名[M]. 出版信息`」"
        "这种人年制写法；这份稿子的文献区对不上，工具**一条都没解析出来**，可原文里确实"
        "有这些行。所以把它们原样贴在这里：请让 AI 按**第一节的 bib 规范**把这些行"
        "结构化成 bib 条目，并遵守**第三节的全部要求**（整份可直接编译的 bibtex、"
        "key 一律不改、中文条目带 `userf = {cn}`……）。",
        "",
        "⚠️ 以下内容**未经解析**，是按 Word 段落原样抄下来的：可能有一行被拆成两条、"
        "或两条被并成一行（Word 的软换行不一定是条目边界），也可能带着行内多余空格。"
        "请按语义判断，不要按行数照抄，也不要把这些行原样当成 bib。",
        "",
        fence,
        body,
        fence,
        "",
    ]


def build(bib_text: str, stem: str, note: str = "",
          raw_ref_lines: list[str] | None = None,
          raw_by_key: dict[str, str] | None = None,
          search_note: str = "",
          evidence: dict[str, dict] | None = None,
          evidence_note: str = "",
          cite_keys: set[str] | None = None,
          bibdb_hits: list[dict] | None = None) -> str:
    """生成整份 markdown 任务书。

    `raw_ref_lines` = 源稿参考文献区的**原文行**（`DocxDoc.ref_raw_lines`）。
    **只在 `bib_text` 一条条目都切不出来时**才用它：追加「第五节 未解析的文献区原文」，
    把原文（未经解析、可能有断行/合并错误）交给编辑的 AI 去按第一节规范结构化。
    `raw_by_key` = **逐条**的源稿原文（`DocxDoc.ref_raw_by_key`，第 68 条）：{最终 bib 键: 原文}。
    命中哪个键，就在「四、逐条清单」的那一条下面附一行「源稿原文」；没命中的条目
    **一个字都不多写**。两个参数都不给时，返回的 markdown 与旧签名调用**逐字节相同**。

    `search_note` = **本次核实通道**的一行/几行说明（markdown 引用行）。给了它就替换掉头部
    那句「工具**不做联网核实**」——因为开了检索时那句话**不成立**（工具确实会去联网查）；
    不给（默认）时头部与从前逐字节相同。为什么不做成布尔开关：通道说明要带上协议与模型名
    （编辑事后要能对账），那是调用方（`convert.py` ⑪）才知道的信息，任务书这边只负责摆出来。

    `evidence` / `evidence_note` = **自动检索证据**（P1，第 71 条起；`--search-refs`）：
    `{bib 键: 证据包}`（由 `slcn/search.py: gather_evidence` 产出），给了就在每条上面追加
    一小段「🔎 自动检索证据」（含来源 URL / 权威度 / 匹配分 / 用过的 query）；
    `evidence_note` 是写进头部的检索统计（次数/命中分布/费用）。两者都不给（默认）时，
    任务书与旧签名调用**逐字节相同**——这是「默认关闭、零行为变化」的硬要求。

    `cite_keys` = **本次只核哪些键**（第 128 条成本闸门；`VerifyScope.scope_keys`）。
    **默认 None = 全量**（旧签名调用逐字节相同）；给了集合就只列这些条目、只统计它们的
    离线问题与作者体例，并在头部加一行「本次核实范围」说明跳过了多少条（数字**不许手抄**，
    由本函数现算）——编辑拿到任务书时必须一眼看出「这不是全量」。

    `bibdb_hits` = **命中编辑部引用库已核实**的条目（第 129 条；`slcn/bibdb.py`
    `match_reference()` 的 safe 结果）。给了就加一节「五、库内已核实（无需再查）」，
    逐个列出 key / 库内来源期·篇·文件 / **库版本字段差异（提示，待编辑裁决）**。
    这些键本次**没调检索/AI、reference.bib 没改**。默认 None = 旧输出逐字节相同。
    """
    raw_by_key = raw_by_key or {}
    rep = bibcheck.check(bib_text)
    entries_all = list(bibcheck.split_entries(bib_text))
    # 英文作者一律倒装是**硬规则**（第一节第 2 条），逐条点给 AI 看——
    # 2026-10-15 实测的教训：规范里写「两种写法都对 / 非必要不修改」时，AI 会**照抄**
    # 源稿的「名 姓」，人工成品却是倒装式，15 处里差 14 处。所以这里必须出声。
    # ⚠️ `bibcheck.check()` 刻意**不含**这条（它被冻结基线的 docx_audit 用着），见其注释。
    form_all = bibcheck.author_form_issues(bib_text)

    # ── 第 128 条：范围过滤（默认 None = 全量，与旧调用逐字节相同）──────────────
    scope_line = ""
    if cite_keys is None:
        entries = entries_all
        issues = rep.issues
        form = form_all
        n_must = rep.n_must
        n_nice = sum(1 for i in rep.issues if i.severity == "nice")
        n_total = rep.total
    else:
        keep = set(cite_keys)
        entries = [e for e in entries_all if e[1] in keep]
        issues = [i for i in rep.issues if i.key in keep]
        form = [i for i in form_all if i.key in keep]
        n_total, n_must = len(entries), sum(1 for i in issues if i.severity == "must")
        n_nice = sum(1 for i in issues if i.severity == "nice")
        scope_line = (
            f"- ❗ **本次核实范围**：只核**正文引用到的 {n_total} 条**"
            f"（源 bib 共 {len(entries_all)} 条；跳过 {len(entries_all) - n_total} 条"
            "**正文未引用**的条目——它们不在下面的清单里，AI 建议也不改动它们；"
            "要全量核实加 `--verify-all-refs`）")

    L: list[str] = [
        f"# 参考文献核实任务书 · {stem}",
        "",
        "> 这份文件是**给编辑（和他们各自的 AI）**用的：把「一、规范」+「二、来源」+「三、格式」"
        "连同下面逐条清单一起交给 AI，或自己按来源查证，再把回答粘回 `reference.bib`。",
    ]
    if search_note:
        L += [ln for ln in search_note.splitlines() if ln.strip()]
    else:
        L.append("> 工具**不做联网核实**，也不预设你用哪家 AI。")
    if evidence_note:
        L += ["", *[ln for ln in evidence_note.splitlines() if ln.strip()]]
    L += [
        "",
        f"- 条目总数：**{n_total}**",
        f"- 离线核对：必补字段 **{n_must}** 项，建议核对 {n_nice} 项",
    ]
    if scope_line:
        L.append(scope_line)
    if form:
        L.append(f"- ❗ 作者体例：**{len(form)}** 条英文 `author`/`editor` **未倒装**，"
                 "必须改写成 `姓, 名`（第一节第 2 条：这是硬规则，不是「修改」）")
    if note:
        L += ["", note]
    L += ["", "---", "", SPEC_MD, SOURCES_MD, "---", "", FORMAT_MD, "---", "", "## 四、逐条清单", ""]

    for etype_raw, key, body in entries:
        etype = bibcheck.TYPE_ALIASES.get(etype_raw.lower(), etype_raw.lower())
        # 第 68 条：这一条的源稿原文（拿不到就是空串，`_entry_block` 不写任何多余内容）。
        L.append(_entry_block(etype, key, body, issues + form,
                              raw=raw_by_key.get(key, ""),
                              evidence=(evidence or {}).get(key)))
    # 第四节**只在**「bib 一条都没有 + 有文献区原文」时追加：这批稿子（全库 14 篇有
    # 文献区却 `n_bib = 0`，其中 9 篇确实有 10–27 行候选条目）交给编辑的 AI 去结构化。
    # 条目解析得出来时这一节不出现——任务书里已经有逐条清单，再贴原文只会打架。
    if bibdb_hits:
        # 第 129 条：安全命中（已核实）的条目单独一节——它们本次不调检索/AI、bib 未改。
        L += _bibdb_hits_section(list(bibdb_hits))
    if not entries and raw_ref_lines:
        L += _raw_refs_section(list(raw_ref_lines))
    return "\n".join(L)
