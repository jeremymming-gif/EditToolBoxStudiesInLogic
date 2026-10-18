# `Engine/vendor_omml/` —— 纯 Python OMML→MathML→LaTeX 栈（vendored）

第四十四批②（task-50）：组件评估首选方案的 vendored 依赖，**默认关闭**
（`docxxml.USE_OMML_STACK = False` / `omml_to_latex(..., use_omml_stack=True)` 才启用；
现有 `omml_to_latex` 旧实现保留为 fallback）。

```
omml2mml.xsl                       OMML → MathML（XSLT 1.0，Apache-2.0）
mathml_to_latex/                   MathML → LaTeX（MIT，Python 纯标准库）
testdata/omml_149.json             149 条真实稿公式 fixture（结构回归）
LICENSE-omml2mathml-Apache-2.0.txt Apache-2.0 全文
LICENSE-py-mathml-to-latex-MIT.md  MIT 全文
NOTICE                             归属声明
SOURCES.md                         上游/提交/校验和/更新策略
```

- 总大小约 **0.55 MB**（不含 `__pycache__`；xsl 88KB + python 包 436KB + fixture 446KB）。
- 打包：`build.sh` / Windows 打包白名单**需要把 `Engine/vendor_omml/` 一起带上**（否则 App 内开关不可用）。
- 许可：Apache-2.0（XSL）+ MIT（转换器）→ 与项目 MIT 兼容；`THIRD_PARTY.md` 已署名。
