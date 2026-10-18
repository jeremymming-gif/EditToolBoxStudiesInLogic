# vendor_omml 来源与校验和（task-50，第四十四批②）

| 文件 | 上游 | 版本/提交 | 许可 | sha256 |
|---|---|---|---|---|
| `omml2mml.xsl` | github.com/scienceai/omml2mathml | master（npm 1.3.0）；Microsoft Office Beta 070708 派生 | Apache-2.0 | `d85242010d10fca4e6f84be06c2268171597520a45db42a8308f416587b3ce99` |
| `mathml_to_latex/converter.py` | github.com/asnunes/py-mathml-to-latex | 187138aac0e5c6c180906868ecb70396909516c8（2024-11-09） | MIT | `ac8107ced97b07356f87895e29c14eaf880149e82f3ba5cc437d03cc50340c43` |
| `testdata/omml_149.json` | 本项目从 3 篇真实稿抽取（87/06 135 + 90/07 7 + 90/10 7） | — | 本项目（不含稿件正文：仅 OMML XML 片段，供结构回归） | 见 `sha256sum testdata/omml_149.json` |

**更新策略**：上游均已停止维护（npm 1.3.0 老包；py 2024-11 最后提交）。**不追随上游更新**；
行为由 `Engine/tests/test_omml_stack.py` 的 149 fixture 锁住。升级须同时更新本表校验和与 fixture 断言。

**加载方式**：`Engine/slcn/docxxml.py` 的 `_load_omml_stack()` 把本目录加入 `sys.path`
（仅在使用 OMML 栈时惰性加载）；`lxml.etree.XSLT` 用仓库已有的 lxml 6.1.3（XSLT 1.0）。
