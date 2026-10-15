// ⚠️ 本文件由 tools/gen_templates.py 自动生成，请勿手工修改。
// 内容是 Windows 原版程序内置的 LaTeX 模板，逐字节一致。

enum ContentTemplates {

    /// chContentHead —— 675 个字符
    static let chContentHead = "\n\\documentclass[b5paper,11pt,onecolumn,twoside]{ctexart}\n\\pagestyle{empty}\n\\usepackage[total={130mm,203mm}, top=28.3mm, headheight=20pt, headsep=4truemm]{geometry}%调整版式\n\\usepackage{tabularx}\n\\setmainfont{Times New Roman}\n\n\\newcommand{\\vdistance}{\\\\[4.8ex]}%调整行间隔\n\n\\begin{document}\n\n\\begin{flushright}\n\\renewcommand{\\baselinestretch}{1.3}\n\\zihao{5}\n\\heiti{逻辑学研究（双月刊）\\\\\n第\\ 15 卷，第\\ 6 期，总第\\ 67 期}\\\\\n\\heiti{2022 年第\\ 6 期，12月\\ 18 日出版}\n\\end{flushright}\n\\vspace{7.8ex}{\\songti{\\huge{\\bfseries  目 \\ \\ \\ \\ \\ \\ \\ \\ 录}}}\n\n\\noindent\\hrulefill \\vspace{2.5ex}\\\\\n\\begin{tabularx}{\\textwidth}\n{l@{\\hspace{4.5em}}r@{\\hspace{-6.1em}}r}\t\n{\\large \\bf{专栏：XXX}}\n&&\\textit{\\large 主持人：XXX}\n\\\\[5.0 ex]\n"

    /// chContentEnd —— 31 个字符
    static let chContentEnd = "\n\\end{tabularx}\n\\end{document}\n"

    /// enContentHead —— 565 个字符
    static let enContentHead = "\n\\documentclass[b5paper,10pt,onecolumn,twoside]{ctexart}\n\\pagestyle{empty}%不显示页码\n\\usepackage[total={130mm,203mm}, top=20.3mm, headheight=20pt, headsep=4truemm]{geometry}%调整版式\n\\usepackage{tabularx}\n\\setmainfont{Times New Roman}\n\n\n\\newcommand{\\vdistance}{\\\\[4mm]}%调整行间隔\n\n\\begin{document}\n\\begin{flushright}\n\\zihao{-4}\n\\textbf{Studies in Logic\\\\\nVolume 16, Number 2, April 2023}\n\\end{flushright}\n\\vspace{0.5ex} {\\rmfamily{\\bfseries TABLE OF CONTENTS}} \\vspace{-0.5ex}\n\n\\noindent\\hrulefill{\\rmfamily} \\vspace{1.3ex}\\\\\n\\begin{tabularx}{\\textwidth}{l@{\\hspace{-1.2em}}r}\n"

    /// 原程序编译时把中英文目录的结尾视为同一个字符串（内容相同被去重）
    static let enContentEnd = chContentEnd
}
