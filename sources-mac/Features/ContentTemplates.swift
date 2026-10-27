// ⚠️ 本文件由 tools/gen_templates.py 自动生成，请勿手工修改。
// 内容是 Windows 原版程序内置的 LaTeX 模板，逐字节一致。

enum ContentTemplates {

    /// chContentHead —— 692 个字符
    static let chContentHead = "\\documentclass[b5paper,11pt,onecolumn,twoside,fontset=windows]{ctexart}\n\\pagestyle{empty}\n\\usepackage[total={130mm,203mm}, top=25.3mm, headheight=20pt, headsep=4truemm]{geometry}%调整版式\n\\usepackage{tabularx}\n\\usepackage{amssymb}\n\\usepackage{xeCJK}\n\\setmainfont{Times New Roman}\n\n\\newcommand{\\vdistance}{\\\\[4.8ex]}%调整行间隔\n\\newcommand{\\cndash}{\\raise0.1em\\hbox{--}}\n\n\\begin{document}\n\n\\begin{flushright}\n\\renewcommand{\\baselinestretch}{1.3}\n\\zihao{5}\n\\heiti{逻辑学研究（双月刊）\\\\\n第\\ 19 卷，第\\ 3 期，总第\\ 88 期}\\\\\n\\heiti{2026 年第\\ 3 期，6月\\ 18 日出版}\n\\end{flushright}\n\\vspace{4ex}{\\songti{\\huge{\\bfseries  目 \\ \\ \\ \\ \\ \\ \\ \\ 录}}}\n\n\\noindent\\hrulefill \\vspace{2.5ex}\\\\\n\\begin{tabularx}{\\textwidth}\n{l@{\\hspace{-0.3em}}r@{\\hspace{-4.2em}}r}\t\n{\\large \\bf{专栏：XXX}}\n&&\\textit{\\large 主持人：XXX}\n\\\\[3.0 ex]\n"

    /// chContentEnd —— 31 个字符
    static let chContentEnd = "\n\\end{tabularx}\n\\end{document}\n"

    /// enContentHead —— 713 个字符
    static let enContentHead = "\\documentclass[b5paper,10pt,onecolumn,twoside,fontset=windows]{ctexart}\n\\pagestyle{empty}%不显示页码\n\\usepackage[total={130mm,203mm}, top=20.3mm, headheight=20pt, headsep=4truemm]{geometry}%调整版式\n\\usepackage{tabularx}\n\\usepackage{amssymb}\n\\setmainfont{Times New Roman}\n\n% \\renewcommand{\\large}{\\fontsize{11}{14}\\selectfont}%调整字体大小\n\\newcommand{\\vdistance}{\\\\[4mm]}%调整行间隔\n\n\\begin{document}\n\\begin{flushright}\n\\zihao{-4}\n\\textbf{Studies in Logic\\phantom{|}\\\\\nVolume 19, Number 3, June 2026\\phantom{|}}\n\\end{flushright}\n\\vspace{0.5ex} {\\rmfamily{\\bfseries TABLE OF CONTENTS}} \\vspace{-0.5ex}\n\n\\noindent\\hrulefill{\\rmfamily} \\vspace{2ex}\\\\\n\\begin{tabularx}{\\textwidth}{l@{\\hspace{4em}}r}\n\\large  \\bf{Column: XXX} \n\\\\[6.2mm]\n"

    /// 原程序编译时把中英文目录的结尾视为同一个字符串（内容相同被去重）
    static let enContentEnd = chContentEnd
}
