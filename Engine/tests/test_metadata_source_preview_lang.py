#!/usr/bin/env python3
"""元数据预览语种显示 bug（bug c）的 TDD 套件。

`metadata_source.preview()` 在未指定语种时曾写死 `lang or "zh"`，导致 GUI
元数据预览面板显示「语种 zh」，而实际转换（convert.py 的 `detect_lang`）会把
英文稿判为 en。本套件钉住：预览的 "lang" 字段在未指定语种时必须与
`detect_lang(source)` 一致，显式 lang 仍优先；且 preview 保持只读。

夹具全部写在 /tmp/slcn-preview-lang-* 下，不读稿件库。
用法（cwd=Engine）：
    PYTHONDONTWRITEBYTECODE=1 SLCN_MANUSCRIPTS=/dev/null PYTHONPATH="$PWD/vendor" \
        /opt/homebrew/bin/python3.14 -B -m unittest tests.test_metadata_source_preview_lang -v
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[1]
for _p in (str(_ENGINE), os.path.join(str(_ENGINE), "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import metadata_source  # noqa: E402

_EN_TEX = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "The logic of conditional proof and its applications in philosophy.\n"
    "We study entailment, consequence relations, and consequence operators.\n"
    "This manuscript contains no Chinese characters in its body text.\n"
    "A formal treatment of the deduction theorem is given in section two.\n"
    "The results are compared with several well known semantic frameworks.\n"
    "All assertions are supported by rigorous mathematical argumentation.\n"
    "\\end{document}\n"
)

_ZH_TEX = (
    "\\documentclass{article}\n"
    "\\begin{document}\n"
    "本文研究条件证明的逻辑结构及其在哲学推理中的应用与价值。\n"
    "我们考察蕴涵关系、推论关系以及相应的形式化语义刻画与性质。\n"
    "全文用大量篇幅讨论演绎定理的形式证明与相关技术问题。\n"
    "这些结论与若干已有的语义学框架进行了细致的比较分析。\n"
    "所有论断都给出了严格的数学论证与形式化说明。\n"
    "\\end{document}\n"
)


class PreviewLangTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # detect_lang()（已提交的 convert_metadata.py，不在本代理写域内）用裸 open()
        # 读 .tex，Python 3.14 的 unittest 默认会报 ResourceWarning；那是既有实现细节，
        # 与本套件要钉的预览语种行为无关，仅在本套件内静音以保持证据输出干净。
        warnings.filterwarnings("ignore", category=ResourceWarning,
                                message="unclosed file")
        cls.tmp = tempfile.mkdtemp(prefix="slcn-preview-lang-")
        cls.en = os.path.join(cls.tmp, "english.tex")
        cls.zh = os.path.join(cls.tmp, "chinese.tex")
        for path, text in ((cls.en, _EN_TEX), (cls.zh, _ZH_TEX)):
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_lang_none_english_source_previews_en(self):
        """未指定语种 + 英文稿 → 预览 lang 应为 en（当前写死 zh，RED）。"""
        out = metadata_source.preview(self.en, [])
        self.assertEqual(out["lang"], "en")

    def test_lang_none_chinese_source_previews_zh(self):
        """未指定语种 + 中文稿 → 预览 lang 应为 zh。"""
        out = metadata_source.preview(self.zh, [])
        self.assertEqual(out["lang"], "zh")

    def test_explicit_lang_wins_over_detection(self):
        """显式 lang='en' 优先，不被 detect_lang 覆盖（即便稿件明显是中文）。"""
        out = metadata_source.preview(self.zh, [], lang="en")
        self.assertEqual(out["lang"], "en")

    def test_preview_is_read_only(self):
        """preview() 不产生任何产物文件/目录（只对传入的临时目录断言）。"""
        before = sorted(os.listdir(self.tmp))
        metadata_source.preview(self.zh, [])
        self.assertEqual(sorted(os.listdir(self.tmp)), before)


if __name__ == "__main__":
    unittest.main()
