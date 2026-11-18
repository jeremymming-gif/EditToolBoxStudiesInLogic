#!/usr/bin/env python3.14
r"""C 包回归：一份 Markdown 回件、一次导入、错误可定位、按范围给分支提示。

对应执行卡 §1 C 项与红线 3.1–3.5。全部夹具匿名自造，不搬任何真实稿件。

覆盖：
  (a) 未闭合 bibtex 围栏        → 整份拒收，理由含「围栏未闭合」+ 行号
  (b) 两个不同 bibtex 块        → 整份拒收，理由点明「多个冲突结果块」
  (c) 声称新协议但 JSON 区不唯一 / 未闭合 → 各自拒收且错误文本不同
  (d) TASK-ID 不符 / 多个不一致  → 拒收且带声明值与要求值
  (e) 未验证身份纯 Bib 接受 / 夹带叙述拒收
  (f) 一次导入成功路径：单份 Markdown（bibtex 围栏 + JSON 区）一次解析出
      bib_text 与 typeset/text 结果，并经 state_v3 一次导入落盘
  (g) 四种范围各一条分支提示断言
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from slcn import ai_workflow as w
from slcn import ai_workflow_reply as r

TID = "AIW-R3C00001234"


def ctx(*, keys=("KA01",), t=("KA01",), o=None, m=(), f=(), u=(),
        body_units=(), typeset=True, text=False, report_only=False):
    """最小上下文：sets/execution 语义与 taskbook 一致（O 全量、T 委派待核）。"""
    all_keys = list(keys)
    return {
        "version": 3,
        "task_id": TID,
        "sets": {"O": list(o if o is not None else all_keys), "T": list(t),
                 "F": list(f), "U": list(u), "M": list(m),
                 "R": [], "C": [], "A": []},
        "items": [{"key": k, "kind": "article"} for k in all_keys],
        "body_units": list(body_units),
        "execution": {"bib_verify": True, "typeset": typeset, "text": text},
        "fn_items": [], "meta_items": [],
    }


def entry(key="KA01", title="Alpha"):
    return f"@article{{{key},\n  author = {{A. Author}},\n  title = {{{title}}},\n  year = {{2024}}\n}}\n"


def bib_reply(*, task_id=TID, key="KA01", title="Alpha", json_body=None):
    """一份 Markdown 回件：可选 TASK-ID 头 + bibtex 围栏 + 可选 JSON 区。"""
    head = f"% TASK-ID: {task_id}\n" if task_id else ""
    parts = [head, "```bibtex\n", entry(key, title), "```\n"]
    if json_body is not None:
        parts += ["```json\n", json_body, "\n```\n"]
    return "".join(parts)


def json_typeset(items=(), status="completed", reason=None):
    node = {"status": status, "items": list(items)}
    if reason is not None:
        node["reason"] = reason
    return '{"schema":"slcn.ai_result/3","typeset":%s,"text":{"status":"not_run"}}' % (
        __import__("json").dumps(node))


class LocatableRejectionTest(unittest.TestCase):
    """(a)(b)(c)(d)：拒收，且理由能让人直接改回件。"""

    def test_a_unclosed_bibtex_fence_rejects_with_position(self):
        text = f"% TASK-ID: {TID}\n```bibtex\n{entry()}"  # 没有闭合围栏
        result = r.parse_reply(text=text, context=ctx())
        self.assertFalse(result["ok"])
        self.assertEqual(result["bib_text"], "")
        self.assertEqual(result["branch"], r.BRANCH_REJECTED)
        self.assertEqual(result["errors"][0]["code"], "bibtex_fence_unclosed")
        # 能定位：围栏类型 + 开围栏行号
        self.assertEqual(result["errors"][0]["line"], 2)
        message = result["errors"][0]["message"]
        self.assertIn("bibtex 围栏未闭合", message)
        self.assertIn("第 2 行", message)
        self.assertIn("整份拒收", message)
        # 拒收不是「格式错误」四个字，且给出下一步
        self.assertIn("整份重新导入", result["next_step"])

    def test_b_two_different_bibtex_blocks_reject_as_conflicting(self):
        text = (f"% TASK-ID: {TID}\n"
                "```bibtex\n" + entry("KA01", "Alpha") + "```\n"
                "```bibtex\n" + entry("KA01", "Beta") + "```\n")
        result = r.parse_reply(text=text, context=ctx())
        self.assertFalse(result["ok"])
        self.assertEqual(result["bib_text"], "")
        self.assertEqual(result["errors"][0]["code"], "conflicting_bibtex_blocks")
        message = result["errors"][0]["message"]
        self.assertIn("多个冲突结果块", message)
        self.assertIn("2 个", message)
        self.assertIn("第 2、9 行", message)   # 两个冲突块各自的围栏行
        self.assertEqual(result["errors"][0]["line"], 2)  # 机器可读的首块行号
        self.assertIn("不能挑一份用", message)

    def test_b_identical_duplicate_block_is_not_a_conflict(self):
        # 同一结果抄两遍不算冲突（不放松“多块冲突”，但也不误伤重复粘贴）。
        body = entry("KA01", "Alpha")
        text = (f"% TASK-ID: {TID}\n"
                f"```bibtex\n{body}```\n```bibtex\n{body}```\n")
        result = r.parse_reply(text=text, context=ctx())
        self.assertTrue(result["ok"], result["errors"])

    def test_c_json_zone_not_unique_and_unclosed_are_distinct_errors(self):
        two = (bib_reply() + "```json\n" + json_typeset() + "\n```\n"
               + "```json\n" + json_typeset() + "\n```\n")
        not_unique = r.parse_reply(text=two, context=ctx())
        self.assertFalse(not_unique["ok"])
        self.assertEqual(not_unique["errors"][0]["code"], "json_zone_not_unique")
        self.assertIn("JSON 区不唯一", not_unique["errors"][0]["message"])
        self.assertIn("2 个", not_unique["errors"][0]["message"])

        unclosed = bib_reply() + "```json\n" + json_typeset()  # 未闭合
        broken = r.parse_reply(text=unclosed, context=ctx())
        self.assertFalse(broken["ok"])
        self.assertEqual(broken["errors"][0]["code"], "json_fence_unclosed")
        self.assertIn("JSON 围栏未闭合", broken["errors"][0]["message"])

        # 两条错误文本必须不同（否则用户分不清是「多了」还是「少了闭合」）
        self.assertNotEqual(not_unique["errors"][0]["message"],
                            broken["errors"][0]["message"])
        self.assertNotEqual(not_unique["errors"][0]["code"],
                            broken["errors"][0]["code"])

    def test_c_claimed_schema_but_broken_json_rejects_with_line(self):
        text = bib_reply() + "```json\n{broken\n```\n"
        result = r.parse_reply(text=text, context=ctx())
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "json_parse_error")
        self.assertEqual(result["errors"][0]["line"], 9)  # JSON 开围栏所在行
        self.assertIn("JSON 解析失败", result["errors"][0]["message"])

    def test_d_wrong_task_id_reports_declared_and_required(self):
        text = bib_reply(task_id="AIW-R3C99999999")
        result = r.parse_reply(text=text, context=ctx())
        self.assertFalse(result["ok"])
        self.assertEqual(result["identity_status"], "mismatch")
        self.assertEqual(result["errors"][0]["code"], "task_id_mismatch")
        message = result["errors"][0]["message"]
        self.assertIn("AIW-R3C99999999", message)   # 声明值
        self.assertIn(TID, message)                 # 要求值
        self.assertIn("第 1 行", message)
        self.assertEqual(result["bib_text"], "")

    def test_d_conflicting_task_ids_report_all_values(self):
        text = (f"% TASK-ID: {TID}\n"
                f"% TASK-ID: AIW-R3C55555555\n"
                "```bibtex\n" + entry() + "```\n")
        result = r.parse_reply(text=text, context=ctx())
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "task_id_conflict")
        message = result["errors"][0]["message"]
        self.assertIn(TID, message)
        self.assertIn("AIW-R3C55555555", message)
        self.assertIn("第 1 行", message)
        self.assertIn("第 2 行", message)
        self.assertEqual(result["bib_text"], "")

    def test_a_every_rejection_carries_a_next_step(self):
        cases = [
            f"% TASK-ID: {TID}\n```bibtex\n{entry()}",
            f"% TASK-ID: {TID}\n```bibtex\n{entry()}```\n```bibtex\n{entry('KA02')}```\n",
            bib_reply(task_id="AIW-OTHER"),
            bib_reply() + "```json\n{broken\n```\n",
            "这是说明文字。\n" + entry(),   # 未验证 + 叙述
        ]
        for text in cases:
            result = r.parse_reply(text=text, context=ctx())
            self.assertFalse(result["ok"])
            self.assertEqual(result["branch"], r.BRANCH_REJECTED)
            self.assertTrue(result["next_step"])
            self.assertTrue(result["errors"][0]["message"])
            self.assertIn("整份", result["branch_hint"])


class UnverifiedPlainBibTest(unittest.TestCase):
    """(e)：未验证身份只收纯 Bib。"""

    def test_e_plain_bib_without_task_id_is_accepted(self):
        text = "```bibtex\n" + entry() + "```\n"
        result = r.parse_reply(text=text, context=ctx())
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["identity_status"], "unverified")
        self.assertEqual(result["branch"], r.BRANCH_UNVERIFIED_PLAIN_BIB)
        self.assertIn("KA01", result["bib_text"])
        self.assertIn("未验证", result["branch_hint"])

    def test_e_plain_bib_without_fence_is_accepted(self):
        result = r.parse_reply(text=entry(), context=ctx())
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["branch"], r.BRANCH_UNVERIFIED_PLAIN_BIB)

    def test_e_prose_mixed_with_bib_is_rejected_with_line(self):
        text = "这是本次核对的结果说明。\n" + entry()
        result = r.parse_reply(text=text, context=ctx())
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "unverified_not_plain_bib")
        message = result["errors"][0]["message"]
        self.assertIn("只接受纯 Bib", message)
        self.assertIn("第 1 行", message)   # 点到具体叙述行
        self.assertIn("这是本次核对的结果说明", message)

    def test_e_unverified_cannot_smuggle_a_claimed_schema(self):
        text = "```json\n" + json_typeset() + "\n```\n"   # 无 TASK-ID 却声称新协议
        result = r.parse_reply(text=text, context=ctx())
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "unverified_not_plain_bib")
        self.assertIn("声称新协议", result["errors"][0]["message"])

    def test_e_unverified_cannot_smuggle_prose_outside_the_fence(self):
        # 未验证身份：围栏内是纯 Bib，围栏外夹带叙述 —— 仍整份拒收并点到行。
        text = "```bibtex\n" + entry() + "```\n以上是本次结果，请查收。\n"
        result = r.parse_reply(text=text, context=ctx())
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "unverified_not_plain_bib")
        message = result["errors"][0]["message"]
        self.assertIn("围栏之外", message)
        self.assertIn("第 8 行", message)
        self.assertIn("以上是本次结果", message)

    def test_e_verified_reply_may_still_carry_commentary(self):
        # 反过来：已验证身份允许围栏外有说明文字（任务书示例就带说明）。
        text = bib_reply() + "\nExtra commentary\n"
        result = r.parse_reply(text=text, context=ctx())
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["identity_status"], "verified")

    def test_e_external_complete_bib_branch_is_distinct(self):
        text = "```bibtex\n" + entry() + "```\n"
        result = r.parse_reply(text=text, context=ctx(),
                               source_kind="complete_external")
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["source_kind"], "complete_external")
        self.assertEqual(result["branch"], r.BRANCH_EXTERNAL_COMPLETE_BIB)
        self.assertIn("外部完整 Bib", result["branch_hint"])
        # 外部完整 Bib 不做 T 覆盖（整份替换语义）
        self.assertEqual(result["coverage"]["missing_t"], [])


class OneShotImportTest(unittest.TestCase):
    """(f)：一份 Markdown、一次导入。"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = folder.name
        self.bib = Path(self.work, "in.bib")
        self.tex = Path(self.work, "in.tex")
        self.bib.write_text(entry("KA01", "One") + entry("KA02", "Two"),
                            encoding="utf-8")
        self.tex.write_text(r"See \cite{KA01} and \cite{KA02}.", encoding="utf-8")

    def prepare(self):
        return w.prepare(work_dir=self.work, entry=w.ENTRY_BIB,
                         bib_text=self.bib.read_text(),
                         tex_text=self.tex.read_text(),
                         target_path=str(self.bib), tex_path=str(self.tex),
                         taskbook_version=3)

    def test_f_one_markdown_one_import_yields_both_results(self):
        task = self.prepare()
        body = json_typeset(items=[{
            "item_id": "M-1", "action": "metadata_fill",
            "field": "year", "new": "2025", "evidence_ids": ["E1"],
        }])
        reply = bib_reply(task_id=task.task_id, key="KA01", title="One fixed",
                          json_body=body)
        # 一次调用 = 一次导入
        result = w.import_reply(work_dir=self.work, text=reply)
        self.assertTrue(result["ok"], result.get("notes"))
        self.assertIn("KA01", result["bib_text"])           # bib 结果
        self.assertEqual(result["typeset"]["status"], "completed")
        self.assertEqual(len(result["typeset"]["items"]), 1)  # 结构化结果
        # 落盘后仍是一份活动回复，不需要第二次导入
        state = w.load_state(self.work)
        active = state.get("active_reply")
        self.assertIsNotNone(active)
        self.assertIn("KA01", active["bib_text"])
        self.assertEqual(active["typeset"]["status"], "completed")
        self.assertTrue(w.preview(work_dir=self.work).ok)

    def test_f_bibtex_only_markdown_is_a_complete_import(self):
        task = self.prepare()
        reply = bib_reply(task_id=task.task_id, key="KA01", title="One fixed")
        result = w.import_reply(work_dir=self.work, text=reply)
        self.assertTrue(result["ok"], result.get("notes"))
        self.assertIn("One fixed", result["bib_text"])
        self.assertEqual(result["typeset"]["status"], "not_run")  # 未选择执行
        self.assertTrue(w.preview(work_dir=self.work).ok)

    def test_f_rejected_single_markdown_leaves_no_candidate(self):
        task = self.prepare()
        # 一份回件里两个冲突块：整份拒收，且不能从原文重扫出候选
        reply = (f"% TASK-ID: {task.task_id}\n"
                 "```bibtex\n" + entry("KA01", "A") + "```\n"
                 "```bibtex\n" + entry("KA01", "B") + "```\n")
        result = w.import_reply(work_dir=self.work, text=reply)
        self.assertFalse(result["ok"])
        self.assertIsNone(w.load_state(self.work).get("active_reply"))
        self.assertFalse(w.preview(work_dir=self.work).ok)
        self.assertTrue(any("多个冲突结果块" in note for note in result["notes"]))

    def test_f_missing_entries_are_named_after_one_import(self):
        task = self.prepare()
        # 只交回 KA01，KA02 是 T 内应核条目 → 一次导入成功但标出缺件
        reply = bib_reply(task_id=task.task_id, key="KA01", title="One fixed")
        result = w.import_reply(work_dir=self.work, text=reply)
        self.assertTrue(result["ok"], result.get("notes"))
        self.assertEqual(result["coverage"]["missing_t"], ["KA02"])
        self.assertTrue(any("KA02" in note for note in result["diagnostics"]))


class BranchHintTest(unittest.TestCase):
    """(g)：四种范围各一条分支提示。"""

    def test_g_delegated_subset(self):
        result = r.parse_reply(text=bib_reply(), context=ctx())
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["branch"], r.BRANCH_DELEGATED_SUBSET)
        self.assertIn("委派条目子集", result["branch_hint"])
        self.assertIn("preview", result["next_step"])

    def test_g_missing_entries(self):
        result = r.parse_reply(text=bib_reply(key="KA01"),
                               context=ctx(keys=("KA01", "KA02"), t=("KA01", "KA02")))
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["branch"], r.BRANCH_MISSING_ENTRIES)
        self.assertIn("没交回", result["branch_hint"])
        self.assertIn("KA02", result["branch_hint"])
        self.assertTrue(any("KA02" in note for note in result["diagnostics"]))

    def test_g_external_complete_bib(self):
        result = r.parse_reply(text="```bibtex\n" + entry() + "```\n",
                               context=ctx(), source_kind="complete_external")
        self.assertEqual(result["branch"], r.BRANCH_EXTERNAL_COMPLETE_BIB)
        self.assertIn("外部完整 Bib", result["branch_hint"])
        self.assertIn("replace_active", result["next_step"])

    def test_g_unverified_plain_bib(self):
        result = r.parse_reply(text="```bibtex\n" + entry() + "```\n", context=ctx())
        self.assertEqual(result["branch"], r.BRANCH_UNVERIFIED_PLAIN_BIB)
        self.assertIn("未验证", result["branch_hint"])
        self.assertIn("preview", result["next_step"])

    def test_g_report_only_is_flagged_alongside_the_range(self):
        body = json_typeset(items=[{
            "item_id": "M-2", "action": "report_only",
            "reason": "仅记录：作者单位与致谢不一致，无需改稿",
            "evidence_ids": ["E2"],
        }])
        result = r.parse_reply(text=bib_reply(json_body=body), context=ctx())
        self.assertTrue(result["ok"], result["errors"])
        self.assertIn(r.BRANCH_REPORT_ONLY, result["branches"])
        self.assertIn(r.BRANCH_DELEGATED_SUBSET, result["branches"])
        self.assertIn("报告式只读项", result["branch_hint"])

    def test_g_four_branches_are_mutually_distinct_strings(self):
        names = [r.BRANCH_DELEGATED_SUBSET, r.BRANCH_MISSING_ENTRIES,
                 r.BRANCH_EXTERNAL_COMPLETE_BIB, r.BRANCH_UNVERIFIED_PLAIN_BIB,
                 r.BRANCH_REPORT_ONLY, r.BRANCH_REJECTED]
        self.assertEqual(len(set(names)), len(names))
        # 常量本身必须导出，值不能重复（否则分支判断会串味）
        for constant in ("BRANCH_DELEGATED_SUBSET", "BRANCH_MISSING_ENTRIES",
                         "BRANCH_EXTERNAL_COMPLETE_BIB",
                         "BRANCH_UNVERIFIED_PLAIN_BIB", "BRANCH_REPORT_ONLY",
                         "BRANCH_REJECTED"):
            self.assertIn(constant, r.__all__)
            self.assertIsInstance(getattr(r, constant), str)


class ContractCompatibilityTest(unittest.TestCase):
    """追加键不改变既有八键的语义（state_v3 契约）。"""

    def test_eight_contract_keys_unchanged_on_success(self):
        result = r.parse_reply(text=bib_reply(), context=ctx())
        for key in ("ok", "identity_status", "source_kind", "bib_text",
                    "typeset", "text", "coverage", "rejected"):
            self.assertIn(key, result)
        self.assertEqual(result["source_kind"], "ai_subset")
        self.assertEqual(set(result["typeset"]), {"status", "items"})

    def test_rejected_result_keeps_contract_shape(self):
        result = r.parse_reply(text=bib_reply(task_id="AIW-NOPE"), context=ctx())
        self.assertEqual(result["bib_text"], "")
        self.assertEqual(result["typeset"]["status"], "not_run")
        self.assertEqual(result["text"]["status"], "not_run")
        self.assertEqual(result["coverage"], {"missing_t": [], "rejected": True})
        self.assertTrue(result["rejected"])


class LineNumberAccuracyTest(unittest.TestCase):
    """行号必须指向回件的真实行，不能是围栏体内的相对行号。

    独验 V-C 指出的缺陷：未验证身份 + 围栏内夹带叙述时，行号系统性少算了
    「开围栏之前的行数 + 1」，而且该路径干脆没有 ``line`` 字段。用户照着报错
    去改会改错行，所以四种组合（前导 0/2 行 × 围栏内叙述在首行/中行）都要钉住。
    """

    LEADING = "% 前言一行\n% 再来一行\n"   # k = 2 行前导

    def _reject(self, text):
        result = r.parse_reply(text=text, context=ctx())
        self.assertFalse(result["ok"], "夹带叙述必须整份拒收")
        self.assertEqual(result["errors"][0]["code"], "unverified_not_plain_bib")
        self.assertEqual(result["bib_text"], "")
        return result["errors"][0]

    def test_h_prose_on_first_fence_line_without_leading_lines(self):
        # 围栏在第 1 行 → 围栏内第 1 行 = 回件第 2 行
        error = self._reject("```bibtex\nHere is my answer:\n" + entry() + "```\n")
        self.assertEqual(error["line"], 2)
        self.assertIn("第 2 行", error["message"])
        self.assertIn("Here is my answer", error["message"])

    def test_h_prose_on_first_fence_line_with_two_leading_lines(self):
        # 围栏在第 3 行 → 围栏内第 1 行 = 回件第 4 行（V-C 的最小复现）
        error = self._reject(self.LEADING + "```bibtex\nHere is my answer:\n"
                             + entry() + "```\n")
        self.assertEqual(error["line"], 4)
        self.assertIn("第 4 行", error["message"])
        self.assertIn("Here is my answer", error["message"])

    def test_h_prose_in_middle_of_fence_without_leading_lines(self):
        # 围栏在第 1 行，叙述在围栏体第 2 行 → 回件第 3 行
        text = ("```bibtex\n@article{KA01, title={A}}\nOK so far\n"
                "@article{KA02, title={B}}\n```\n")
        error = self._reject(text)
        self.assertEqual(error["line"], 3)
        self.assertIn("第 3 行", error["message"])
        self.assertIn("OK so far", error["message"])

    def test_h_prose_in_middle_of_fence_with_two_leading_lines(self):
        # 围栏在第 3 行，叙述在围栏体第 2 行 → 回件第 5 行
        text = (self.LEADING + "```bibtex\n@article{KA01, title={A}}\nOK so far\n"
                "@article{KA02, title={B}}\n```\n")
        error = self._reject(text)
        self.assertEqual(error["line"], 5)
        self.assertIn("第 5 行", error["message"])
        self.assertIn("OK so far", error["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
