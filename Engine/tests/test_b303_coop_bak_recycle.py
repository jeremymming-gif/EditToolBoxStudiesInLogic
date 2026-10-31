#!/usr/bin/env python3
"""B-303 回归（Part A）：coop 旧交付目录（`.coop-bak-`）自动回收。

全部 hermetic（tmpdir），走**真的** `coop.promote_stage()` + 真的回收站（`slcn.clean`）。
断言面：
  (a) 首交付：没有旧目录 → 不产生回收站、不产生 `.coop-bak-*`；
  (b) 覆盖交付：旧目录进 `<交付根>/.toolbox-trash/<根名>-<stamp>/<名>.coop-bak-*`，
      工作区不再有同级 `.coop-bak-*`，新交付内容正确；
  (c) manifest：schema=2 / `mode="coop-bak"` / 条目 `type="dir"`，`restore()` 整棵搬回；
  (d) 二次覆盖：同一交付只留**最近 1 份**（旧条目连 manifest 一起清、删空的回收目录收掉），
      回收站里**别的**内容一个不动；
  (e) promote 换新失败：旧交付逐字节回滚、不产生回收站、不留 `.coop-tmp-*`；
  (f) convert 侧收尾：记录 §五 指向回收站相对路径（旧文案不再出现）；源码里回收调用在
      `try` 内且 handler **不 die/不 raise**（回收失败只 warning，交付照样成功）。
"""
from __future__ import annotations

import ast
import glob
import json
import os
import pathlib
import shutil
import sys
import tempfile

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import clean as C  # noqa: E402
from slcn import coop  # noqa: E402

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


def write_tree(root: str, files: dict[str, bytes]) -> None:
    os.makedirs(root, exist_ok=True)
    for rel, data in files.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(data)


def tree(root: str) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, root)] = fh.read()
    return out


def read_manifest(trash_root: str) -> dict:
    with open(os.path.join(trash_root, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def deliver(final: str, files: dict[str, bytes]) -> dict:
    """模拟 convert_stage：写暂存目录 → promote（交付到 final，覆盖时留 backup）。"""
    stage = os.path.join(os.path.dirname(final), "." + os.path.basename(final) + ".stage")
    shutil.rmtree(stage, ignore_errors=True)
    write_tree(stage, files)
    try:
        return coop.promote_stage(stage, final, exclude=(coop.STATE_NAME,))
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def siblings(root: str, pattern: str) -> list[str]:
    return [os.path.basename(p) for p in glob.glob(os.path.join(root, pattern))]


# ── (a) 首交付 ───────────────────────────────────────────────────────────


def test_first_delivery(tmp: str) -> None:
    root = os.path.join(tmp, "a")
    final = os.path.join(root, "2024年第1期")
    promoted = deliver(final, {"芯片.tex": b"v1"})
    check("(a)-1 首交付没有旧目录（backup 为空）",
          promoted.get("backup") == "", repr(promoted.get("backup")))
    check("(a)-2 首交付不产生回收站",
          not os.path.exists(os.path.join(root, C.TRASH_DIR)),
          os.path.join(root, C.TRASH_DIR))
    check("(a)-3 首交付工作区无 .coop-bak-*", siblings(root, "*.coop-bak-*") == [])
    check("(a)-4 首交付内容正确", tree(final) == {"芯片.tex": b"v1"}, str(tree(final)))


# ── (b)(c) 覆盖交付 → 回收站 + manifest + restore ────────────────────────


def test_cover_then_restore(tmp: str) -> None:
    root = os.path.join(tmp, "b")
    final = os.path.join(root, "2024年第1期")
    write_tree(final, {"旧.tex": b"old", "旧/附表.txt": b"t"})
    promoted = deliver(final, {"新.tex": b"new"})
    backup = promoted.get("backup", "")

    check("(b)-1 覆盖交付留下旧目录 backup",
          bool(backup) and os.path.isdir(backup)
          and os.path.basename(backup).startswith(os.path.basename(final) + C.COOP_BAK_MARK),
          backup)
    check("(b)-2 覆盖后新交付内容正确", tree(final) == {"新.tex": b"new"}, str(tree(final)))

    stamp = "2026_01_02_030405"
    retired = C.retire_coop_bak(final, backup, stamp=stamp)
    trash_root = os.path.join(root, C.TRASH_DIR, os.path.basename(root) + "-" + stamp)
    check("(b)-3 旧目录移入 <根>/.toolbox-trash/<根名>-<stamp>/<旧名>",
          retired == os.path.join(trash_root, os.path.basename(backup)), retired)
    check("(b)-4 回收站里旧交付整棵在（含含子目录）",
          tree(retired) == {"旧.tex": b"old", "旧/附表.txt": b"t"}, str(tree(retired)))
    check("(b)-5 工作区不再有同级 .coop-bak-*",
          not os.path.exists(backup) and siblings(root, "*.coop-bak-*") == [],
          str(siblings(root, "*.coop-bak-*")))

    man = read_manifest(trash_root)
    check("(c)-1 manifest schema=2 / mode=coop-bak / root 为交付根",
          man.get("schema") == 2 and man.get("mode") == "coop-bak"
          and man.get("root") == os.path.abspath(root), json.dumps(man, ensure_ascii=False))
    check("(c)-2 条目 type=dir、path=旧目录名",
          [(e.get("path"), e.get("type")) for e in man.get("entries") or []]
          == [(os.path.basename(backup), "dir")],
          json.dumps(man.get("entries") or [], ensure_ascii=False))

    n, moved = C.restore(root, trash_root)
    check("(c)-3 restore 整棵搬回原位",
          n == 1 and moved == [os.path.basename(backup)]
          and os.path.isdir(backup)
          and tree(backup) == {"旧.tex": b"old", "旧/附表.txt": b"t"},
          f"n={n} moved={moved}")
    check("(c)-4 回收站里旧条目已搬走（restore 不覆盖新交付）",
          not os.path.isdir(os.path.join(trash_root, os.path.basename(backup)))
          and tree(final) == {"新.tex": b"new"}, str(tree(final)))


# ── (d) 二次覆盖：只留最近 1 份、不动别的回收内容 ────────────────────────


def test_keep_newest_only(tmp: str) -> None:
    root = os.path.join(tmp, "d")
    final = os.path.join(root, "第2期")
    write_tree(final, {"v1.tex": b"1"})
    p2 = deliver(final, {"v2.tex": b"2"})
    p3 = deliver(final, {"v3.tex": b"3"})
    check("(d)-1 两次覆盖各自留下旧目录",
          os.path.isdir(p2["backup"]) and os.path.isdir(p3["backup"]),
          f"{p2['backup']} / {p3['backup']}")

    # 回收站里另有**别的**回收内容（不得被剪枝误伤）
    other = os.path.join(root, C.TRASH_DIR, "别的东西-2020_01_01_000000")
    write_tree(other, {"keep.aux": b"k"})
    with open(os.path.join(other, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema": 2, "root": os.path.abspath(root),
                   "createdAt": "2020_01_01_000000", "mode": "aux",
                   "entries": [{"path": "keep.aux", "type": "file"}]}, fh,
                  ensure_ascii=False)

    # 故意让**旧**回收目录的时间戳字典序更大：剪枝必须按「本次新建」保，不按名字排
    old_stamp = "2099_12_31_235959"
    new_stamp = "2026_01_02_030406"
    old_trash = os.path.dirname(C.retire_coop_bak(final, p2["backup"], stamp=old_stamp))
    r3 = C.retire_coop_bak(final, p3["backup"], stamp=new_stamp)
    new_trash = os.path.dirname(r3)

    man = read_manifest(new_trash)
    check("(d)-2 回收站只剩最新 1 份该交付的旧目录",
          [(e.get("path"), e.get("type")) for e in man.get("entries") or []]
          == [(os.path.basename(p3["backup"]), "dir")]
          and os.path.isdir(r3), json.dumps(man.get("entries") or [], ensure_ascii=False))
    check("(d)-3 更旧的回收目录连 manifest 一起收掉（不留空壳/悬空条目）",
          not os.path.exists(old_trash), old_trash)
    check("(d)-4 工作区无同级 .coop-bak-*", siblings(root, "*.coop-bak-*") == [],
          str(siblings(root, "*.coop-bak-*")))
    check("(d)-5 回收站里别的条目一个不动",
          os.path.isfile(os.path.join(other, "keep.aux"))
          and [e.get("path") for e in read_manifest(other).get("entries") or []]
          == ["keep.aux"], other)


# ── (e) promote 换新失败：回滚、零回收站 ────────────────────────────────


def test_promote_failure_no_trash(tmp: str) -> None:
    root = os.path.join(tmp, "e")
    final = os.path.join(root, "第3期")
    write_tree(final, {"旧.tex": b"old"})
    before = tree(final)
    stage = os.path.join(root, ".stage")
    write_tree(stage, {"新.tex": b"new"})
    calls: list[tuple[str, str]] = []

    def flaky(src: str, dst: str) -> None:
        calls.append((os.path.basename(src), os.path.basename(dst)))
        if len(calls) == 2:  # 第 2 次 = tmp → final（旧目录已让位）
            raise OSError("模拟换新失败")
        os.replace(src, dst)

    raised = ""
    try:
        coop.promote_stage(stage, final, exclude=(coop.STATE_NAME,), rename=flaky)
    except OSError as exc:
        raised = str(exc)
    check("(e)-1 换新失败照常抛（调用方收口）", raised == "模拟换新失败", raised)
    check("(e)-2 旧交付逐字节回滚", tree(final) == before, str(tree(final)))
    check("(e)-3 不产生回收站",
          not os.path.exists(os.path.join(root, C.TRASH_DIR)))
    check("(e)-4 不留 .coop-tmp-*/.coop-bak-*",
          siblings(root, "*.coop-tmp-*") == [] and siblings(root, "*.coop-bak-*") == [],
          str(siblings(root, "*.coop-*")))
    shutil.rmtree(stage, ignore_errors=True)


# ── (f) convert 侧：记录 §五 指向回收站 / 回收失败只 warning ─────────────


def test_convert_record_and_warning() -> None:
    rel = ".toolbox-trash/g-2026_01_02_030405/x.coop-bak-20260102-030405"
    note = f"已移入回收站：`{rel}`（可在「删除临时文件」恢复/清空）"
    base = dict(stem="x", phase="done", parse_report=coop.ParseReport(),
                low_report=coop.LowConfReport(), merge_summary=None,
                apply_summary=None, final_dir="/tmp/g", no_ai_merge=True,
                compiled="未请求 --compile")
    rec = coop.render_record(backup=note, **base)
    check("(f)-1 记录 §五 指向回收站相对路径",
          "## 五、旧交付目录（回收站）" in rec and rel in rec, rec[-500:])
    check("(f)-2 旧「已备份为」文案不再出现",
          "已备份为" not in rec and "## 五、交付备份" not in rec)
    plain = coop.render_record(**base)
    check("(f)-3 无旧目录时照旧不出 §五",
          "## 五、" not in plain and "回收站" not in plain)

    src = (ENGINE / "slcn" / "convert_stage.py").read_text(encoding="utf-8")
    check("(f)-4 回收调用在 promote 之后",
          src.index("retire_coop_bak") > src.index("coop.promote_stage"))
    check("(f)-5 成功提示与失败 warning 文案都在",
          "旧交付目录已移入回收站" in src
          and "旧交付目录未能移入回收站（不影响交付）" in src
          and "旧交付目录已备份" not in src)
    check("(f)-6 记录参数接的是回收结论", "backup=_backup_note" in src)

    in_try, handler_safe = False, True
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Try):
            continue
        body = ast.Module(body=list(node.body), type_ignores=[])
        calls = [n for n in ast.walk(body)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", "") == "retire_coop_bak"]
        if not calls:
            continue
        in_try = True
        for handler in node.handlers:
            for sub in ast.walk(handler):
                if isinstance(sub, ast.Raise):
                    handler_safe = False
                if isinstance(sub, ast.Call):
                    fn = getattr(sub.func, "id", "") or getattr(sub.func, "attr", "")
                    if fn == "die":
                        handler_safe = False
    check("(f)-7 回收失败只 warning（在 try 内、handler 不 die/raise）",
          in_try and handler_safe, f"in_try={in_try} handler_safe={handler_safe}")

    # 回收助手自己失败时必须**抛**：convert 的 try/except 才有东西可接
    missing = os.path.join(os.path.dirname(os.path.abspath(__file__)), "不存在", "x")
    try:
        C.retire_coop_bak("/tmp/g", missing)
        raised = ""
    except FileNotFoundError as exc:
        raised = type(exc).__name__
    check("(f)-8 旧目录不存在 → FileNotFoundError（交给调用方降级）",
          raised == "FileNotFoundError", raised)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="b303_")
    try:
        test_first_delivery(tmp)
        test_cover_then_restore(tmp)
        test_keep_newest_only(tmp)
        test_promote_failure_no_trash(tmp)
        test_convert_record_and_warning()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if fails:
        print(f"失败 {len(fails)} 项：{fails}")
        print(f"SUMMARY total={pass_n + len(fails)} pass={pass_n} fail={len(fails)}")
        return 1
    print(f"全部 {pass_n} 项断言通过")
    print(f"SUMMARY total={pass_n} pass={pass_n} fail=0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
