#!/usr/bin/env python3.14
"""R3 精简回复解析独立期望测试（2026-11-18，Task 3）。

被测（尚不存在，本文件先写钉死契约）：
  Engine/slcn/ai_workflow_reply.py
    parse_reply(*, text: str, context: dict, source_kind: str = 'ai_subset') -> dict

返回 dict 字段：
  ok, identity_status, source_kind, bib_text, typeset, text, coverage, rejected

全部匿名夹具（Kxx / TXT-Ux / FNx），不读真实稿件、不联网、不调模型。
契约 context 形状与 taskbook 模块 v3 完全一致（见 test_ai_workflow_taskbook）。

覆盖规格 §7 关键语义：
  正例（未核实诚实保留）、错 TASK-ID 整份拒收、冲突多 ID 拒收、坏 schema 整份拒收、
  叙述/JSON 字符串里的 @book 示例不算数据、多个冲突块拒收、F/U 免交不算漏交、
  T 漏交标记、未知键隔离、report_only 免 old/new、类型/未交/partial/not_run 分离。

用法：
  cd Engine && PYTHONPATH=.:vendor /opt/homebrew/bin/python3.14 \
      tests/test_ai_workflow_reply_20261118.py
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn.ai_workflow_reply import parse_reply  # noqa: E402

TID = "AIW-R3TEST00042"


def _ctx(*, keys=("K18",), t=("K18",), safe=(), o=None, m=(), f=(), u=(),
         body_units=(), typeset_selected=True, text_selected=True,
         fn_items=(), meta_items=()):
    o = list(o if o is not None else keys)
    return {
        "version": 3,
        "task_id": TID,
        "sets": {
            "O": o, "T": list(t), "F": list(f), "U": list(u),
            "M": list(m), "R": o, "C": [], "A": o,
        },
        "items": [{"item_id": "i" + k, "key": k, "etype": "article", "raw": ""} for k in o],
        "body_units": list(body_units),
        "execution": {"bib_verify": True, "typeset": typeset_selected, "text": text_selected},
        "fn_items": list(fn_items),
        "meta_items": list(meta_items),
    }


def _bib_reply(key="K18", body=None):
    if body is None:
        body = (f"@article{{{key}, author={{Example, A.}}, title={{An Example Study}},\n"
                f" journal={{Example Journal}}, year={{2020}}, pages={{1--9}}}}")
    return f"% TASK-ID: {TID}\n```bibtex\n% {key}：已核对来源。\n{body}\n```\n"


def _json_typeset(items=None, status="completed"):
    import json
    obj = {"schema": "slcn.ai_result/3",
           "typeset": {"status": status, "items": items or []}}
    return "```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```\n"


def _json_both(t_items=None, x_items=None, t_status="completed", x_status="completed"):
    import json
    obj = {"schema": "slcn.ai_result/3",
           "typeset": {"status": t_status, "items": t_items or []},
           "text": {"status": x_status, "items": x_items or []}}
    return "```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```\n"


class IdentityTest(unittest.TestCase):
    """身份与协议边界（整份拒收，不许降级重扫）。"""

    def test_spec_positive_example(self):
        text = _bib_reply() + _json_both(
            x_items=[{"item_id": "TXT-U1-01", "unit_id": "U1", "old": "teh", "new": "the",
                      "before": "This is ", "after": " example.", "kind": "typo",
                      "reason": "明确拼写错误", "certainty": "clear"}]
        )
        r = parse_reply(text=text, context=_ctx(body_units=[{"unit_id": "U1", "text": "This is teh example."}]))
        self.assertTrue(r["ok"], f"正例应通过：{r['rejected']}")
        self.assertEqual(r["identity_status"], "verified")
        self.assertEqual(r["source_kind"], "ai_subset")
        self.assertIn("@article{K18", r["bib_text"])
        self.assertEqual(len(r["text"]["items"]), 1)

    def test_wrong_task_id_rejects_entire_reply(self):
        text = "% TASK-ID: OTHER\n```bibtex\n@article{K18, title={X}}\n```\n"
        r = parse_reply(text=text, context=_ctx())
        self.assertFalse(r["ok"], "错 TASK-ID 必须整份拒收")
        self.assertEqual(r["identity_status"], "mismatch")
        self.assertEqual(r["bib_text"].strip(), "", "拒收后不得留下可扫的 Bib 文本")

    def test_conflicting_ids_reject(self):
        text = ("% TASK-ID: " + TID + "\n```bibtex\n@book{K18, title={A}}\n```\n"
                "% TASK-ID: OTHER\n```bibtex\n@book{K19, title={B}}\n```\n")
        r = parse_reply(text=text, context=_ctx())
        self.assertFalse(r["ok"], "多个不一致 ID 必须拒收")

    def test_broken_schema_rejects_entire_reply(self):
        text = _bib_reply() + "```json\n{\"schema\":\"slcn.ai_result/9\",\"typeset\":{}\n```\n"
        r = parse_reply(text=text, context=_ctx())
        self.assertFalse(r["ok"], "声称新协议但 JSON 破损必须整份拒收，不得降为裸 Bib")

    def test_bib_example_in_prose_is_not_data(self):
        text = ("% TASK-ID: " + TID + "\n"
                "叙述里举例：比如 @book{EX, title={Not real}} 只是说明。\n"
                "```bibtex\n@article{K18, title={Real}}\n```\n"
                "```json\n{\"schema\":\"slcn.ai_result/3\",\"note\":\"@book{FAKE,title={x}}\","
                "\"typeset\":{\"status\":\"completed\",\"items\":[]}}\n```\n")
        r = parse_reply(text=text, context=_ctx())
        self.assertTrue(r["ok"])
        self.assertIn("K18", r["bib_text"])
        self.assertNotIn("EX", r["bib_text"], "叙述里的 Bib 示例不得入数据")
        self.assertNotIn("FAKE", r["bib_text"], "JSON 字符串里的 Bib 示例不得入数据")

    def test_multiple_conflicting_blocks_reject(self):
        text = ("% TASK-ID: " + TID + "\n```bibtex\n@article{K18, title={A}}\n```\n"
                "```bibtex\n@article{K18, title={B}}\n```\n")
        r = parse_reply(text=text, context=_ctx())
        self.assertFalse(r["ok"], "多个冲突 Bib 结果块必须拒收")

    def test_no_identity_plain_bib_is_unverified_subset(self):
        r = parse_reply(text="```bibtex\n@article{K18, title={X}}\n```\n", context=_ctx())
        self.assertTrue(r["ok"])
        self.assertEqual(r["identity_status"], "unverified")
        self.assertEqual(r["source_kind"], "ai_subset")

    def test_verified_identity_always_means_ai_subset(self):
        text = "% TASK-ID: " + TID + "\n```bibtex\n@article{K18, title={X}}\n```\n"
        r = parse_reply(text=text, context=_ctx(), source_kind="complete_external")
        self.assertEqual(r["source_kind"], "ai_subset")
        self.assertTrue(r["ok"])


class CoverageTest(unittest.TestCase):
    """覆盖与不可绕过活动结果语义。"""

    def test_t_missing_marked_not_f_incompleted(self):
        """T 漏交：coverage 标记未完成；F/U 正常免交不算漏交。"""
        ctx = _ctx(t=["K18", "K19"], o=["K18", "K19", "K01", "K02"],
                   f=["K01"], u=["K02"])
        r = parse_reply(text=_bib_reply("K18") + _bib_reply("K19").split("\n", 1)[1],
                        context=ctx)
        # 只交一条：K19 漏交
        r2 = parse_reply(text=_bib_reply("K18"), context=ctx)
        cov = r2["coverage"]
        joined = " ".join(str(v) for v in cov.get("missing_t", []))
        self.assertIn("K19", joined, "T 漏交的 K19 必须标出")
        self.assertNotIn("K01", joined, "F 正常免交不算漏交")
        self.assertNotIn("K02", joined, "U 正常免交不算漏交")

    def test_unchanged_t_still_submitted_full(self):
        ctx = _ctx(t=["K18"], o=["K18"])
        body = ("@article{K18, author={Example, A.}, title={An Example Study},\n"
                " journal={Example Journal}, year={2020}}")
        r = parse_reply(text=_bib_reply("K18", body), context=ctx)
        self.assertTrue(r["ok"])
        self.assertIn("year={2020}", r["bib_text"], "未改的 T 也必须完整交回")

    def test_not_run_category_not_false_complete(self):
        """缺正文时 text 类别 NOT_RUN，不能报 completed。"""
        ctx = _ctx(body_units=(), text_selected=False)
        r = parse_reply(text=_bib_reply(), context=ctx)
        self.assertEqual(r["text"]["status"], "not_run")

    def test_partial_and_not_run_distinct(self):
        import json
        obj = {"schema": "slcn.ai_result/3",
               "typeset": {"status": "partial", "reason": "仅局部证据", "items": []},
               "text": {"status": "not_run", "reason": "无正文"}}
        text = _bib_reply() + "```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```\n"
        r = parse_reply(text=text, context=_ctx())
        self.assertTrue(r["ok"])
        self.assertEqual(r["typeset"]["status"], "partial")
        self.assertEqual(r["text"]["status"], "not_run")
        self.assertNotEqual(r["typeset"]["status"], r["text"]["status"])

    def test_report_only_needs_no_old_new(self):
        t_items = [{"item_id": "RPT-1", "action": "report_only",
                    "reason": "结构疑点需人工", "evidence_ids": ["U1"]}]
        r = parse_reply(text=_bib_reply() + _json_typeset(t_items), context=_ctx())
        self.assertTrue(r["ok"], f"report_only 不应强制 old/new：{r['rejected']}")
        self.assertEqual(len(r["typeset"]["items"]), 1)

    def test_unselected_category_items_ignored(self):
        """未选 text 类别：即使回复给了 text 建议也不采纳。"""
        x = [{"item_id": "TXT-U1-01", "unit_id": "U1", "old": "teh", "new": "the",
              "before": "", "after": "", "kind": "typo", "reason": "x", "certainty": "clear"}]
        ctx = _ctx(text_selected=False)
        r = parse_reply(text=_bib_reply() + _json_both(x_items=x), context=ctx)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["text"]["items"]), 0, "未选类别的建议不得进入活动结果")

    def test_rejected_entries_isolated_not_applied(self):
        """不合法建议逐项进 rejected，不影响 ok。"""
        bad = [{"item_id": "F-1", "action": "delete_everything", "reason": "x",
                "evidence_ids": []}]
        r = parse_reply(text=_bib_reply() + _json_typeset(bad), context=_ctx())
        self.assertTrue(r["ok"])
        self.assertTrue(any("F-1" in str(x) for x in r["rejected"]),
                        "非法动作应隔离到 rejected，不静默吞掉")

    def test_declared_schema_but_broken_json_rejects(self):
        text = (_bib_reply() + "```json\n{\"schema\":\"slcn.ai_result/3\","
                "\"typeset\":{\"status\":\"completed\",\n```\n")
        r = parse_reply(text=text, context=_ctx())
        self.assertFalse(r["ok"], "坏 JSON 必须整份拒收")


class ReadTaskbookTest(unittest.TestCase):
    """合流 §10：read_taskbook 只认 state 登记的 taskbook + 验 SHA，失败无正文降级。"""

    def setUp(self):
        import json
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(prefix="r3-readtb-")
        self.work = os.path.join(self.tmp.name, "work")
        os.makedirs(self.work, exist_ok=True)
        self.json = json
        self.tb_text = "%% TASK-ID: %s\n任务书正文\n" % TID
        self.tb_path = os.path.join(self.work, "冻结任务书.md")
        with open(self.tb_path, "w", encoding="utf-8") as f:
            f.write(self.tb_text)
        import hashlib
        self.tb_sha = hashlib.sha256(self.tb_text.encode("utf-8")).hexdigest()

    def tearDown(self):
        self.tmp.cleanup()

    def _write_state(self, schema_version="slcn.ai_workflow/3", task_id=TID,
                     path=None, sha=None):
        """R3 state 与正常工作流使用同一位置。"""
        st = {
            "schema_version": schema_version,
            "task_id": task_id,
            "taskbook": {"path": path or self.tb_path, "sha256": sha or self.tb_sha},
        }
        from slcn import ai_workflow as W
        if schema_version.endswith("/3"):
            st["taskbook_version"] = 3
        W.save_state(self.work, st)

    def test_read_taskbook_ok_returns_seven_keys(self):
        from slcn import ai_workflow as W
        self._write_state()
        r = W.read_taskbook(work_dir=self.work, task_id=TID)
        self.assertTrue(r.get("ok"), f"应读到登记的任务书：{r}")
        for k in ("ok", "task_id", "template_version", "path", "sha256", "text", "chars"):
            self.assertIn(k, r, "§10 七键缺 %s" % k)
        self.assertEqual(r["text"], self.tb_text, "全文必须等于冻结任务书原文")
        self.assertEqual(r["sha256"], self.tb_sha)

    def test_read_taskbook_sha_mismatch_drops_body(self):
        from slcn import ai_workflow as W
        self._write_state(sha="deadbeef")
        r = W.read_taskbook(work_dir=self.work, task_id=TID)
        self.assertFalse(r.get("ok"), "SHA 不符必须失败")
        self.assertEqual(r.get("text", ""), "", "失败不得回传正文")

    def test_read_taskbook_task_id_mismatch_drops_body(self):
        from slcn import ai_workflow as W
        self._write_state(task_id="AIW-OTHER")
        r = W.read_taskbook(work_dir=self.work, task_id=TID)
        self.assertFalse(r.get("ok"), "task_id 不符必须失败")
        self.assertEqual(r.get("text", ""), "")


class RequestRetryTest(unittest.TestCase):
    """合流 §9：单请求显式 retry；成功不重发、进行中拒绝、失败/结果不明仅显式重试。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(prefix="r3-retry-")
        self.work = os.path.join(self.tmp.name, "work")
        os.makedirs(self.work, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _prepare_real(self):
        from slcn import ai_workflow as W
        return W.prepare(work_dir=self.work, entry="bib",
                         bib_text="@article{K, title={T}, author={A, B}, year={2020}}",
                         scope=W.Scope.defaults())

    def test_success_not_resent_without_retry(self):
        from slcn import ai_workflow as W
        self._prepare_real()
        reply = "% TASK-ID: X\n```bibtex\n@article{K, title={T2}}\n```\n"
        c1 = W.FakeClient(reply)
        r1 = W.request(work_dir=self.work, taskbook_text="tb", client=c1)
        self.assertTrue(r1.get("ok"))
        c2 = W.FakeClient(reply)
        r2 = W.request(work_dir=self.work, taskbook_text="tb", client=c2)
        self.assertFalse(r2.get("ok"), "成功请求默认不得自动重发")
        self.assertEqual(len(c2.calls), 0, "未发第二次请求")
        self.assertIn("retry", r2.get("error", "").lower())

    def test_success_not_resent_even_with_retry(self):
        from slcn import ai_workflow as W
        self._prepare_real()
        reply = "% TASK-ID: X\n```bibtex\n@article{K, title={T2}}\n```\n"
        c1 = W.FakeClient(reply)
        W.request(work_dir=self.work, taskbook_text="tb", client=c1)
        c2 = W.FakeClient(reply)
        r2 = W.request(work_dir=self.work, taskbook_text="tb", client=c2, retry=True)
        self.assertFalse(r2.get("ok"), "成功后不重发：即使显式 retry 也拒绝（§9）")
        self.assertEqual(len(c2.calls), 0, "成功不得发第二次请求")

    def test_failed_then_explicit_retry(self):
        from slcn import ai_workflow as W
        self._prepare_real()
        reply = "% TASK-ID: X\n```bibtex\n@article{K, title={T2}}\n```\n"
        c1 = W.FakeClient(reply, fail_times=1)
        r1 = W.request(work_dir=self.work, taskbook_text="tb", client=c1)
        self.assertFalse(r1.get("ok"))
        c2 = W.FakeClient(reply)
        r2 = W.request(work_dir=self.work, taskbook_text="tb", client=c2)
        self.assertFalse(r2.get("ok"), "失败结果不明，未显式 retry 不自动重发")
        self.assertEqual(len(c2.calls), 0)
        c3 = W.FakeClient(reply)
        r3 = W.request(work_dir=self.work, taskbook_text="tb", client=c3, retry=True)
        self.assertTrue(r3.get("ok"), "显式 retry 可重发")

    def test_in_flight_refused_without_retry(self):
        from slcn import ai_workflow as W
        self._prepare_real()
        st = W.load_state(self.work)
        st["phase"] = W.PH_REQUESTING
        W.save_state(self.work, st)
        c = W.FakeClient("anything")
        r = W.request(work_dir=self.work, taskbook_text="tb", client=c)
        self.assertFalse(r.get("ok"), "进行中请求必须互斥拒绝")
        self.assertEqual(len(c.calls), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
