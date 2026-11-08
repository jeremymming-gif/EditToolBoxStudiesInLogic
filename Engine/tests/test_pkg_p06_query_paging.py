#!/usr/bin/env python3
r"""P06「引用库查询分页与复用」引擎侧判据（2.6-E）。

覆盖 CONTRACT §6 的 1)–9)（合成库，tmp_path；**不读真实库/真实稿件**）：

  1) 省略 `--offset` 的 `--json` 键集与基线逐键一致（query / query-advanced），
     且人读输出保持基线文案；
  2) 显式 offset 0 / 1 / 199 / 200 / 末页 / 越界 / 超大 offset 的 `page` 六字段；
  3) 401 条合成库按 200 翻页串联 == 独立全量（逐 key 顺序）；
  4) 稳定序（同分/同年/同姓时按 key；relevance / year / author 三口径）；
  5) 负 offset：rc2 + 契约 stderr + 库文件 SHA 前后一致（零写，且校验在开库之前）；
  6) key/DOI + 非零 offset：rc2 + 契约 stderr；+0 与省略逐字节一致（无 page）；
  7) advanced `truncated` 旧口径（省略时 `total > count`）与分页口径
     （`total > offset + count`；offset==0 时两者等价）；
  8) `page.db_sha256` == 库文件字节 SHA-256（小写 hex）；
  9) 每页 ≤ 200、`next_offset` 链能走完、无重/无漏。

口径来源：CONTRACT §2/§3/§4；`slcn/bibdb_query_page.py` 是 page 字段的唯一构造点。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import pathlib
import subprocess
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_ENGINE = _HERE.parent
for _p in (str(_ENGINE), str(_ENGINE / "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from slcn import bibdb  # noqa: E402
from slcn import bibdb_query_page as qpage  # noqa: E402

pass_n = 0
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global pass_n
    if ok:
        pass_n += 1
        print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"FAIL  {name}  {detail}")


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def file_sha(path: str) -> str:
    return hashlib.sha256(read_bytes(path)).hexdigest()


CLI = str(_ENGINE / "bibdb.py")
CLI_ENV = dict(os.environ, PYTHONPATH=str(_ENGINE) + os.pathsep
               + str(_ENGINE / "vendor"), PYTHONIOENCODING="utf-8")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(_ENGINE),
                          env=CLI_ENV)


def cli_json(*args: str) -> dict:
    result = run_cli(*args)
    if result.returncode != 0:
        raise AssertionError(f"rc={result.returncode} stderr={result.stderr!r}")
    return json.loads(result.stdout)


TITLE = "Shared Query Title"
TOTAL = 401
T = bibdb.SearchTerm


def build_library(tmp: str) -> str:
    """401 条合成库：同题名/同作者/同年/同分数（volume 唯一保证内容不合并）。

    这样标题检索与高级检索的排序在 `key` 之前全部打平 → 稳定序 = key 升序，
    既覆盖 tie-break，也让「翻页串联 == 独立全量」有可独立计算的期望序列。
    """
    path = os.path.join(tmp, "p06-paging.jsonl")
    store = bibdb.Store.load(path, create=True)
    entries = []
    for index in range(TOTAL):
        entries.append(
            f"@article{{K{index:04d},\n"
            f"  author  = {{Smith, John}},\n"
            f"  title   = {{{TITLE}}},\n"
            f"  journal = {{Journal of Paging}},\n"
            f"  year    = {{2020}},\n"
            f"  volume  = {{{index}}}\n"
            "}")
    store.import_bib_text("\n\n".join(entries),
                          {"kind": bibdb.KIND_PUBLISHED,
                           "issue": "2026年第1期(1)", "slug": "00 synthetic"},
                          verified=True)
    store.save()
    return path


def check_page(name: str, page: dict | None, *, offset: int, limit: int,
               total_matched: int, count: int) -> None:
    """断言 `page` 恰好六键且语义符合 CONTRACT §2。"""
    has_more = offset + count < total_matched
    expected_next = offset + count if has_more else None
    ok = (
        isinstance(page, dict)
        and set(page) == set(qpage.PAGE_KEYS)
        and page.get("offset") == offset
        and page.get("limit") == limit
        and page.get("total_matched") == total_matched
        and page.get("count", count) is None  # page 里没有 count 键（见下）
    )
    # `count` 不属于 page 六键：本页条数由 hits 长度表达。
    ok = (
        isinstance(page, dict)
        and set(page) == set(qpage.PAGE_KEYS)
        and page.get("offset") == offset
        and page.get("limit") == limit
        and page.get("total_matched") == total_matched
        and page.get("next_offset") == expected_next
        and page.get("has_more") is has_more
    )
    check(name, ok, f"{page!r}" if not ok else "")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="p06_paging_") as tmp:
        db = build_library(tmp)
        before_bytes = read_bytes(db)
        before_sha = hashlib.sha256(before_bytes).hexdigest()
        store = bibdb.Store.load(db)
        records = sorted(store.records(), key=lambda record: record["key"])
        all_keys = [record["key"] for record in records]
        check("合成库 401 条、key 连续（K0000…K0400）",
              len(bytes_key := all_keys) == TOTAL
              and bytes_key[0] == "K0000" and bytes_key[-1] == "K0400",
              f"{len(all_keys)}")

        # ── 0) page 纯模块：PAGE_KEYS / offset_error / build_page ──────────────
        print("\n── 0) `slcn/bibdb_query_page.py` 纯函数 ──")
        check("PAGE_KEYS 恰为六键且顺序固定",
              qpage.PAGE_KEYS == ("offset", "limit", "total_matched", "next_offset",
                                  "has_more", "db_sha256"))
        check("offset_error：0/5 合法", qpage.offset_error(0) is None
              and qpage.offset_error(5) is None)
        check("offset_error：负数文案逐字",
              qpage.offset_error(-1) == "offset 不能为负：-1"
              and qpage.offset_error(-0) is None)
        check("offset_error：bool/float/str/None → 「offset 必须是整数」",
              qpage.offset_error(True) == "offset 必须是整数"
              and qpage.offset_error(False) == "offset 必须是整数"
              and qpage.offset_error(1.5) == "offset 必须是整数"
              and qpage.offset_error("3") == "offset 必须是整数"
              and qpage.offset_error(None) == "offset 必须是整数")
        page = qpage.build_page(offset=0, limit=200, total_matched=401, count=200,
                                db_sha256="ab" * 32)
        check("build_page：键顺序 = PAGE_KEYS、has_more/next_offset 正确",
              tuple(page) == qpage.PAGE_KEYS and page["has_more"] is True
              and page["next_offset"] == 200 and page["offset"] == 0)
        page = qpage.build_page(offset=400, limit=200, total_matched=401, count=1,
                                db_sha256="ab" * 32)
        check("build_page：末页 has_more=False、next_offset=None",
              page["has_more"] is False and page["next_offset"] is None)
        page = qpage.build_page(offset=401, limit=200, total_matched=401, count=0,
                                db_sha256="ab" * 32)
        check("build_page：越界（offset==total）也 has_more=False",
              page["has_more"] is False and page["next_offset"] is None)
        page = qpage.build_page(offset=199, limit=200, total_matched=399, count=200,
                                db_sha256="ab" * 32)
        check("build_page：offset+count==total 边界 → next_offset=None",
              page["has_more"] is False and page["next_offset"] is None)

        # ── 1) 省略 offset：JSON 键集与基线一致 / 文本文案不变 ────────────────
        print("\n── 1) 省略 `--offset`：与基线逐键/逐字一致 ──")
        payload = cli_json("query", "--title", TITLE, "--limit", "3", "--json",
                           "--db", db)
        check("query --title 省略 offset：键集 = 基线 7 键、无 page/offset/total_matched",
              set(payload) == {"db", "kind", "query", "hits", "count", "high",
                               "ambiguous"}
              and payload["kind"] == "title" and payload["count"] == 3,
              str(sorted(payload)))
        payload = cli_json("query", "--key", "K0000", "--json", "--db", db)
        check("query --key 省略 offset：键集 = 基线 6 键、无 page",
              set(payload) == {"db", "kind", "query", "hits", "count", "ambiguous"}
              and payload["kind"] == "key" and payload["count"] == 1)
        payload = cli_json("query", "--doi", "10.1000/none", "--json", "--db", db)
        check("query --doi 省略 offset：键集 = 基线 6 键、无 page",
              set(payload) == {"db", "kind", "query", "hits", "count", "ambiguous"}
              and payload["kind"] == "doi")
        payload = cli_json("query-advanced", "--title", "shared", "--limit", "3",
                           "--json", "--db", db)
        check("query-advanced 省略 offset：键集 = 基线 7 键、无 page/offset",
              set(payload) == {"db", "kind", "query", "hits", "count",
                               "total_matched", "truncated"}
              and payload["kind"] == "advanced" and payload["count"] == 3
              and payload["total_matched"] == TOTAL and payload["truncated"] is True,
              str(sorted(payload)))
        result = store.query_title(TITLE)
        check("引擎 query_title（无 offset 参数）：返回键 = 旧 5 键",
              set(result) == {"query", "hits", "count", "high", "ambiguous"})
        title_sig = inspect.signature(bibdb.Store.query_title)
        check("(a) inspect.signature(query_title) 仍为旧 7 参数（签名零变化）",
              tuple(title_sig.parameters) == ("self", "title", "author", "year",
                                              "limit", "threshold", "min_score"),
              str(tuple(title_sig.parameters)))
        title_defaults = {name: param.default
                          for name, param in title_sig.parameters.items()}
        check("(a) query_title 默认值零变化（author=''/year=''/limit=10/threshold=0.90/min_score=0.60）",
              title_defaults["author"] == "" and title_defaults["year"] == ""
              and title_defaults["limit"] == 10
              and title_defaults["threshold"] == bibdb.FUZZY_TITLE_THRESHOLD
              and title_defaults["min_score"] == bibdb.FUZZY_MIN_SCORE,
              str(title_defaults))
        page_sig = inspect.signature(bibdb.Store.query_title_page)
        check("(b) query_title_page 签名 = 裁决 C（offset 0 / limit 10 默认值）",
              tuple(page_sig.parameters) == ("self", "title", "author", "year",
                                             "offset", "limit", "threshold",
                                             "min_score")
              and page_sig.parameters["offset"].default == 0
              and page_sig.parameters["limit"].default == 10
              and page_sig.parameters["threshold"].default == bibdb.FUZZY_TITLE_THRESHOLD
              and page_sig.parameters["min_score"].default == bibdb.FUZZY_MIN_SCORE,
              str(tuple(page_sig.parameters)))
        result = store.query_advanced(bibdb.SearchSpec((T("title", "shared"),),
                                                       limit=3))
        check("引擎 query_advanced(offset 默认 0)：返回键 = 旧 5 键（无 offset）",
              set(result) == {"query", "hits", "count", "total_matched", "truncated"}
              and "offset" not in result)

        text = run_cli("query", "--title", TITLE, "--limit", "3", "--db", db)
        lines = text.stdout.splitlines()
        check("query --title 省略 offset：文本与基线逐字一致",
              text.returncode == 0 and lines[0] == f"库：{db}"
              and lines[1] == "title 查询命中 3 条（高置信 3）"
                           "（⚠️ 多条同 key/alias，需人工确认）",
              "\n".join(lines[:2]))
        text = run_cli("query", "--key", "K0000", "--db", db)
        lines = text.stdout.splitlines()
        check("query --key 省略 offset：文本首行/命中行与基线逐字一致",
              text.returncode == 0 and lines[0] == f"库：{db}"
              and lines[1] == "key 查询命中 1 条",
              "\n".join(lines[:2]))
        text = run_cli("query-advanced", "--title", "shared", "--limit", "3",
                       "--db", db)
        lines = text.stdout.splitlines()
        check("query-advanced 省略 offset：文本保留旧「已截断」括注",
              text.returncode == 0 and lines[0] == f"库：{db}"
              and lines[1] == f"高级检索命中 3 条（共 {TOTAL} 条，已截断）",
              "\n".join(lines[:2]))

        # ── 2) 显式 offset：page 六字段 ────────────────────────────────────────
        print("\n── 2) 显式 offset：page 六字段（0/1/199/200/末页/越界/超大）──")
        cases = [
            # (offset, limit, expected_count)
            (0, 200, 200),
            (1, 200, 200),
            (199, 200, 200),
            (200, 200, 200),
            (400, 200, 1),
            (401, 200, 0),
            (999999, 200, 0),
            (0, 10, 10),
        ]
        for offset, limit, expected_count in cases:
            payload = cli_json("query", "--title", TITLE, "--limit", str(limit),
                               "--offset", str(offset), "--json", "--db", db)
            page = payload.get("page")
            check(f"title offset={offset} limit={limit}：page 六字段 + 顶层精确键集",
                  set(page) == set(qpage.PAGE_KEYS)
                  and set(payload) == {"db", "kind", "query", "hits", "count",
                                       "high", "ambiguous", "page"}
                  and "offset" not in payload and "total_matched" not in payload
                  and payload["count"] == expected_count
                  and page["total_matched"] == TOTAL
                  and page["offset"] == offset,
                  f"{page!r} / {sorted(payload)}")
            check_page(f"title offset={offset} limit={limit}：page 语义",
                       page, offset=offset, limit=limit, total_matched=TOTAL,
                       count=expected_count)
        payload = cli_json("query", "--title", TITLE, "--offset", "0", "--json",
                           "--db", db)
        check("title 省略 --limit 时 page.limit = 默认 10",
              payload["page"]["limit"] == 10 and payload["count"] == 10)
        payload = cli_json("query-advanced", "--title", "shared", "--limit", "200",
                           "--offset", "200", "--json", "--db", db)
        check_page("advanced offset=200 limit=200：page 语义",
                   payload.get("page"), offset=200, limit=200,
                   total_matched=TOTAL, count=200)
        check("advanced 显式 offset>0：顶层精确 = 旧 7 键 + page，无引擎 `offset` 键",
              set(payload) == {"db", "kind", "query", "hits", "count",
                               "total_matched", "truncated", "page"}
              and "offset" not in payload
              and payload["total_matched"] == TOTAL
              and payload["truncated"] is True
              and payload["page"]["offset"] == 200,
              str(sorted(payload)))
        payload = cli_json("query-advanced", "--title", "shared", "--offset", "0",
                           "--json", "--db", db)
        check("advanced 显式 offset=0：page.limit = 默认 20、顶层精确 = 旧 7 键 + page",
              set(payload) == {"db", "kind", "query", "hits", "count",
                               "total_matched", "truncated", "page"}
              and "offset" not in payload
              and payload["page"]["limit"] == 20
              and payload["page"]["offset"] == 0,
              str(sorted(payload)))

        # ── 3) 401 条按 200 翻页串联 == 独立全量 ──────────────────────────────
        print("\n── 3) 401 条按 200 翻页串联 == 独立全量（逐 key）──")
        full_title = [hit["key"] for hit in store.query_title(TITLE, limit=10000)["hits"]]
        check("独立全量（query_title limit=10000，非分页路径）401 条",
              len(full_title) == TOTAL and full_title == sorted(all_keys))
        chained: list[str] = []
        offset = 0
        guard = 0
        while True:
            guard += 1
            result = store.query_title_page(TITLE, limit=200, offset=offset)
            chained.extend(hit["key"] for hit in result["hits"])
            page = qpage.build_page(offset=offset, limit=200,
                                    total_matched=result["total_matched"],
                                    count=result["count"], db_sha256="x")
            if not page["has_more"]:
                break
            offset = page["next_offset"]
            if guard > 10:
                break
        check("title 翻页串联（offset 0→200→400）== 独立全量（逐 key 顺序）",
              chained == full_title, f"{len(chained)}")
        chained_adv: list[str] = []
        offset = 0
        for _ in range(10):
            result = store.query_advanced(bibdb.SearchSpec(
                (T("title", "shared"),), sort="relevance", limit=200, offset=offset))
            chained_adv.extend(hit["key"] for hit in result["hits"])
            page = qpage.build_page(offset=offset, limit=200,
                                    total_matched=result["total_matched"],
                                    count=result["count"], db_sha256="x")
            if not page["has_more"]:
                break
            offset = page["next_offset"]
        check("advanced 翻页串联 == 独立全量（同分同年同姓 → key 升序）",
              chained_adv == full_title, f"{len(chained_adv)}")

        # ── 4) 稳定序：同分/同年/同姓时按 key（relevance / year / author）──
        print("\n── 4) 稳定序（tie-break 到 key）──")
        check("合成库同分同年同姓（构造前提）",
              len({(r["fields"].get("author"), r["fields"].get("year")) for r in records}) == 1)
        for sort in bibdb.SEARCH_SORTS:
            result = store.query_advanced(bibdb.SearchSpec(
                (T("title", "shared"),), sort=sort, limit=200, offset=0))
            keys = [hit["key"] for hit in result["hits"]]
            check(f"advanced sort={sort}：offset=0 的 200 条 == 全量升序前 200（key tie-break）",
                  keys == sorted(all_keys)[:200], f"{keys[:3]}…")
            result = store.query_advanced(bibdb.SearchSpec(
                (T("title", "shared"),), sort=sort, limit=200, offset=200))
            keys = [hit["key"] for hit in result["hits"]]
            check(f"advanced sort={sort}：offset=200 的 200 条 == 全量升序 200..400",
                  keys == sorted(all_keys)[200:400], f"{keys[:3]}…")
        result = store.query_title_page(TITLE, limit=200, offset=400)
        check("title：末页 1 条 = 全量升序最后 1 条（稳定、不漂移）",
              [hit["key"] for hit in result["hits"]] == sorted(all_keys)[400:])

        # ── 5) 负 offset：rc2 + 契约 stderr + 零写（且校验先于开库）────────────
        print("\n── 5) 负 offset：rc2 + stderr + 零写 ──")
        for cmd in ("query", "query-advanced"):
            missing = os.path.join(tmp, f"missing-{cmd}.jsonl")
            extra = ["--title", "x"] if cmd == "query" else ["--title", "x"]
            result = run_cli(cmd, *extra, "--offset", "-1", "--db", missing)
            check(f"{cmd} --offset -1：rc2、stderr 文案逐字、缺库文件未被创建（校验先于开库）",
                  result.returncode == 2
                  and result.stderr.strip() == "✗ offset 不能为负：-1"
                  and not os.path.exists(missing),
                  f"rc={result.returncode} stderr={result.stderr!r}")
        for bad in ("-2", "-999999"):
            result = run_cli("query", "--title", "x", "--offset", bad, "--db", db)
            check(f"query --offset {bad}：rc2、stdout 空、stderr 逐字",
                  result.returncode == 2 and result.stdout == ""
                  and result.stderr.strip() == f"✗ offset 不能为负：{bad}",
                  f"rc={result.returncode} stderr={result.stderr!r}")
        check("负 offset 前后库文件字节 SHA 不变（零写）",
              file_sha(db) == before_sha and read_bytes(db) == before_bytes)
        result = run_cli("query", "--title", "x", "--offset", "abc", "--db", db)
        check("--offset 非整数由 argparse 拒绝（rc2）", result.returncode == 2
              and "invalid int value" in result.stderr, result.stderr.strip()[:80])

        # ── 6) key/DOI + offset：非零 rc2；+0 等同省略 ─────────────────────────
        print("\n── 6) key/DOI + offset：非零 rc2；0 等同省略 ──")
        for selector in (("--key", "K0000"), ("--doi", "10.1000/none")):
            result = run_cli("query", *selector, "--offset", "5", "--db", db)
            check(f"query {selector[0]} + 非零 offset：rc2、stderr 逐字（在开库前）",
                  result.returncode == 2 and result.stdout == ""
                  and result.stderr.strip()
                  == "✗ --offset 仅用于列表检索（题名/高级）；key/DOI 定位为单条查询",
                  f"rc={result.returncode} stderr={result.stderr!r}")
            omitted = run_cli("query", *selector, "--json", "--db", db)
            explicit_zero = run_cli("query", *selector, "--offset", "0", "--json",
                                    "--db", db)
            check(f"query {selector[0]} + --offset 0：stdout 与省略逐字节一致（无 page）",
                  omitted.returncode == 0 and explicit_zero.returncode == 0
                  and omitted.stdout == explicit_zero.stdout
                  and '"page"' not in explicit_zero.stdout,
                  explicit_zero.stdout[:120])
            omitted = run_cli("query", *selector, "--db", db)
            explicit_zero = run_cli("query", *selector, "--offset", "0", "--db", db)
            check(f"query {selector[0]} + --offset 0：人读文本与省略逐字节一致",
                  omitted.returncode == 0 and omitted.stdout == explicit_zero.stdout,
                  explicit_zero.stdout[:120])
        result = run_cli("query", "--key", "K0000", "--offset", "5", "--db",
                         os.path.join(tmp, "missing-key.jsonl"))
        check("key + 非零 offset 在开库前拒绝（缺库未创建）",
              result.returncode == 2 and not os.path.exists(
                  os.path.join(tmp, "missing-key.jsonl")))
        title_zero = cli_json("query", "--title", TITLE, "--offset", "0",
                              "--json", "--db", db)
        check("title + --offset 0：显式分页产生 page（不是省略路径）",
              "page" in title_zero and title_zero["page"]["offset"] == 0)

        # ── 7) advanced truncated：旧口径 vs 分页口径 ─────────────────────────
        print("\n── 7) advanced truncated 旧口径 / 分页口径 ──")
        base = store.query_advanced(bibdb.SearchSpec((T("title", "shared"),),
                                                     limit=200))
        check("省略 offset（offset 默认 0）：total_matched=全量、truncated=total>count",
              base["total_matched"] == TOTAL and base["count"] == 200
              and base["truncated"] is True and "offset" not in base)
        zero = store.query_advanced(bibdb.SearchSpec((T("title", "shared"),),
                                                     limit=200, offset=0))
        check("显式 offset=0：与省略同值（total>0+count）、仍无 offset 键",
              zero["total_matched"] == TOTAL and zero["count"] == 200
              and zero["truncated"] is True and "offset" not in zero)
        mid = store.query_advanced(bibdb.SearchSpec((T("title", "shared"),),
                                                    limit=200, offset=200))
        check("offset=200 limit=200：truncated = 401 > 200+200 = True",
              mid["count"] == 200 and mid["truncated"] is True
              and mid["offset"] == 200)
        tail = store.query_advanced(bibdb.SearchSpec((T("title", "shared"),),
                                                     limit=200, offset=300))
        check("offset=300 limit=200：truncated = 401 > 300+101 = False（不是旧的 total>count）",
              tail["count"] == 101 and tail["truncated"] is False)
        over = store.query_advanced(bibdb.SearchSpec((T("title", "shared"),),
                                                     limit=200, offset=401))
        check("offset=401：count=0、truncated = 401 > 401 = False",
              over["count"] == 0 and over["truncated"] is False
              and over["offset"] == 401)
        text = run_cli("query-advanced", "--title", "shared", "--limit", "200",
                       "--db", db)
        check("省略 offset：人读仍写「已截断」", "已截断" in text.stdout)
        text = run_cli("query-advanced", "--title", "shared", "--limit", "200",
                       "--offset", "0", "--db", db)
        check("显式 offset=0：人读改分页文案、不再写「已截断」",
              "已截断" not in text.stdout and "（第 1–200 条 / 共 401 条）" in text.stdout
              and "；下一页 --offset 200" in text.stdout, text.stdout.splitlines()[:2])
        text = run_cli("query-advanced", "--title", "shared", "--limit", "200",
                       "--offset", "400", "--db", db)
        check("显式 offset=400（末页）：无下一页提示",
              "（第 401–401 条 / 共 401 条）" in text.stdout
              and "下一页" not in text.stdout)

        # ── 8) page.db_sha256 == 库文件字节 SHA ───────────────────────────────
        print("\n── 8) page.db_sha256 == 库文件 SHA-256 ──")
        payload = cli_json("query", "--title", TITLE, "--offset", "0", "--json",
                           "--db", db)
        check("title page.db_sha256 == 文件 SHA（小写 64 位 hex）",
              payload["page"]["db_sha256"] == file_sha(db)
              and payload["page"]["db_sha256"] == before_sha
              and payload["page"]["db_sha256"] == payload["page"]["db_sha256"].lower()
              and len(payload["page"]["db_sha256"]) == 64)
        payload = cli_json("query-advanced", "--title", "shared", "--offset", "200",
                           "--json", "--db", db)
        check("advanced page.db_sha256 == 文件 SHA",
              payload["page"]["db_sha256"] == file_sha(db))

        # ── 9) 每页 ≤200、next_offset 链走完、无重无漏 ────────────────────────
        print("\n── 9) next_offset 链：每页 ≤200、走完、无重无漏 ──")
        seen: list[str] = []
        offset = 0
        pages = 0
        while pages < 10:
            payload = cli_json("query", "--title", TITLE, "--limit", "200",
                               "--offset", str(offset), "--json", "--db", db)
            page = payload["page"]
            pages += 1
            check(f"链第 {pages} 页：page.offset == 请求 offset、hits ≤ 200",
                  page["offset"] == offset and len(payload["hits"]) <= 200
                  and payload["count"] == len(payload["hits"]))
            seen.extend(hit["key"] for hit in payload["hits"])
            if not page["has_more"]:
                break
            check("has_more 时 next_offset == offset + 本页条数",
                  page["next_offset"] == page["offset"] + len(payload["hits"]))
            offset = page["next_offset"]
        check("next_offset 链能走完（3 页：200+200+1）",
              pages == 3 and len(seen) == TOTAL)
        check("翻页无重无漏：去重后 == 独立全量顺序（逐 key）",
              seen == full_title and len(set(seen)) == TOTAL)
        payload = cli_json("query-advanced", "--title", "shared", "--limit", "200",
                           "--offset", "400", "--json", "--db", db)
        check("advanced 末页：has_more=False、next_offset=null、count=1",
              payload["page"]["has_more"] is False
              and payload["page"]["next_offset"] is None and payload["count"] == 1)
        payload = cli_json("query-advanced", "--title", "shared", "--limit", "200",
                           "--offset", "401", "--json", "--db", db)
        check("advanced 越界：count=0、has_more=False、next_offset=null",
              payload["count"] == 0 and payload["page"]["has_more"] is False
              and payload["page"]["next_offset"] is None)

        # ── 10) 边界与防御：SearchSpec/query_title offset 校验、空题名、high 口径 ──
        print("\n── 10) 边界：offset 校验、空题名、high/ambiguous 口径 ──")
        for bad, message in ((-1, "offset 不能为负：-1"), (True, "offset 必须是整数"),
                             (None, "offset 必须是整数"),
                             ("3", "offset 必须是整数")):
            try:
                bibdb.SearchSpec((T("title", "x"),), offset=bad)
                raised = ""
            except bibdb.BibDBError as exc:
                raised = str(exc)
            check(f"SearchSpec(offset={bad!r}) → BibDBError({message})",
                  raised == message, raised)
        for bad, message in ((-1, "offset 不能为负：-1"), (True, "offset 必须是整数"),
                             (None, "offset 必须是整数"), ("3", "offset 必须是整数")):
            try:
                store.query_title_page(TITLE, offset=bad)
                raised = ""
            except bibdb.BibDBError as exc:
                raised = str(exc)
            check(f"query_title_page(offset={bad!r}) → BibDBError({message})",
                  raised == message, raised)
        for bad, message in ((0, "limit 超出范围：0（1–200）"),
                             (201, "limit 超出范围：201（1–200）"),
                             (True, "limit 超出范围：True（1–200）"),
                             ("5", "limit 超出范围：5（1–200）")):
            try:
                store.query_title_page(TITLE, limit=bad)
                raised = ""
            except bibdb.BibDBError as exc:
                raised = str(exc)
            check(f"query_title_page(limit={bad!r}) → BibDBError({message})",
                  raised == message, raised)
        result = store.query_title_page(TITLE, limit=200, offset=200)
        check("query_title_page(offset=200)：high 仍按全量命中算（=401，不是 200）",
              result["high"] == TOTAL and result["count"] == 200
              and result["total_matched"] == TOTAL and result["offset"] == 200)
        result = store.query_title_page(TITLE, limit=200, offset=400)
        check("query_title_page(offset=400)：ambiguous 按本页（1 条 → False）",
              result["count"] == 1 and result["ambiguous"] is False)
        result = store.query_title_page(TITLE, limit=200, offset=0)
        check("query_title_page(offset=0)：ambiguous 按本页（200 条 → True）",
              result["ambiguous"] is True)
        result = store.query_title_page("", offset=0)
        check("空题名 + query_title_page：8 键、total_matched=0、offset=0、high=0",
              set(result) == {"query", "hits", "count", "high", "ambiguous",
                              "error", "offset", "total_matched"}
              and result["total_matched"] == 0 and result["offset"] == 0
              and result["count"] == 0 and result["high"] == 0
              and result["error"] == "题名为空")
        result = store.query_title("")
        check("空题名 + 旧 query_title：键与旧行为一致（无 offset/total_matched）",
              set(result) == {"query", "hits", "count", "ambiguous", "error"})
        payload = cli_json("query", "--title", "", "--offset", "0", "--json",
                           "--db", db)
        check("CLI 空题名 + offset 0：page.total_matched=0、count=0",
              payload["count"] == 0 and payload["page"]["total_matched"] == 0
              and payload["page"]["has_more"] is False)
        text = run_cli("query", "--title", "", "--offset", "0", "--db", db)
        check("CLI 空题名 + offset 0：人读分页零命中文案逐字",
              "题名检索命中 0 条（offset=0 超出共 0 条）" in text.stdout,
              text.stdout.splitlines()[:3])

        # ── 11) 全程零写：查询/翻页/错误后库字节不变 ───────────────────────────
        print("\n── 11) 全程零写 ──")
        check("全部查询/翻页/错误路径之后：库文件字节与 SHA 均不变",
              read_bytes(db) == before_bytes and file_sha(db) == before_sha)
        check("tmp 内没有新增其它库文件（只有合成库）",
              sorted(name for name in os.listdir(tmp) if name.endswith(".jsonl"))
              == ["p06-paging.jsonl"],
              str(sorted(os.listdir(tmp))))

    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 66)
    if fails:
        print(f"失败 {len(fails)} 项：{fails}")
        return 1
    print(f"全部 {pass_n} 项断言通过")
    print("失败 0 项（全部通过）")
    return 0


def test_pkg_p06_query_paging() -> None:
    """pytest 入口：与脚本模式共用同一套断言（runner 仍按脚本跑一次）。

    本文件是脚本式判据（`python tests/…py`，rc 即判据）；这里补一个 pytest 可收集的
    入口，避免 `pytest Engine/tests` 收集到 0 项，也避免 import 时副作用。
    """
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
