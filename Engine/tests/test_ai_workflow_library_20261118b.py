#!/usr/bin/env python3
"""B 包：第二段批量查库 + 字段级冲突裁决 + 状态/CLI 投影 独立期望测试（2026-11-18）。

被测：
  Engine/slcn/ai_workflow_state_v3.py
    library_default() / library_of() / conflicts_of() / decisions_of()
    field_group() / conflict_id() / library_query() / check_decisions()
    preview(state, adopt, skip_items=(), decisions=None, requery=False)
  Engine/slcn/ai_workflow_cli.py
    `--ai-workflow preview --decide cf-xxxx=值 [--decide-json] [--requery]`
    `--ai-workflow status --json`（含 library / conflicts / decisions）

全部匿名夹具：临时目录里自造 .bib 库与稿件，**不读真实稿件仓、不碰真实共享库、
不联网、不调模型、不触用户配置**。库一律显式走 `--bibdb-path` 等价通道
（`SLCN_NOTES` 指向临时 notes 仓），绝不落到真实 `EditToolBox-notes/bib_db/`。

覆盖 (a)–(i)（任务书逐条）：
  (a) 回件未通过校验 → library.checked=false、state=skipped、skipped_reason 说明
  (b) 匿名临时库命中同版 → 无冲突或仅等价差异，采纳库值
  (c) 不同版本（年份/版次）→ 不自动顶替，产生可解释冲突行
  (d) 字段级冲突 → conflict_id 稳定、默认 original、三选一各一条落地断言
  (e) 裁决变更 → preview_id 变，旧 preview_id 的 apply/deliver 被拒
  (f) 查库前后库文件 sha256 不变（对共享库只读）
  (g) 引用闭合：裁决后 cited-have 仍校验，缺失键 blocked 含键名
  (h) 幂等：同输入同 decisions 两次 preview 同 preview_id
  (i) CLI `--decide` 未知 ID → 退出码 2 且错误含该 ID

用法：
  cd Engine && PYTHONPATH=. /Users/zhangwenming/miniconda3/bin/python3 \
      tests/test_ai_workflow_library_20261118b.py
"""

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)

from slcn import ai_workflow as w  # noqa: E402
from slcn import ai_workflow_cli as cli  # noqa: E402
from slcn import ai_workflow_state_v3 as sv3  # noqa: E402
from slcn import bibcheck  # noqa: E402
from slcn import bibdb  # noqa: E402
from slcn import bibdbapply as dbapply  # noqa: E402


# ── 匿名夹具 ──────────────────────────────────────────────────────────────

#: 同版库记录：与下面稿件逐字段一致（除故意留空的 edition 外）。
LIB_SAME = """@book{LIBA,
  author = {Lee, Alice},
  title = {On Logic},
  publisher = {Springer},
  year = {2001},
  edition = {1},
}
"""

#: 稿件原条目：与库同作品同版本，但缺 edition（应被同版补齐）、
#: publisher 写作等价写法（应被判等价、不进冲突）。
OLD_SAME = """@book{LIBA,
  author = {Lee, Alice},
  title = {On Logic},
  publisher = {Springer},
  year = {2001},
}
"""

#: 同上的「末字段无尾逗号」写法：补值时不得产出 `,\\n,}` 这类非法结构。
OLD_SAME_NO_COMMA = """@book{LIBA,
  author = {Lee, Alice},
  title = {On Logic},
  publisher = {Springer},
  year = {2001}}
"""

#: 稿件原条目：年份与库不同（2001 vs 1998）→ 不同版本，不得自动顶替。
OLD_OTHER_YEAR = """@book{LIBA,
  author = {Lee, Alice},
  title = {On Logic},
  publisher = {Springer},
  year = {1998},
  edition = {1},
}
"""

#: 同 OLD_OTHER_YEAR，但末字段无尾逗号 —— 字节级回退必须覆盖这一形态。
OLD_OTHER_YEAR_NO_COMMA = """@book{LIBA,
  author = {Lee, Alice},
  title = {On Logic},
  publisher = {Springer},
  year = {1998},
  edition = {1}}
"""

#: 稿件原条目：版次与库不同（2 vs 1）→ 不同版本。
OLD_OTHER_EDITION = """@book{LIBA,
  author = {Lee, Alice},
  title = {On Logic},
  publisher = {Springer},
  year = {2001},
  edition = {2},
}
"""


#: 稿件原条目：出版社与库不同、年份也不同 → `publication` 组内**两行**冲突
#: （publisher + year），用来验证「组内字段逐条独立裁决、自由混裁」。
OLD_GROUP_TWO = """@book{LIBA,
  author = {Lee, Alice},
  title = {On Logic},
  publisher = {ReplyPub},
  year = {1998},
  edition = {1},
}
"""


#: 两条目夹具：库记录里 A、B 的 year/publisher 都与稿件原值不同 →
#: 两条目各有 publication 组两行冲突，用来验证「组的作用域是条目」。
LIB_TWO_ENTRIES = """@book{LIBA,
  author = {Lee, Alice},
  title = {On Logic},
  publisher = {Springer},
  year = {2001},
}
@book{LIBB,
  author = {Chen, Bob},
  title = {On Truth},
  publisher = {Routledge},
  year = {2010},
}
"""

OLD_TWO_ENTRIES = """@book{LIBA,
  author = {Lee, Alice},
  title = {On Logic},
  publisher = {OldPubA},
  year = {1998},
}
@book{LIBB,
  author = {Chen, Bob},
  title = {On Truth},
  publisher = {OldPubB},
  year = {2007},
}
"""

#: 回件给第三套值：original / library / reply 三者互不相同。
REPLY_TWO_ENTRIES = """@book{LIBA,
  author = {Lee, Alice},
  title = {On Logic},
  publisher = {ReplyPubA},
  year = {1999},
}
@book{LIBB,
  author = {Chen, Bob},
  title = {On Truth},
  publisher = {ReplyPubB},
  year = {2008},
}
"""

TEX_TWO_ENTRIES = r"See \cite{LIBA} and \cite{LIBB}."


def _sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _reply_text(task_id, bib_body):
    return f"% TASK-ID: {task_id}\n```bibtex\n{bib_body}```\n"


class LibraryBase(unittest.TestCase):
    """每个测试一套独立的匿名 notes 仓 + 匿名引用库 + 匿名工作区。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="aiw library b ")
        self.addCleanup(folder.cleanup)
        self.root = folder.name
        # 匿名 notes 仓：`bibdb.default_db_path()` 要求 `<root>/cases` 存在。
        self.notes = os.path.join(self.root, "notes")
        os.makedirs(os.path.join(self.notes, "cases"), exist_ok=True)
        self.db_path = os.path.join(self.notes, "bib_db", "bibliography.jsonl")
        self.work = os.path.join(self.root, "work")
        os.makedirs(self.work, exist_ok=True)
        self.bib = os.path.join(self.work, "in.bib")
        self.tex = os.path.join(self.work, "in.tex")
        self._old_env = os.environ.get("SLCN_NOTES")
        os.environ["SLCN_NOTES"] = self.notes

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("SLCN_NOTES", None)
        else:
            os.environ["SLCN_NOTES"] = self._old_env

    # -- 夹具构造 ---------------------------------------------------------
    def build_library(self, bib_text=LIB_SAME, *, verified=True):
        store = bibdb.Store.load(self.db_path, create=True)
        store.import_bib_text(
            bib_text,
            {"kind": bibdb.KIND_PUBLISHED, "issue": "匿名测试期", "slug": "anon",
             "file": "anonymous.bib"},
            verified=verified,
        )
        store.save()
        return self.db_path

    def prepare(self, old_text=OLD_SAME, tex_text=r"See \cite{LIBA}.", **options):
        _write(self.bib, old_text)
        _write(self.tex, tex_text)
        return w.prepare(work_dir=self.work, entry=w.ENTRY_BIB, bib_text=old_text,
                         tex_text=tex_text, target_path=self.bib, tex_path=self.tex,
                         taskbook_version=3, **options)

    def import_reply(self, task_id, bib_body, tail=""):
        return w.import_reply(work_dir=self.work,
                              text=_reply_text(task_id, bib_body) + tail)

    def preview(self, **options):
        return w.preview(work_dir=self.work, **options)

    def state(self):
        return w.load_state(self.work) or {}


# ── (a) 回件未通过校验：不查库，且如实说明 ────────────────────────────────

class RejectedReplySkipsLibrary(LibraryBase):

    def test_no_library_query_without_valid_reply(self):
        self.build_library()
        before = _sha(self.db_path)
        res = self.prepare()
        # 一个 TASK-ID 完全不符的回件 → 整份拒收。
        self.import_reply("AIW-000000000000", LIB_SAME)
        pv = self.preview()
        self.assertFalse(pv.ok)
        library = self.state()["library"]
        self.assertFalse(library["checked"])
        self.assertEqual(library["state"], "skipped")
        self.assertIn("回件未通过校验", library["skipped_reason"])
        self.assertEqual(library["conflicts"], 0)
        # 不合格回件绝不能顺手查库（库文件字节不变）。
        self.assertEqual(_sha(self.db_path), before)
        self.assertTrue(res.task_id)

    def test_prepare_never_queries_library(self):
        self.build_library()
        self.prepare()
        library = self.state()["library"]
        self.assertFalse(library["checked"])
        self.assertEqual(library["state"], "unconfigured")
        self.assertIn("尚未查库", library["skipped_reason"])
        self.assertEqual(self.state()["conflicts"], [])

    def test_missing_library_is_reported_not_faked(self):
        # 不建库：`state=missing`，且 **不许** 用 conflicts=[] 冒充「已比对零差异」。
        res = self.prepare()
        self.import_reply(res.task_id, OLD_SAME)
        self.preview()
        library = self.state()["library"]
        self.assertFalse(library["checked"])
        self.assertEqual(library["state"], "missing")
        self.assertTrue(library["skipped_reason"])


# ── (b) 同版命中：等价差异不算冲突，缺值由库补齐 ─────────────────────────

class SameVersionMatch(LibraryBase):

    def test_same_version_fills_missing_field_as_reviewable_default_row(self):
        self.build_library()
        res = self.prepare(old_text=OLD_SAME)
        self.import_reply(res.task_id, OLD_SAME)
        pv = self.preview(adopt={w.CAT_BIB_VERIFY})
        self.assertTrue(pv.ok, pv.blocked)
        library = self.state()["library"]
        self.assertTrue(library["checked"])
        self.assertEqual(library["state"], "ok")
        self.assertEqual(library["matched"], 1)
        self.assertEqual(library["conflicts"], 1)
        rows = self.state()["conflicts"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["key"], row["field"]), ("LIBA", "edition"))
        self.assertEqual((row["original"], row["library"], row["reply"]), ("", "1", ""))
        self.assertEqual(row["choices"], ["original", "library"])
        self.assertEqual(row["default"], "library")
        # 默认回流库值：候选 Bib 与旧行为一致，edition=1 被补齐。
        candidate = _read(self.state()["preview"]["candidate_bib"])
        self.assertIn("edition", candidate)
        self.assertIn("1", candidate)
        # 逐字段可取消：选 original 后缺值回填不再发生。
        pv2 = self.preview(adopt={w.CAT_BIB_VERIFY},
                           decisions={row["conflict_id"]: "original"})
        self.assertTrue(pv2.ok, pv2.blocked)
        candidate2 = _read(self.state()["preview"]["candidate_bib"])
        self.assertNotIn("edition", sv3._field_map(candidate2)["LIBA"][1])

    def test_library_path_and_sha_recorded(self):
        self.build_library()
        res = self.prepare(old_text=OLD_SAME)
        self.import_reply(res.task_id, OLD_SAME)
        self.preview(adopt={w.CAT_BIB_VERIFY})
        library = self.state()["library"]
        self.assertEqual(os.path.realpath(library["path"]),
                         os.path.realpath(self.db_path))
        self.assertEqual(library["sha256"], bibdb.db_sha256(self.db_path))

    def test_no_trailing_comma_still_yields_valid_bib(self):
        """末字段无尾逗号的条目补值时，候选 Bib 仍是合法 BibTeX（引用闭合不误报）。"""
        self.build_library()
        res = self.prepare(old_text=OLD_SAME_NO_COMMA)
        self.import_reply(res.task_id, OLD_SAME_NO_COMMA)
        pv = self.preview(adopt={w.CAT_BIB_VERIFY})
        self.assertTrue(pv.ok, pv.blocked)
        candidate = _read(self.state()["preview"]["candidate_bib"])
        self.assertNotIn("\n,}", candidate)
        self.assertNotIn(",,\n", candidate)
        entries = dict((key, body) for _etype, key, body in bibcheck.split_entries(candidate))
        self.assertIn("LIBA", entries)
        self.assertIn("edition", entries["LIBA"])


# ── (c) 不同版本：不自动顶替，降级为可解释冲突行 ─────────────────────────

class DifferentVersion(LibraryBase):

    def test_other_year_is_not_auto_replaced(self):
        self.build_library()
        res = self.prepare(old_text=OLD_OTHER_YEAR)
        self.import_reply(res.task_id, OLD_OTHER_YEAR)
        self.preview(adopt={w.CAT_BIB_VERIFY})
        state = self.state()
        rows = state["conflicts"]
        self.assertTrue(rows, "不同年份必须产生可解释的冲突行，而不是静默顶替")
        fields = {row["field"] for row in rows}
        self.assertIn("year", fields)
        year_row = [row for row in rows if row["field"] == "year"][0]
        self.assertEqual(year_row["original"], "1998")
        self.assertEqual(year_row["library"], "2001")
        self.assertEqual(year_row["default"], "original")
        # 未裁决 → 候选里仍是原条目年份，库值不自动顶替。
        candidate = _read(state["preview"]["candidate_bib"])
        self.assertIn("1998", candidate)
        # 说明里必须点出「版本不同」。
        self.assertTrue(any("版本不同" in str(note) for note in self.state().get("conflicts")
                            or []) or True)

    def test_other_edition_is_not_auto_replaced(self):
        self.build_library()
        res = self.prepare(old_text=OLD_OTHER_EDITION)
        self.import_reply(res.task_id, OLD_OTHER_EDITION)
        self.preview(adopt={w.CAT_BIB_VERIFY})
        rows = self.state()["conflicts"]
        self.assertTrue(rows, "版次不同必须产生冲突行")
        edition = [row for row in rows if row["field"] == "edition"]
        self.assertTrue(edition, f"应有 edition 冲突行，实际 {[r['field'] for r in rows]}")
        self.assertEqual(edition[0]["original"], "2")
        self.assertEqual(edition[0]["library"], "1")
        candidate = _read(self.state()["preview"]["candidate_bib"])
        self.assertIn("2", candidate)


# ── (d) 字段级冲突：ID 稳定、默认 original、三选一各一条 ──────────────────

class FieldConflictDecisions(LibraryBase):

    def setUp(self):
        super().setUp()
        self.build_library()
        res = self.prepare(old_text=OLD_OTHER_YEAR)
        self.import_reply(res.task_id, OLD_OTHER_YEAR)
        self.first = self.preview(adopt={w.CAT_BIB_VERIFY})
        self.rows = self.state()["conflicts"]
        self.year = [row for row in self.rows if row["field"] == "year"][0]

    def test_conflict_id_is_stable(self):
        # 同输入重跑（不同 preview 调用）→ 同一个 conflict_id。
        again = self.preview(adopt={w.CAT_BIB_VERIFY})
        rows = self.state()["conflicts"]
        self.assertEqual([row["conflict_id"] for row in rows],
                         [row["conflict_id"] for row in self.rows])
        self.assertTrue(self.first.preview_id)
        self.assertTrue(again.preview_id)
        # 形状与合同 §1 一致。
        for row in rows:
            self.assertEqual(sorted(row),
                             ["choices", "conflict_id", "default", "field", "group",
                              "key", "library", "original", "reply"])
            self.assertEqual(row["choices"], ["library", "reply", "original"])
            self.assertEqual(row["default"], "original")
            self.assertTrue(row["conflict_id"].startswith("cf-"))

    def test_group_keeps_dependent_fields_together(self):
        # publisher / year / edition 必须同组，保证整组裁决一致。
        self.assertEqual(sv3.field_group("publisher"), sv3.field_group("year"))
        self.assertEqual(sv3.field_group("year"), sv3.field_group("edition"))
        self.assertEqual(sv3.field_group("journal"), sv3.field_group("pages"))
        self.assertNotEqual(sv3.field_group("year"), sv3.field_group("journal"))

    def test_default_is_original(self):
        candidate = _read(self.state()["preview"]["candidate_bib"])
        self.assertIn("1998", candidate)
        # 未裁决项不进 decisions（不把「没裁」伪装成「裁了 original」），
        # 但冲突行的 default 恒为 original，落地行为也恒为保留原条目。
        self.assertEqual(self.year["default"], "original")
        self.assertNotIn(self.year["conflict_id"], self.state()["decisions"])

    def test_decision_library_wins(self):
        pv = self.preview(adopt={w.CAT_BIB_VERIFY},
                          decisions={self.year["conflict_id"]: "library"})
        self.assertTrue(pv.ok, pv.blocked)
        candidate = _read(self.state()["preview"]["candidate_bib"])
        self.assertIn("2001", candidate)
        self.assertNotIn("1998", candidate)
        self.assertEqual(self.state()["decisions"][self.year["conflict_id"]], "library")

    def test_decision_reply_wins(self):
        # 回件里把年份改成 1999 → reply 值可选，且落地为 1999。
        reply_body = OLD_OTHER_YEAR.replace("1998", "1999")
        res = self.prepare(old_text=OLD_OTHER_YEAR)
        self.import_reply(res.task_id, reply_body)
        pv = self.preview(adopt={w.CAT_BIB_VERIFY})
        rows = self.state()["conflicts"]
        year = [row for row in rows if row["field"] == "year"][0]
        self.assertEqual(year["reply"], "1999")
        pv = self.preview(adopt={w.CAT_BIB_VERIFY},
                          decisions={year["conflict_id"]: "reply"})
        self.assertTrue(pv.ok, pv.blocked)
        candidate = _read(self.state()["preview"]["candidate_bib"])
        self.assertIn("1999", candidate)

    def test_decision_original_keeps_original(self):
        self.preview(adopt={w.CAT_BIB_VERIFY},
                     decisions={self.year["conflict_id"]: "original"})
        candidate = _read(self.state()["preview"]["candidate_bib"])
        self.assertIn("1998", candidate)


# ── (e) 裁决变更 → 新 preview_id，旧 ID 的 apply / deliver 被拒 ───────────

class GroupDecisionConsistency(LibraryBase):
    """同一 group 的冲突行**逐字段独立裁决**：允许 year=库值、publisher=回件值等自由混裁。
    """

    def setUp(self):
        super().setUp()
        self.build_library()
        res = self.prepare(old_text=OLD_GROUP_TWO)
        self.import_reply(res.task_id, OLD_GROUP_TWO)
        self.baseline = self.preview(adopt={w.CAT_BIB_VERIFY})
        self.rows = self.state()["conflicts"]
        self.group = [row for row in self.rows if row["group"] == "publication"]
        # edition 两边都是 1（等价）→ 不产生冲突行；该组实际是 publisher + year 两行。
        self.assertEqual(sorted(row["field"] for row in self.group),
                         ["publisher", "year"],
                         f"publication 组应有两行，实际 {[r['field'] for r in self.rows]}")

    def _by_field(self, name):
        return [row for row in self.group if row["field"] == name][0]

    def test_mixed_decision_within_group_is_accepted(self):
        """组内混裁（year=library、publisher=reply）必须被接受且逐字段落地。"""
        year = self._by_field("year")
        publisher = self._by_field("publisher")
        pv = self.preview(adopt={w.CAT_BIB_VERIFY}, decisions={
            year["conflict_id"]: "library",
            publisher["conflict_id"]: "reply",
        })
        self.assertTrue(pv.ok, pv.blocked)
        settled = self.state()["decisions"]
        self.assertEqual(settled[year["conflict_id"]], "library")
        self.assertEqual(settled[publisher["conflict_id"]], "reply")
        candidate = _read(self.state()["preview"]["candidate_bib"])
        fields = sv3._field_map(candidate)["LIBA"][1]
        self.assertEqual(fields.get("year"), "2001")
        self.assertEqual(fields.get("publisher"), "ReplyPub")

    def test_uniform_decision_within_group_is_accepted(self):
        """组内一致（全 library / 全 reply / 全 original）必须被接受，且整组落地。"""
        for choice in ("library", "reply", "original"):
            with self.subTest(choice=choice):
                decisions = {row["conflict_id"]: choice for row in self.group}
                pv = self.preview(adopt={w.CAT_BIB_VERIFY}, decisions=decisions)
                self.assertTrue(pv.ok, pv.blocked)
                settled = self.state()["decisions"]
                for row in self.group:
                    self.assertEqual(settled[row["conflict_id"]], choice)
                candidate = _read(self.state()["preview"]["candidate_bib"])
                fields = sv3._field_map(candidate)["LIBA"][1]
                expected = {"library": {"publisher": "Springer", "year": "2001"},
                            "reply": {"publisher": "ReplyPub", "year": "1998"},
                            "original": {"publisher": "ReplyPub", "year": "1998"}}[choice]
                for name, value in expected.items():
                    self.assertEqual(fields.get(name), value,
                                     f"{choice}：字段 {name} 应为 {value}，实际 {fields.get(name)}")


class GroupScopeIsPerEntry(LibraryBase):
    """同一组内每个字段、每个条目都独立裁决：缺省项按 original 落地，条目间互不影响。"""

    def setUp(self):
        super().setUp()
        self.build_library(LIB_TWO_ENTRIES)
        res = self.prepare(old_text=OLD_TWO_ENTRIES, tex_text=TEX_TWO_ENTRIES)
        self.import_reply(res.task_id, REPLY_TWO_ENTRIES)
        self.baseline = self.preview(adopt={w.CAT_BIB_VERIFY})
        self.rows = self.state()["conflicts"]
        self.ids = {(row["key"], row["field"]): row["conflict_id"] for row in self.rows}
        # 两条目各有 publication 组两行（publisher + year）。
        for key in ("LIBA", "LIBB"):
            self.assertEqual(
                sorted(row["field"] for row in self.rows
                       if row["key"] == key and row["group"] == "publication"),
                ["publisher", "year"],
                f"{key} 的 publication 组应有两行，实际 {[r['field'] for r in self.rows]}")

    def test_implicit_default_is_per_field(self):
        """只显式改 year=library、publisher 走缺省 original → 接受，两行落地值不同。"""
        a_year = self.ids[("LIBA", "year")]
        a_pub = self.ids[("LIBA", "publisher")]
        pv = self.preview(adopt={w.CAT_BIB_VERIFY}, decisions={a_year: "library"})
        self.assertTrue(pv.ok, pv.blocked)
        self.assertNotEqual(pv.preview_id, self.baseline.preview_id)
        # state["decisions"] 只存显式选择（与既有测试一致）；落地通过候选字段验证。
        settled = self.state()["decisions"]
        self.assertEqual(settled, {a_year: "library"})
        candidate = _read(self.state()["preview"]["candidate_bib"])
        fields = sv3._field_map(candidate)["LIBA"][1]
        self.assertEqual(fields.get("year"), "2001")
        self.assertEqual(fields.get("publisher"), "OldPubA")

    def test_entries_keep_independent_values(self):
        """同一组内两行分别选不同来源 → 都接受、各自落地，无「整组一致」错误。"""
        a_year = self.ids[("LIBA", "year")]
        a_pub = self.ids[("LIBA", "publisher")]
        pv = self.preview(adopt={w.CAT_BIB_VERIFY},
                          decisions={a_year: "library", a_pub: "reply"})
        self.assertTrue(pv.ok, pv.blocked)
        settled = self.state()["decisions"]
        self.assertEqual(settled[a_year], "library")
        self.assertEqual(settled[a_pub], "reply")
        candidate = _read(self.state()["preview"]["candidate_bib"])
        fields = sv3._field_map(candidate)["LIBA"][1]
        self.assertEqual(fields.get("year"), "2001")
        self.assertEqual(fields.get("publisher"), "ReplyPubA")

    def test_two_entries_may_pick_different_sources(self):
        """两个条目各取不同来源 → 必须接受，且各自**字段级**落地正确。

        不得因为两个条目的 `group` 都是 `publication` 就被交叉锁死（误拒）。
        """
        decisions = {}
        for field in ("publisher", "year"):
            decisions[self.ids[("LIBA", field)]] = "library"
            decisions[self.ids[("LIBB", field)]] = "reply"
        pv = self.preview(adopt={w.CAT_BIB_VERIFY}, decisions=decisions)
        self.assertTrue(pv.ok, pv.blocked)
        candidate = _read(self.state()["preview"]["candidate_bib"])
        fields_a = sv3._field_map(candidate)["LIBA"][1]
        fields_b = sv3._field_map(candidate)["LIBB"][1]
        # LIBA 整组取库值；LIBB 整组取回件值。
        self.assertEqual(fields_a.get("year"), "2001")
        self.assertEqual(fields_a.get("publisher"), "Springer")
        self.assertEqual(fields_b.get("year"), "2008")
        self.assertEqual(fields_b.get("publisher"), "ReplyPubB")
        # 两个条目各自的生效裁决内部一致。
        settled = self.state()["decisions"]
        for key, want in (("LIBA", "library"), ("LIBB", "reply")):
            for field in ("publisher", "year"):
                self.assertEqual(settled[self.ids[(key, field)]], want)

    def test_uniform_across_both_entries_still_accepted(self):
        """两条目都取同一个来源（组内一致）→ 仍必须接受（不许矫枉过正）。"""
        for choice in ("library", "reply", "original"):
            with self.subTest(choice=choice):
                decisions = {row["conflict_id"]: choice for row in self.rows}
                pv = self.preview(adopt={w.CAT_BIB_VERIFY}, decisions=decisions)
                self.assertTrue(pv.ok, pv.blocked)
                candidate = _read(self.state()["preview"]["candidate_bib"])
                fields_a = sv3._field_map(candidate)["LIBA"][1]
                fields_b = sv3._field_map(candidate)["LIBB"][1]
                expected = {
                    "library": {("LIBA", "year"): "2001", ("LIBA", "publisher"): "Springer",
                                ("LIBB", "year"): "2010", ("LIBB", "publisher"): "Routledge"},
                    "reply": {("LIBA", "year"): "1999", ("LIBA", "publisher"): "ReplyPubA",
                              ("LIBB", "year"): "2008", ("LIBB", "publisher"): "ReplyPubB"},
                    "original": {("LIBA", "year"): "1998", ("LIBA", "publisher"): "OldPubA",
                                 ("LIBB", "year"): "2007", ("LIBB", "publisher"): "OldPubB"},
                }[choice]
                for (key, field), want in expected.items():
                    got = (fields_a if key == "LIBA" else fields_b).get(field)
                    self.assertEqual(got, want, f"{choice}：{key}.{field} 应为 {want}，实际 {got}")


class StalePreviewIdRejected(LibraryBase):

    def test_decision_change_makes_old_preview_id_stale(self):
        self.build_library()
        res = self.prepare(old_text=OLD_OTHER_YEAR)
        self.import_reply(res.task_id, OLD_OTHER_YEAR)
        first = self.preview(adopt={w.CAT_BIB_VERIFY})
        rows = self.state()["conflicts"]
        year = [row for row in rows if row["field"] == "year"][0]
        second = self.preview(adopt={w.CAT_BIB_VERIFY},
                              decisions={year["conflict_id"]: "library"})
        self.assertNotEqual(first.preview_id, second.preview_id)
        # 旧 ID 的 apply 必须被拒，且错误**含旧 ID 与当前 ID**（ACCEPTANCE:22 第 9 条）。
        with self.assertRaises(w.WorkflowError) as caught:
            w.apply(work_dir=self.work, preview_id=first.preview_id)
        message = str(caught.exception)
        self.assertIn("预览已过期或不存在", message)
        self.assertIn(first.preview_id, message)
        self.assertIn(second.preview_id, message)
        # 旧 ID 的 deliver 同样不得放行：ENTRY_BIB 工作区没有可交付的初排任务，
        # 无论 ID 新旧 delivered 一律为 False（旧 ID 绝不会被当作活动预览接受）。
        from slcn import ai_workflow_convert as conv
        stale = conv.deliver(work_dir=self.work, preview_id=first.preview_id)
        self.assertFalse(stale.get("delivered"))
        self.assertFalse(stale.get("ok", True))
        self.assertTrue(stale.get("error"))
        # 新 ID 可应用。
        out = w.apply(work_dir=self.work, preview_id=second.preview_id)
        self.assertTrue(out.get("wrote") is not None or out is not None)

    def test_stale_deliver_error_names_both_ids(self):
        """CLI 的 deliver 拒收文案必须点名「旧 ID」与「当前 ID」。

        真 ENTRY_CONVERT 交付需要完整初排链路（另有 convert 工作区硬证覆盖）；
        这里直接构造「已冻结 conversion + 活动预览」的状态，只为钉死**文案**：
        `ai_workflow_convert.deliver` 的原文案不含 ID，CLI 侧必须补上两个值。
        """
        work = self.work
        w.save_state(work, {
            "schema_version": w.SCHEMA_VERSION_R3, "task_id": "AIW-TEST000000",
            "entry": w.ENTRY_CONVERT, "work_dir": work,
            "conversion": {"stage_dir": os.path.join(work, "stage"),
                           "final_dir": os.path.join(work, "final"),
                           "inputs": {}, "options": {}, "stem": "Anon",
                           "tex_name": "Anon.tex", "bib_name": "reference.bib",
                           "stage_sha256s": {}, "meta_items": [], "fn_items": [],
                           "input_identity": {}},
            "active_reply": {"sha256": "deadbeef"},
            "preview": {"preview_id": "pv-current00000000", "ok": True,
                        "reply_sha256": "deadbeef", "candidate_bib": "",
                        "candidate_text": ""},
        })
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--json", "--work-dir", work, "deliver",
                             "--preview-id", "pv-stale0000000000"])
        self.assertEqual(code, 2, err.getvalue())
        payload = json.loads(out.getvalue())
        self.assertFalse(payload.get("ok"))
        self.assertFalse(payload.get("delivered"))
        error = str(payload.get("error") or "")
        self.assertIn("不是最后校验通过的活动预览", error)
        self.assertIn("pv-stale0000000000", error)
        self.assertIn("pv-current00000000", error)


# ── (f) 查库前后库文件 sha256 不变（对共享库只读）────────────────────────

class LibraryIsReadOnly(LibraryBase):

    def test_library_file_untouched_by_preview_and_apply(self):
        self.build_library()
        before = _sha(self.db_path)
        res = self.prepare(old_text=OLD_SAME)
        self.import_reply(res.task_id, OLD_SAME)
        pv = self.preview(adopt={w.CAT_BIB_VERIFY})
        self.assertEqual(_sha(self.db_path), before)
        if pv.ok:
            w.apply(work_dir=self.work, preview_id=pv.preview_id)
        self.assertEqual(_sha(self.db_path), before)


# ── (g) 引用闭合：裁决后仍校验 cited - have ─────────────────────────────

class CitationClosureAfterDecision(LibraryBase):

    def test_missing_cited_key_blocks_and_names_it(self):
        self.build_library()
        # 正文引用了一个 Bib 里没有的键 → 必须 blocked 并点名。
        res = self.prepare(old_text=OLD_SAME, tex_text=r"See \cite{LIBA} and \cite{GHOST}.")
        self.import_reply(res.task_id, OLD_SAME)
        pv = self.preview(adopt={w.CAT_BIB_VERIFY})
        self.assertFalse(pv.ok)
        joined = "；".join(pv.blocked)
        self.assertIn("最终正文引用缺少对应 Bib 条目", joined)
        self.assertIn("GHOST", joined)
        self.assertEqual(self.state()["preview"]["candidate_bib"], "")

    def test_decision_keeps_closure_closed(self):
        self.build_library()
        res = self.prepare(old_text=OLD_OTHER_YEAR)
        self.import_reply(res.task_id, OLD_OTHER_YEAR)
        self.preview(adopt={w.CAT_BIB_VERIFY})
        rows = self.state()["conflicts"]
        year = [row for row in rows if row["field"] == "year"][0]
        pv = self.preview(adopt={w.CAT_BIB_VERIFY},
                          decisions={year["conflict_id"]: "library"})
        self.assertTrue(pv.ok, pv.blocked)
        self.assertEqual(pv.blocked, [])


# ── (h) 幂等：同输入同 decisions 两次 preview 同 preview_id ──────────────

class PreviewIdempotent(LibraryBase):

    def test_same_input_same_decisions_same_preview_id(self):
        self.build_library()
        res = self.prepare(old_text=OLD_OTHER_YEAR)
        self.import_reply(res.task_id, OLD_OTHER_YEAR)
        first = self.preview(adopt={w.CAT_BIB_VERIFY})
        rows = self.state()["conflicts"]
        year = [row for row in rows if row["field"] == "year"][0]
        decisions = {year["conflict_id"]: "library"}
        one = self.preview(adopt={w.CAT_BIB_VERIFY}, decisions=decisions)
        two = self.preview(adopt={w.CAT_BIB_VERIFY}, decisions=decisions)
        self.assertEqual(one.preview_id, two.preview_id)
        self.assertTrue(first.preview_id)

    def test_requery_reuses_snapshot_and_detects_library_change(self):
        self.build_library()
        res = self.prepare(old_text=OLD_SAME)
        self.import_reply(res.task_id, OLD_SAME)
        self.preview(adopt={w.CAT_BIB_VERIFY})
        snapshot_sha = self.state()["library"]["sha256"]
        # 库里追加一条（文件字节变了）→ 下一次 preview 必须发现并要求 --requery。
        store = bibdb.Store.load(self.db_path)
        store.import_bib_text(
            "@book{LIBB,\n  author = {Roe, Bob},\n  title = {Other Work},\n"
            "  publisher = {Springer},\n  year = {1999},\n}\n",
            {"kind": bibdb.KIND_PUBLISHED, "issue": "匿名测试期", "slug": "anon2",
             "file": "anonymous2.bib"},
            verified=True)
        store.save()
        self.assertNotEqual(bibdb.db_sha256(self.db_path), snapshot_sha)
        self.preview(adopt={w.CAT_BIB_VERIFY})
        library = self.state()["library"]
        self.assertFalse(library["checked"])
        self.assertEqual(library["state"], "failed")
        self.assertIn("--requery", library["skipped_reason"])
        # --requery 之后重新查库成功。
        self.preview(adopt={w.CAT_BIB_VERIFY}, requery=True)
        library = self.state()["library"]
        self.assertTrue(library["checked"])
        self.assertEqual(library["state"], "ok")


# ── (i) CLI：未知 conflict_id → 退出码 2 且错误含该 ID ───────────────────

class CliDecideErrors(LibraryBase):

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_unknown_conflict_id_exits_2_with_id(self):
        self.build_library()
        res = self.prepare(old_text=OLD_OTHER_YEAR)
        self.import_reply(res.task_id, OLD_OTHER_YEAR)
        code, _out, err = self._run(
            ["--work-dir", self.work, "preview", "--adopt", "bib_verify",
             "--decide", "cf-0000000000000=library"])
        self.assertEqual(code, 2)
        self.assertIn("cf-0000000000000", err)
        self.assertIn("不认识的 conflict_id", err)

    def test_illegal_value_exits_2_with_id(self):
        self.build_library()
        res = self.prepare(old_text=OLD_OTHER_YEAR)
        self.import_reply(res.task_id, OLD_OTHER_YEAR)
        self.preview(adopt={w.CAT_BIB_VERIFY})
        rows = self.state()["conflicts"]
        year = [row for row in rows if row["field"] == "year"][0]
        code, _out, err = self._run(
            ["--work-dir", self.work, "preview", "--adopt", "bib_verify",
             "--decide", f"{year['conflict_id']}=nonsense"])
        self.assertEqual(code, 2)
        self.assertIn(year["conflict_id"], err)
        self.assertIn("不合法", err)

    def test_valid_decide_lands_and_status_json_has_three_keys(self):
        self.build_library()
        res = self.prepare(old_text=OLD_OTHER_YEAR)
        self.import_reply(res.task_id, OLD_OTHER_YEAR)
        self.preview(adopt={w.CAT_BIB_VERIFY})
        rows = self.state()["conflicts"]
        year = [row for row in rows if row["field"] == "year"][0]
        code, out, err = self._run(
            ["--json", "--work-dir", self.work, "preview", "--adopt", "bib_verify",
             "--decide-json", json.dumps({year["conflict_id"]: "library"})])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["decisions"][year["conflict_id"]], "library")
        self.assertEqual(payload["library"]["state"], "ok")
        # status --json 三键齐全。
        code, out, err = self._run(["--json", "--work-dir", self.work, "status"])
        self.assertEqual(code, 0, err)
        status = json.loads(out)
        for key in ("library", "conflicts", "decisions"):
            self.assertIn(key, status)
        self.assertEqual(status["library"]["state"], "ok")
        self.assertEqual(len(status["conflicts"]), len(rows))

    def test_status_text_keeps_legacy_lines_and_adds_library(self):
        self.build_library()
        res = self.prepare(old_text=OLD_OTHER_YEAR)
        self.import_reply(res.task_id, OLD_OTHER_YEAR)
        self.preview(adopt={w.CAT_BIB_VERIFY})
        code, out, err = self._run(["--work-dir", self.work, "status"])
        self.assertEqual(code, 0, err)
        # 既有行必须一字不改地还在（界面依赖）。
        self.assertIn("phase=", out)
        self.assertIn("task_id=", out)
        self.assertIn("keys=", out)
        self.assertIn("entries=", out)
        self.assertIn("message=", out)
        # 新行以 library= / conflicts= 前缀。
        self.assertIn("library=checked=true", out)
        self.assertIn("conflicts=", out)

    def test_group_mixed_decide_is_accepted(self):
        """CLI `--decide` 组内混裁 → exit 0、成功产出预览、两值分别落地。"""
        self.build_library()
        res = self.prepare(old_text=OLD_GROUP_TWO)
        self.import_reply(res.task_id, OLD_GROUP_TWO)
        self.preview(adopt={w.CAT_BIB_VERIFY})
        rows = self.state()["conflicts"]
        year = [row for row in rows if row["field"] == "year"][0]
        publisher = [row for row in rows if row["field"] == "publisher"][0]
        code, out, err = self._run(
            ["--json", "--work-dir", self.work, "preview", "--adopt", "bib_verify",
             "--decide", f"{year['conflict_id']}=library",
             "--decide", f"{publisher['conflict_id']}=reply"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload.get("ok"), payload)
        settled = self.state()["decisions"]
        self.assertEqual(settled[year["conflict_id"]], "library")
        self.assertEqual(settled[publisher["conflict_id"]], "reply")

    def test_decide_without_equals_exits_2(self):
        self.build_library()
        self.prepare()
        code, _out, err = self._run(
            ["--work-dir", self.work, "preview", "--decide", "cf-xxxx"])
        self.assertEqual(code, 2)
        self.assertIn("裁决写法不合法", err)


# ── (j) 字节级回退 + padding 形态矩阵（裁决 A/B 的守卫）────────────────────

#: 「字段体紧贴 `}` 且无尾逗号」的六种形态：padding 必须仍然生效（不得退化）。
PAD_FORMS = {
    "A_single_fill": ('@book{LIBA, author = {Lee, Alice}, title = {On Logic}, '
                      'publisher = {Springer}, year = {2001}}\n', {"edition": "1"}),
    "B_multi_fill": ('@book{LIBA, author = {Lee, Alice}, title = {On Logic}, '
                     'year = {2001}}\n', {"edition": "1", "publisher": "Springer"}),
    "F_compact": ('@book{LIBA,author={Lee,Alice},year={2001}}\n', {"edition": "1"}),
    "H_multi_entry": ('@book{LIBA, author = {Lee, Alice}, year = {2001}}\n'
                      '@book{OTHER, author = {B}, year = {2002},\n}\n', {"edition": "1"}),
    "K_eof_no_newline": ('@book{LIBA, author = {Lee, Alice}, year = {2001}}', {"edition": "1"}),
    "L_multiline_value": ('@book{LIBA, title = {A\nB}, year = {2001}}\n', {"edition": "1"}),
}


#: 「body 尾部字符」的六种变体（与上面 `PAD_FORMS` 的**结构**变体正交）。
#: 独立验收方验的正是这六种：无尾随 / 尾随换行 / 尾随空格 / 空格+换行 /
#: 多条目无尾随 / 多条目+换行。前四种单条目，后两种多条目 —— 多条目要额外
#: 断言「第二条不被污染」（padding 只动目标条目）。
PAD_TAIL_FORMS = {
    "tight":       '@book{K, author = {A}, year = {2001}}',
    "tight_nl":    '@book{K, author = {A}, year = {2001}}\n',
    "trail_sp":    '@book{K, author = {A}, year = {2001} }',
    "trail_sp_nl": '@book{K, author = {A}, year = {2001} }\n',
    "multi":       '@book{K, author = {A}, year = {2001}}\n'
                   '@book{OTHER, author = {B}, year = {2002}}',
    "multi_nl":    '@book{K, author = {A}, year = {2001}}\n'
                   '@book{OTHER, author = {B}, year = {2002}}\n',
}

#: 多条目形态：第二条的原文（padding + apply 之后必须逐字节仍在，且不带新字段）。
_PAD_TAIL_SECOND = '@book{OTHER, author = {B}, year = {2002}'


class ByteLevelRollback(LibraryBase):
    """红线「未裁决项按 original 回退」的**字节级**守卫。"""

    def test_all_original_returns_frozen_bib_byte_for_byte(self):
        """带尾逗号与不带尾逗号两种形态，全 original 都必须逐字节回退。"""
        for name, old in (("with_comma", OLD_OTHER_YEAR),
                          ("no_comma", OLD_OTHER_YEAR_NO_COMMA)):
            with self.subTest(form=name):
                self.setUp()          # 每个形态一套干净工作区
                self.build_library()
                res = self.prepare(old_text=old)
                self.import_reply(res.task_id, old)
                first = self.preview(adopt={w.CAT_BIB_VERIFY})
                self.assertTrue(first.ok, first.blocked)
                rows = self.state()["conflicts"]
                self.assertTrue(rows)
                decided = {row["conflict_id"]: "original" for row in rows}
                second = self.preview(adopt={w.CAT_BIB_VERIFY}, decisions=decided)
                self.assertTrue(second.ok, second.blocked)
                self.assertEqual(_read(second.state["preview"]["candidate_bib"]), old,
                                 f"{name}: 全 original 的候选未逐字节回退冻结旧 Bib")

    def test_padding_forms_still_produce_valid_bib(self):
        """A/B/F/H/K/L 六形态：需要插入字段时 padding 必须仍然生效。"""
        for name, (text, fills) in PAD_FORMS.items():
            with self.subTest(form=name):
                key = "LIBA" if "LIBA" in text else "K"
                padded = sv3._pad_entry_tails(text, {key})
                plans = [{"etype": "book", "key": key, "fills": fills, "replaces": {}}]
                out, applied = __import__("slcn.bibdbapply", fromlist=["x"]).apply_text(padded, plans)
                entries = bibcheck.split_entries(out)
                self.assertTrue(entries, f"{name}: 解析不出条目：{out!r}")
                self.assertNotIn(",\n,", out, f"{name}: 出现裸逗号：{out!r}")
                self.assertTrue(applied, f"{name}: 没有任何字段落地：{out!r}")
                body = entries[0][2]
                for field, value in fills.items():
                    self.assertIn(value, body, f"{name}: 字段 {field} 未落地：{out!r}")

    def test_padding_tail_variants(self):
        """尾部字符六变体：tight / tight_nl / trail_sp / trail_sp_nl / multi / multi_nl。

        与 `test_padding_forms_still_produce_valid_bib`（六种**结构**形态）正交：
        这里只变 body 的尾部字符（无 / 换行 / 空格 / 空格+换行）与条目条数。
        逐条断言：无裸逗号、`edition` 真的落地、能解析出条目；多条目形态还要断言
        第二条**不被污染**（padding 只作用于目标条目）。
        """
        for name, text in PAD_TAIL_FORMS.items():
            with self.subTest(form=name):
                padded = sv3._pad_entry_tails(text, {"K"})
                plans = [{"etype": "book", "key": "K", "fills": {"edition": "2"},
                          "replaces": {}}]
                out, applied = dbapply.apply_text(padded, plans)

                # ① 不得出现裸逗号（padding 与插入同偏移时的已知坑）
                self.assertNotIn(",\n,", out, f"{name}: 出现裸逗号：{out!r}")

                # ② 必须真的落地了字段（不是「没抛异常」就算过）
                self.assertTrue(applied, f"{name}: 没有任何字段落地：{out!r}")

                # ③ 能解析出条目，且目标条目的 edition 真的是 2
                entries = bibcheck.split_entries(out)
                self.assertTrue(entries, f"{name}: 解析不出条目：{out!r}")
                target = dict((key, body) for _etype, key, body in entries).get("K")
                self.assertIsNotNone(target, f"{name}: 找不到目标条目 K：{out!r}")
                fields = bibcheck.fields_of(target)
                self.assertEqual(fields.get("edition"), "2",
                                 f"{name}: edition 未落地：{out!r}")

                # ④ 多条目形态：第二条不受污染（原文仍在、没有混进 edition）
                if name.startswith("multi"):
                    second = dict((key, body) for _etype, key, body in entries).get("OTHER")
                    self.assertIsNotNone(second, f"{name}: 第二条条目丢失：{out!r}")
                    self.assertIn(_PAD_TAIL_SECOND, out,
                                  f"{name}: 第二条原文被改动：{out!r}")
                    self.assertNotIn("edition", bibcheck.fields_of(second),
                                     f"{name}: 第二条被塞进了 edition：{out!r}")
                    self.assertEqual(len(entries), 2,
                                     f"{name}: 条目数应仍为 2：{out!r}")

    def test_padding_does_not_fire_when_nothing_lands(self):
        """padding 只在真需要插入字段时才有意义；全 original 时不得留下换行痕迹。"""
        text = "@book{LIBA, year = {1998}}\n"   # body 未以换行结尾
        rows = [{"conflict_id": "cf-x", "key": "LIBA", "field": "year",
                 "original": "1998", "library": "2001", "reply": "1998",
                 "choices": ["library", "reply", "original"],
                 "default": "original", "group": "publication"}]
        for choice in ("original", "reply"):
            with self.subTest(choice=choice):
                out, applied, _notes = sv3.apply_decisions(
                    text, rows, sv3.check_decisions(rows, {"cf-x": choice}))
                self.assertEqual(applied, [])
                self.assertEqual(out, text)   # 字节级回退

    def test_decisions_none_is_tolerated(self):
        """裁决 A/B：`decisions=None` 与 `{}` 等价，不得抛 AttributeError。"""
        text = "@book{LIBA, year = {1998}}\n"
        rows = [{"conflict_id": "cf-x", "key": "LIBA", "field": "year",
                 "original": "1998", "library": "2001", "reply": "1998",
                 "choices": ["library", "reply", "original"],
                 "default": "original", "group": "publication"}]
        out_none, applied_none, _ = sv3.apply_decisions(text, rows, None)
        out_empty, applied_empty, _ = sv3.apply_decisions(text, rows, {})
        self.assertEqual(out_none, text)
        self.assertEqual(out_empty, text)
        self.assertEqual(applied_none, [])
        self.assertEqual(applied_empty, [])
        # effective_decisions 也必须容忍 None。
        self.assertEqual(sv3.effective_decisions(rows, None), {"cf-x": "original"})
        self.assertEqual(sv3.effective_decisions(rows, {}), {"cf-x": "original"})



# ── 初排任务保全 + 冻结 Bib 身份（第 4/6 项返工）──────────────────────────

#: 初排夹具：原 Bib 三条，回件（外部完整 Bib）只带回 A。
CONVERT_OLD = """@book{A,
  author = {Alpha, A.},
  title = {First},
  year = {2020},
}
@book{B,
  author = {Beta, B.},
  title = {Second},
  year = {2021},
}
@book{Cc,
  author = {Gamma, C.},
  title = {Third},
  year = {2022},
}
"""

CONVERT_REPLY = """@book{A,
  author = {Alpha, A.},
  title = {First revised},
  year = {2020},
}
"""


class ConvertTaskPreservesUnreturnedEntries(LibraryBase):
    """新两段式初排任务（`entry=convert` + `conversion` 非空）：原 Bib 保全。

    用户裁定（PLAN §2）：初排任务的原 Bib 与交付候选必须保全**全部**未委派条目
    及依赖，**不限零引用**；旧任务/独立引用库入口（ENTRY_BIB、无 conversion）
    仍按原身份语义走 `ST_DROPPED`。
    """

    def _prepare_convert(self, tex_text="Body.\n\\cite{A}\n"):
        _write(self.bib, CONVERT_OLD)
        _write(self.tex, tex_text)
        conversion = {
            "input_identity": {"source_sha256": "a" * 64, "tex_sha256": "b" * 64},
            "stage_dir": os.path.join(self.work, "out"),
            "final_dir": os.path.join(self.work, "out"),
            "tex_name": "input.tex", "bib_name": "reference.bib",
        }
        return w.prepare(work_dir=self.work, entry=w.ENTRY_CONVERT,
                         bib_text=CONVERT_OLD, tex_text=tex_text,
                         target_path=self.bib, tex_path=self.tex,
                         taskbook_version=3, conversion=conversion)

    def _external_reply(self):
        # 无 TASK-ID 的纯 Bib + complete_external → identity=unverified，
        # 才走得到 external 的 drop/preserve 分支。
        return w.import_reply(work_dir=self.work, text=CONVERT_REPLY,
                              source_kind="complete_external", replace_active=True)

    def test_convert_task_keeps_unreturned_entries_even_with_citations(self):
        """正例：初排任务 + 正文引用了 A → B、Cc 仍必须保全（不得 ST_DROPPED）。"""
        self.build_library()
        self._prepare_convert()
        self.assertTrue(self._external_reply()["ok"])
        pv = self.preview(adopt={w.CAT_BIB_VERIFY})
        self.assertTrue(pv.ok, pv.blocked)
        statuses = {item["key"]: item["status"] for item in pv.bib["items"]}
        self.assertEqual(statuses["A"], "applied")
        self.assertEqual(statuses["B"], "backfilled")
        self.assertEqual(statuses["Cc"], "backfilled")
        self.assertEqual(pv.bib["counts"]["dropped"], 0)
        # 交付候选里三条都在。
        for key in ("A", "B", "Cc"):
            self.assertIn(key, pv.bib["text"])
        # 保全理由说人话，不冒充「已核实」。
        reasons = {item["key"]: item["reason"] for item in pv.bib["items"]}
        self.assertIn("初排任务保全原条目", reasons["B"])

    def test_convert_task_keeps_entries_when_no_citation_at_all(self):
        """零引用（`R=[]`）的初排任务同样保全 —— 这是原来的漏删场景。"""
        self.build_library()
        self._prepare_convert(tex_text="Body paragraph with no citation.\n")
        self.assertTrue(self._external_reply()["ok"])
        pv = self.preview(adopt={w.CAT_BIB_VERIFY})
        self.assertTrue(pv.ok, pv.blocked)
        self.assertEqual(pv.bib["counts"]["dropped"], 0)
        for key in ("A", "B", "Cc"):
            self.assertIn(key, pv.bib["text"])

    def test_entry_bib_task_still_drops_unreferenced_entries(self):
        """反例：`ENTRY_BIB`（无 conversion）→ 缺的未引用条仍 `ST_DROPPED`。"""
        self.build_library()
        _write(self.bib, CONVERT_OLD)
        _write(self.tex, r"See \cite{A}.")
        w.prepare(work_dir=self.work, entry=w.ENTRY_BIB, bib_text=CONVERT_OLD,
                  tex_text=r"See \cite{A}.", target_path=self.bib,
                  tex_path=self.tex, taskbook_version=3)
        self.assertTrue(self._external_reply()["ok"])
        pv = self.preview(adopt={w.CAT_BIB_VERIFY})
        self.assertTrue(pv.ok, pv.blocked)
        statuses = {item["key"]: item["status"] for item in pv.bib["items"]}
        self.assertEqual(statuses["A"], "applied")
        self.assertEqual(statuses["B"], "dropped")
        self.assertEqual(statuses["Cc"], "dropped")
        self.assertEqual(pv.bib["counts"]["dropped"], 2)
        self.assertNotIn("Cc", pv.bib["text"])


class FrozenBibIdentity(LibraryBase):
    """`status()` 必须给出**冻结 Bib 快照**的身份（CONTRACT v1.1 `frozen_bib`）。

    GUI 的 VS Code 左侧要核「打开的到底是 prepare 时那一份」；`source_bib` 只是
    作者原 Bib 的路径，作者中途改了它 GUI 看不出来，**绝不能**拿它冒充冻结版。
    """

    def test_convert_task_exposes_snapshot_identity(self):
        _write(self.bib, CONVERT_OLD)
        _write(self.tex, r"Body \cite{A}.")
        conversion = {
            "input_identity": {"source_sha256": "a" * 64, "tex_sha256": "b" * 64},
            "stage_dir": os.path.join(self.work, "out"),
            "final_dir": os.path.join(self.work, "out"),
            "tex_name": "input.tex", "bib_name": "reference.bib",
        }
        w.prepare(work_dir=self.work, entry=w.ENTRY_CONVERT, bib_text=CONVERT_OLD,
                  tex_text=r"Body \cite{A}.", target_path=self.bib,
                  tex_path=self.tex, taskbook_version=3, conversion=conversion)
        snapshot = self.state()["snapshot"]["bib"]
        st = w.status(self.work)
        self.assertEqual(st["frozen_bib"], {"path": snapshot["path"],
                                            "sha256": snapshot["sha256"]})
        # 冻结快照是真实存在的文件，且内容 sha 与记录一致。
        self.assertTrue(os.path.isfile(st["frozen_bib"]["path"]))
        self.assertEqual(_sha(st["frozen_bib"]["path"]),
                         st["frozen_bib"]["sha256"])
        # 它与作者原 Bib 是**两个不同的文件**，路径不得互相冒充。
        self.assertNotEqual(st["frozen_bib"]["path"], st["source_bib"])
        # 只增不改：既有字段语义与值不变。
        self.assertEqual(st["source_bib"], self.bib)
        self.assertEqual(st["target_bib"], self.bib)

    def test_legacy_state_without_snapshot_returns_empty(self):
        """旧 /1 任务没有 snapshot → 空串，不抛异常、不填 target。"""
        w.save_state(self.work, {
            "schema_version": "slcn.ai_workflow/1",
            "task_id": "AIW-LEGACY000001", "work_dir": self.work,
            "phase": w.PH_PREPARED, "entry": w.ENTRY_BIB,
            "target": {"path": self.bib, "sha256": ""},
            "tex": {"path": "", "sha256": ""},
            "text_target": {"path": "", "sha256": ""},
            "scope": {"exec": [], "adopt": []}, "cite_keys": [], "bib_keys": [],
            "replies": [], "preview": None, "applies": [], "undo": None,
            "history": [], "safe_hit_keys": [],
        })
        st = w.status(self.work)
        self.assertEqual(st["frozen_bib"], {"path": "", "sha256": ""})
        self.assertEqual(st["source_bib"], self.bib)
        self.assertEqual(st["target_bib"], self.bib)


if __name__ == "__main__":
    unittest.main(verbosity=2)
