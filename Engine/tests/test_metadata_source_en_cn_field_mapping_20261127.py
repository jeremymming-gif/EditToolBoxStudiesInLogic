#!/usr/bin/env python3
"""元数据预览中英文字段对调 bug（bug e）的 TDD 套件（2026-11-27）。

根因：`metadata_source._CANON_MACRO["en"]` 整组反了——英文模板的 `mytitle`
实为英文题名（权威见 `build.py:2850-2872`：`is_en = "mytitleCN" in tpl_fields`
时 `mytitle = doc.title_en`、`mytitleCN = doc.title_cn`），预览却把
`mytitle` 读成中文题名、把 `mytitleCN` 读成英文题名，摘要同反。

夹具全部写在 /tmp/slcn-meta-en- 下，不读稿件库。用法（cwd=Engine）：
    PYTHONDONTWRITEBYTECODE=1 SLCN_MANUSCRIPTS=/dev/null PYTHONPATH="$PWD/vendor" \
        /opt/homebrew/bin/python3.14 -B -m unittest tests.test_metadata_source_en_cn_field_mapping_20261127 -v
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[1]
for _p in (str(_ENGINE), os.path.join(str(_ENGINE), "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import metadata_source as ms  # noqa: E402

_EN_TITLE = "Interpolation for Weighted Modal Logics"
_ZH_TITLE = "加权模态逻辑中的插值性质"
_EN_AUTHOR = "Lufeng Rong"
_ZH_AUTHOR = "荣麓枫"
_EN_ABSTRACT = "We prove interpolation for weighted modal logics."
_ZH_ABSTRACT = "我们证明加权模态逻辑的插值性质。"

# 英文模板宏（与 build.py:2850-2872 的权威语义一致：mytitle=英文、mytitleCN=中文）
_EN_TEX = (
    "\\newcommand{\\mytitle}{" + _EN_TITLE + "}\n"
    "\\newcommand{\\mytitleCN}{" + _ZH_TITLE + "}\n"
    "\\newcommand{\\myfirstauthor}{" + _EN_AUTHOR + "}\n"
    "\\newcommand{\\myfirstauthorCN}{" + _ZH_AUTHOR + "}\n"
    "\\newcommand{\\myabstract}{" + _EN_ABSTRACT + "}\n"
    "\\newcommand{\\myabstractCN}{" + _ZH_ABSTRACT + "}\n"
)
# 中文模板宏（CN 模板 mytitle=中文、mytitleEN=英文，作为反例不变式）
_ZH_TEX = (
    "\\newcommand{\\mytitle}{" + _ZH_TITLE + "}\n"
    "\\newcommand{\\mytitleEN}{" + _EN_TITLE + "}\n"
    "\\newcommand{\\myfirstauthor}{" + _ZH_AUTHOR + "}\n"
    "\\newcommand{\\myfirstauthorEN}{" + _EN_AUTHOR + "}\n"
    "\\newcommand{\\myabstract}{" + _ZH_ABSTRACT + "}\n"
    "\\newcommand{\\myabstractEN}{" + _EN_ABSTRACT + "}\n"
)


def _rows_by_field(out: dict) -> dict:
    return {row["field"]: row["value"] for row in out["rows"]}


class EnCnFieldMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="slcn-meta-en-")
        cls.en_tex = os.path.join(cls.tmp, "en.tex")
        cls.zh_tex = os.path.join(cls.tmp, "zh.tex")
        for path, text in ((cls.en_tex, _EN_TEX), (cls.zh_tex, _ZH_TEX)):
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_preview_en_maps_title_author_abstract_to_correct_language(self):
        """EN 预览：mytitle=英文、mytitleCN=中文；摘要/作者同口径（当前全反，RED）。"""
        fields = _rows_by_field(ms.preview(self.en_tex, [], lang="en"))
        self.assertEqual(fields["title_en"], _EN_TITLE)
        self.assertEqual(fields["title_cn"], _ZH_TITLE)
        self.assertEqual(fields["author_en"], _EN_AUTHOR)
        self.assertEqual(fields["author_cn"], _ZH_AUTHOR)
        self.assertEqual(fields["abstract_en"], _EN_ABSTRACT)
        self.assertEqual(fields["abstract_cn"], _ZH_ABSTRACT)

    def test_preview_zh_mapping_unchanged(self):
        """反例：CN 模板 mytitle=中文、mytitleEN=英文，修复 en 不得影响 zh。"""
        fields = _rows_by_field(ms.preview(self.zh_tex, [], lang="zh"))
        self.assertEqual(fields["title_cn"], _ZH_TITLE)
        self.assertEqual(fields["title_en"], _EN_TITLE)
        self.assertEqual(fields["author_cn"], _ZH_AUTHOR)
        self.assertEqual(fields["author_en"], _EN_AUTHOR)
        self.assertEqual(fields["abstract_cn"], _ZH_ABSTRACT)
        self.assertEqual(fields["abstract_en"], _EN_ABSTRACT)

    def test_en_grants_and_thanks_still_map_to_mythanks_only(self):
        """EN 单槽不变式：grants/thanks 都写 mythanks，mygrants 只作兼容别名。"""
        self.assertEqual(ms._CANON_MACRO["en"]["grants"], "mythanks")
        self.assertEqual(ms._CANON_MACRO["en"]["thanks"], "mythanks")
        self.assertEqual(ms._MACRO_CANON["en"].get("mygrants"), "grants")
        self.assertEqual(ms._CANON_MACRO["zh"]["grants"], "mygrants")

    def test_compose_latex_en_writes_title_cn_to_mytitleCN_and_title_en_to_mytitle(self):
        """compose_latex(EN) 写补充源时 title_cn→mytitleCN、title_en→mytitle。"""
        src = os.path.join(self.tmp, "supplement.tex")
        with open(src, "w", encoding="utf-8", newline="\n") as f:
            f.write("\\newcommand{\\mytitleCN}{" + _ZH_TITLE + "}\n"
                    "\\newcommand{\\mytitle}{" + _EN_TITLE + "}\n")
        path, _names, _warns = ms.compose_latex([src], lang="en")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("\\newcommand{\\mytitleCN}{" + _ZH_TITLE + "}", text)
        self.assertIn("\\newcommand{\\mytitle}{" + _EN_TITLE + "}", text)


if __name__ == "__main__":
    unittest.main()
