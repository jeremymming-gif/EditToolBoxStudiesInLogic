r"""内置目录模板 —— **本文件由 `tools/gen_content_templates.py` 生成，不要手改**。

源头：`sources-mac/Features/ContentTemplates.swift`（mac 界面的内置模板常量，
其上游是原版 `charti_build_content.py` 里写死的 2022 年模板）。

「生成目录」只在**找不到可继承的成品**时才用它（正常路径是继承上一期的
`000 Table of Contents/Chinese Content.tex` 或 `contents.tex`）。即便如此，
它也必须与 Swift 那份逐字节一致 —— 否则两个平台在兜底情形下会产出不同的目录。

要改模板：改 Swift 那份，然后跑
`python3 tools/gen_content_templates.py`。
"""

chContentHead = r"""\documentclass[b5paper,11pt,onecolumn,twoside,fontset=windows]{ctexart}
\pagestyle{empty}
\usepackage[total={130mm,203mm}, top=25.3mm, headheight=20pt, headsep=4truemm]{geometry}%调整版式
\usepackage{tabularx}
\usepackage{amssymb}
\usepackage{xeCJK}
\setmainfont{Times New Roman}

\newcommand{\vdistance}{\\[4.8ex]}%调整行间隔
\newcommand{\cndash}{\raise0.1em\hbox{--}}

\begin{document}

\begin{flushright}
\renewcommand{\baselinestretch}{1.3}
\zihao{5}
\heiti{逻辑学研究（双月刊）\\
第\ 19 卷，第\ 3 期，总第\ 88 期}\\
\heiti{2026 年第\ 3 期，6月\ 18 日出版}
\end{flushright}
\vspace{4ex}{\songti{\huge{\bfseries  目 \ \ \ \ \ \ \ \ 录}}}

\noindent\hrulefill \vspace{2.5ex}\\
\begin{tabularx}{\textwidth}
{l@{\hspace{-0.3em}}r@{\hspace{-4.2em}}r}	
{\large \bf{专栏：XXX}}
&&\textit{\large 主持人：XXX}
\\[3.0 ex]
"""

chContentEnd = r"""
\end{tabularx}
\end{document}
"""

enContentHead = r"""\documentclass[b5paper,10pt,onecolumn,twoside,fontset=windows]{ctexart}
\pagestyle{empty}%不显示页码
\usepackage[total={130mm,203mm}, top=20.3mm, headheight=20pt, headsep=4truemm]{geometry}%调整版式
\usepackage{tabularx}
\usepackage{amssymb}
\setmainfont{Times New Roman}

% \renewcommand{\large}{\fontsize{11}{14}\selectfont}%调整字体大小
\newcommand{\vdistance}{\\[4mm]}%调整行间隔

\begin{document}
\begin{flushright}
\zihao{-4}
\textbf{Studies in Logic\phantom{|}\\
Volume 19, Number 3, June 2026\phantom{|}}
\end{flushright}
\vspace{0.5ex} {\rmfamily{\bfseries TABLE OF CONTENTS}} \vspace{-0.5ex}

\noindent\hrulefill{\rmfamily} \vspace{2ex}\\
\begin{tabularx}{\textwidth}{l@{\hspace{4em}}r}
\large  \bf{Column: XXX} 
\\[6.2mm]
"""

enContentEnd = r"""
\end{tabularx}
\end{document}
"""
