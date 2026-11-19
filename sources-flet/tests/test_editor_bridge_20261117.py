"""2026-11-17 对比预览（DESIGN §10）：`services/editor_bridge.py` 的离线契约。

锁 8 组行为（全部在 tmp_path 里跑；不建窗口、不碰真实产物目录、不开真 VS Code）：

1. `make_preview` 落盘：标准文件名副本（`reference.bib` / `reference_proposed.bib`）、
   字节与源一致、`preview.json` 字段齐全、`preview_id` 由两侧 sha 派生（同内容可复现）；
2. 长公共前缀/后缀的两个版本**字节不同**、sha 不同、id 不同 —— 防「看似相同的假相等」；
3. `verify_preview` 是 apply 闸门：正常 True；副本被篡改 / 副本被删 / 元数据坏 / 元数据
   字段缺失 / `preview_id` 与目录不符，一律 False 且给中文原因；
4. 不覆盖用户已有 `.diff`：`.diff` 先被建成**文件** → `make_preview` 报错且**没删它**；
   `.diff` 下别人的预览目录原样保留（只写自己 preview_id 子目录）；
5. 用户原文件只读：`make_preview` 前后源文件字节不变；源缺失 / work_dir 为空 → ValueError；
6. `open_diff` 诚实文案：没有编辑器 → `(False, msg)`，msg 含**两个副本路径**且不含「已打开」；
   非零退出码同样 False；monkeypatch 出假 `code` + 成功 `subprocess.run` → `(True, …)`，
   且 argv 是 **list**、含 `"--diff"`、`shell` 不是 True；
7. `.tex` 走同一条路径（扩展名换成 `.tex`，副本名同规则）；
8. 静态守卫：模块源码里 grep 不到 `shell=True`、`os.path.relpath`、`import flet`/`tkinter`。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from services import editor_bridge as EB

APP = Path(__file__).resolve().parents[1] / "app"
MODULE_PATH = APP / "services" / "editor_bridge.py"

OLD_BIB = """@article{alpha2020,
  author = {Alpha, A.},
  title  = {A Study of Logic},
  year   = {2020},
}

@book{beta2019,
  author = {Beta, B.},
  title  = {Another Book},
  year   = {2019},
}
"""

NEW_BIB = """@article{alpha2020,
  author = {Alpha, A.},
  title  = {A Study of Logic (Revised)},
  year   = {2020},
  doi    = {10.1000/xyz},
}

@book{beta2019,
  author = {Beta, B.},
  title  = {Another Book},
  year   = {2019},
}
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def work(tmp_path: Path) -> Path:
    """产物目录（引擎暂存目录的对等物）：预览都落在它的 `.diff/` 下。"""
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def old_bib(tmp_path: Path) -> Path:
    path = tmp_path / "reference.bib"
    path.write_text(OLD_BIB, encoding="utf-8", newline="")
    return path


def _make(work: Path, old_bib: Path, new_text: str = NEW_BIB, **kwargs) -> EB.Preview:
    return EB.make_preview(old_file=str(old_bib), new_text=new_text,
                           work_dir=str(work), **kwargs)


def _no_editor(monkeypatch) -> None:
    """模拟「机器上没有 code」：which 探不到，常见安装路径也当作不存在。"""
    monkeypatch.setattr(EB.shutil, "which", lambda name, *a, **k: None)
    monkeypatch.setattr(EB, "CODE_PATHS", ())


def _assert_not_claimed_open(msg: str) -> None:
    """失败文案绝不能自称「已打开对比」（DESIGN §10 硬要求）。"""
    assert "已打开" not in msg, msg
    assert "打开成功" not in msg, msg


# ── 1. make_preview 落盘：标准文件名 / 字节一致 / 元数据齐全 ────────────────
def test_make_preview_writes_standard_names_under_hidden_diff_dir(work, old_bib):
    preview = _make(work, old_bib)

    assert os.path.basename(preview.old_path) == "reference.bib"
    assert os.path.basename(preview.new_path) == "reference_proposed.bib"
    assert os.path.dirname(preview.old_path) == preview.dir
    assert os.path.dirname(preview.new_path) == preview.dir
    # 隐藏子目录：<work>/.diff/<preview_id>/，且是绝对路径
    assert os.path.isabs(preview.dir)
    assert os.path.dirname(preview.dir) == os.path.join(os.path.abspath(str(work)), ".diff")
    assert os.path.basename(preview.dir) == preview.preview_id
    assert os.path.isdir(preview.dir)

    # 副本内容与来源一致：旧版 = 源文件字节，新版 = new_text 的 UTF-8 字节
    assert Path(preview.old_path).read_bytes() == old_bib.read_bytes()
    assert Path(preview.new_path).read_text(encoding="utf-8") == NEW_BIB
    assert preview.old_sha256 == _sha(Path(preview.old_path).read_bytes())
    assert preview.new_sha256 == _sha(Path(preview.new_path).read_bytes())
    assert preview.ext == ".bib"


def test_make_preview_metadata_has_all_fields_and_matches_id(work, old_bib):
    preview = _make(work, old_bib)
    meta_path = Path(preview.dir) / EB.PREVIEW_META
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    for key in ("preview_id", "old_sha256", "new_sha256", "ext", "created_at"):
        assert key in meta, f"preview.json 缺字段 {key}"
    assert meta["preview_id"] == preview.preview_id
    assert meta["old_sha256"] == preview.old_sha256 == _sha(old_bib.read_bytes())
    assert meta["new_sha256"] == preview.new_sha256
    assert meta["ext"] == ".bib"
    assert EB.preview_id_for(meta["old_sha256"], meta["new_sha256"]) == meta["preview_id"]

    # id 形状：p + 12 hex（DESIGN §10 的稳定 id）
    assert preview.preview_id.startswith("p")
    assert len(preview.preview_id) == 13
    assert all(ch in "0123456789abcdef" for ch in preview.preview_id[1:])

    # created_at 是 ISO8601 本地时间（可被 fromisoformat 解析、无时区后缀）
    assert datetime.datetime.fromisoformat(preview.created_at) is not None
    assert meta["created_at"] == preview.created_at


def test_same_content_reuses_one_preview_dir(work, old_bib):
    first = _make(work, old_bib)
    second = _make(work, old_bib)
    assert first.preview_id == second.preview_id
    assert first.dir == second.dir
    # 只多出一个 preview_id 目录，不堆临时目录
    assert sorted(p.name for p in (work / ".diff").iterdir()) == [first.preview_id]


# ── 2. 长公共前缀/后缀：两个版本必须真的不同 ───────────────────────────────
def test_versions_sharing_long_prefix_and_suffix_still_differ(tmp_path, work):
    common_head = "@article{gamma,\n  title = {Same Title},\n" * 40
    common_tail = "  year = {2021},\n}\n" * 20
    old_text = common_head + "  note  = {first},\n" + common_tail
    new_text = common_head + "  note  = {third},\n" + common_tail
    source = tmp_path / "shared.bib"
    source.write_text(old_text, encoding="utf-8", newline="")

    preview = _make(work, source, new_text=new_text)

    old_bytes = Path(preview.old_path).read_bytes()
    new_bytes = Path(preview.new_path).read_bytes()
    assert old_bytes != new_bytes, "长公共前后缀下的两版副本必须字节不同"
    assert preview.old_sha256 != preview.new_sha256
    assert len(old_bytes) == len(new_bytes)          # 只差一个词，长度相同
    # 差异确实在中间那处，而不是整份重写
    assert b"first" in old_bytes and b"first" not in new_bytes
    assert b"third" in new_bytes and b"third" not in old_bytes


def test_different_content_yields_different_preview_id(work, old_bib):
    one = _make(work, old_bib, new_text=NEW_BIB)
    two = _make(work, old_bib, new_text=NEW_BIB.replace("Revised", "Rewritten"))
    assert one.preview_id != two.preview_id
    assert one.dir != two.dir
    assert one.old_sha256 == two.old_sha256          # 旧版没变
    assert one.new_sha256 != two.new_sha256


# ── 3. verify_preview：过期副本不得继续用于应用 ────────────────────────────
def test_verify_preview_accepts_fresh_copies(work, old_bib):
    preview = _make(work, old_bib)
    ok, msg = EB.verify_preview(preview.preview_id, str(work))
    assert ok is True
    assert msg == ""


def test_verify_preview_rejects_tampered_copy(work, old_bib):
    preview = _make(work, old_bib)
    Path(preview.new_path).write_text(NEW_BIB + "@misc{injected,\n}\n",
                                      encoding="utf-8", newline="")
    ok, msg = EB.verify_preview(preview.preview_id, str(work))
    assert ok is False
    assert "不一致" in msg or "过期" in msg
    assert msg.strip()


def test_verify_preview_rejects_tampered_old_copy(work, old_bib):
    preview = _make(work, old_bib)
    Path(preview.old_path).write_text("@misc{sneaky,\n}\n", encoding="utf-8", newline="")
    ok, msg = EB.verify_preview(preview.preview_id, str(work))
    assert ok is False
    assert msg.strip()


def test_verify_preview_rejects_missing_copy(work, old_bib):
    preview = _make(work, old_bib)
    os.remove(preview.new_path)
    ok, msg = EB.verify_preview(preview.preview_id, str(work))
    assert ok is False
    assert "缺失" in msg


def test_verify_preview_rejects_corrupt_metadata(work, old_bib):
    preview = _make(work, old_bib)
    (Path(preview.dir) / EB.PREVIEW_META).write_text("{not json,,}", encoding="utf-8")
    ok, msg = EB.verify_preview(preview.preview_id, str(work))
    assert ok is False
    assert "元数据" in msg


def test_verify_preview_rejects_metadata_missing_fields(work, old_bib):
    preview = _make(work, old_bib)
    meta_path = Path(preview.dir) / EB.PREVIEW_META
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    del meta["old_sha256"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    ok, msg = EB.verify_preview(preview.preview_id, str(work))
    assert ok is False
    assert msg.strip()


def test_verify_preview_rejects_id_mismatch_and_unknown_id(work, old_bib):
    preview = _make(work, old_bib)
    meta_path = Path(preview.dir) / EB.PREVIEW_META
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["preview_id"] = "pdeadbeef0000"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    ok, msg = EB.verify_preview(preview.preview_id, str(work))
    assert ok is False
    assert "preview_id" in msg

    ok2, msg2 = EB.verify_preview("p000000000000", str(work))
    assert ok2 is False and "不存在" in msg2

    ok3, msg3 = EB.verify_preview("", str(work))
    assert ok3 is False and msg3.strip()

    ok4, msg4 = EB.verify_preview("../../etc", str(work))
    assert ok4 is False and "不合法" in msg4


# ── 4. 不覆盖用户已有 .diff（文件占位 / 别人的预览目录）──────────────────
def test_make_preview_refuses_when_diff_is_a_file_and_keeps_it(work, old_bib):
    squatter = work / ".diff"
    squatter.write_text("用户自己建的占位文件\n", encoding="utf-8")
    before = squatter.read_bytes()

    with pytest.raises(ValueError) as info:
        _make(work, old_bib)
    assert ".diff" in str(info.value)
    assert "不是目录" in str(info.value)

    # 没有清空、没有改名、没有写进去
    assert squatter.is_file()
    assert squatter.read_bytes() == before


def test_make_preview_leaves_other_previews_untouched(work, old_bib):
    other = work / ".diff" / "p000000000001"
    other.mkdir(parents=True)
    (other / "reference.bib").write_text("@misc{other,\n}\n", encoding="utf-8")
    (other / EB.PREVIEW_META).write_text("{}", encoding="utf-8")

    preview = _make(work, old_bib)

    assert preview.preview_id != "p000000000001"
    assert (other / "reference.bib").read_text(encoding="utf-8") == "@misc{other,\n}\n"
    assert (other / EB.PREVIEW_META).read_text(encoding="utf-8") == "{}"


# ── 5. 用户原文件只读 + 参数错误的诚实报错 ────────────────────────────────
def test_make_preview_never_writes_the_original(work, old_bib):
    before = old_bib.read_bytes()
    before_mtime = old_bib.stat().st_mtime_ns
    _make(work, old_bib)
    assert old_bib.read_bytes() == before
    assert old_bib.stat().st_mtime_ns == before_mtime


def test_make_preview_rejects_bad_inputs(work, old_bib, tmp_path):
    with pytest.raises(ValueError):
        EB.make_preview(old_file=str(tmp_path / "nope.bib"), new_text=NEW_BIB,
                        work_dir=str(work))
    with pytest.raises(ValueError):
        EB.make_preview(old_file="", new_text=NEW_BIB, work_dir=str(work))
    with pytest.raises(ValueError):
        EB.make_preview(old_file=str(old_bib), new_text=NEW_BIB, work_dir="")
    with pytest.raises(ValueError):
        EB.make_preview(old_file=str(old_bib), new_text=NEW_BIB,
                        work_dir=str(tmp_path / "no-such-dir"))
    with pytest.raises(ValueError):
        EB.make_preview(old_file=str(old_bib), new_text=b"bytes", work_dir=str(work))  # type: ignore[arg-type]


def test_newline_bytes_are_preserved(work, old_bib):
    """`newline=""`：调用方给 CRLF 就落 CRLF，不做换行翻译（字节稳定）。"""
    crlf = "@misc{x,\r\n  title = {T},\r\n}\r\n"
    preview = _make(work, old_bib, new_text=crlf)
    assert Path(preview.new_path).read_bytes() == crlf.encode("utf-8")
    assert EB.verify_preview(preview.preview_id, str(work))[0] is True


def test_candidate_digest_checks_the_actual_bytes_before_writing_copies(work, old_bib):
    payload = b"@misc{x,\r\n  title = {T},\r\n}\r\n"
    expected = hashlib.sha256(payload).hexdigest()
    kwargs = {"old_file": str(old_bib), "work_dir": str(work),
              "expected_new_sha256": expected}
    with pytest.raises(EB.CandidateIntegrityError, match="重新生成拟应用结果"):
        EB.make_preview(new_bytes=payload.replace(b"\r\n", b"\n"), **kwargs)
    assert not (work / EB.DIFF_DIRNAME).exists()
    preview = EB.make_preview(new_bytes=payload, **kwargs)
    assert Path(preview.new_path).read_bytes() == payload
    assert preview.new_sha256 == expected


def test_preview_dir_and_stem_customisation(work, old_bib):
    assert EB.preview_dir(str(work)) == os.path.join(os.path.abspath(str(work)), ".diff")
    with pytest.raises(ValueError):
        EB.preview_dir("")

    preview = _make(work, old_bib, stem="paper")
    assert os.path.basename(preview.old_path) == "paper.bib"
    assert os.path.basename(preview.new_path) == "paper_proposed.bib"


# ── 6. open_diff：诚实文案 + 列表 argv + 无 shell ─────────────────────────
def test_open_diff_without_editor_gives_paths_and_no_false_claim(work, old_bib, monkeypatch):
    preview = _make(work, old_bib)
    _no_editor(monkeypatch)

    ok, msg = EB.open_diff(preview)

    assert ok is False
    assert preview.old_path in msg
    assert preview.new_path in msg
    _assert_not_claimed_open(msg)
    assert "VS Code" in msg


def test_open_diff_success_uses_list_argv_without_shell(work, old_bib, monkeypatch):
    preview = _make(work, old_bib)
    calls: list[tuple[list, dict]] = []

    monkeypatch.setattr(EB.shutil, "which", lambda name, *a, **k: "/fake/bin/code")

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(EB.subprocess, "run", fake_run)

    ok, msg = EB.open_diff(preview)

    assert ok is True
    assert msg.strip()
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert isinstance(argv, list), "必须是列表 argv，不能是拼接出来的字符串"
    assert all(isinstance(part, str) for part in argv)
    assert "--diff" in argv
    assert preview.old_path in argv and preview.new_path in argv
    assert argv[-2:] == [preview.old_path, preview.new_path]
    assert kwargs.get("shell") is not True


def test_open_diff_nonzero_exit_is_reported_honestly(work, old_bib, monkeypatch):
    preview = _make(work, old_bib)
    monkeypatch.setattr(EB.shutil, "which", lambda name, *a, **k: "/fake/bin/code")
    monkeypatch.setattr(EB.subprocess, "run",
                        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 3, "", "boom"))

    ok, msg = EB.open_diff(preview)

    assert ok is False
    assert "3" in msg
    assert preview.old_path in msg and preview.new_path in msg
    _assert_not_claimed_open(msg)


def test_open_diff_launch_exception_is_reported_honestly(work, old_bib, monkeypatch):
    preview = _make(work, old_bib)
    monkeypatch.setattr(EB.shutil, "which", lambda name, *a, **k: "/fake/bin/code")

    def boom(argv, **kwargs):
        raise OSError("Exec format error")

    monkeypatch.setattr(EB.subprocess, "run", boom)

    ok, msg = EB.open_diff(preview)

    assert ok is False
    assert preview.old_path in msg and preview.new_path in msg
    _assert_not_claimed_open(msg)


def test_open_diff_missing_copies_are_reported(work, old_bib, monkeypatch):
    preview = _make(work, old_bib)
    _no_editor(monkeypatch)
    os.remove(preview.new_path)

    ok, msg = EB.open_diff(preview)

    assert ok is False
    assert "不存在" in msg
    assert preview.new_path in msg
    _assert_not_claimed_open(msg)


# ── 7. .tex 走同一条路径 ──────────────────────────────────────────────────
def test_tex_preview_uses_same_layout_and_gate(work, tmp_path):
    tex_old = tmp_path / "paper.tex"
    tex_old.write_text("\\documentclass{article}\n\\begin{document}\nold\n\\end{document}\n",
                       encoding="utf-8", newline="")
    tex_new = "\\documentclass{article}\n\\begin{document}\nnew\n\\end{document}\n"

    preview = _make(work, tex_old, new_text=tex_new, ext=".tex")

    assert preview.ext == ".tex"
    assert os.path.basename(preview.old_path) == "reference.tex"
    assert os.path.basename(preview.new_path) == "reference_proposed.tex"
    assert Path(preview.old_path).read_text(encoding="utf-8") == tex_old.read_text(encoding="utf-8")
    assert Path(preview.new_path).read_text(encoding="utf-8") == tex_new

    meta = json.loads((Path(preview.dir) / EB.PREVIEW_META).read_text(encoding="utf-8"))
    assert meta["ext"] == ".tex"
    assert EB.verify_preview(preview.preview_id, str(work)) == (True, "")

    # .tex 也能被闸门挡住：篡改后 False
    Path(preview.new_path).write_text("tampered\n", encoding="utf-8")
    ok, msg = EB.verify_preview(preview.preview_id, str(work))
    assert ok is False and msg.strip()


# ── 8. 静态守卫：源码里没有禁用写法 ───────────────────────────────────────
def test_module_source_has_no_banned_patterns():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "os.path.relpath" not in source
    assert "import flet" not in source
    assert "from flet" not in source
    assert "import tkinter" not in source
    assert "from tkinter" not in source

    # shell=False 只在 subprocess.run 里显式给一次（另有防误用的静态存在性检查）
    assert "shell=False" in source


def test_module_is_stdlib_only_at_import_time():
    """import 这个模块不该拉起 flet / tkinter（pytest 与无窗口环境都要能跑）。"""
    import sys

    assert "flet" not in sys.modules or True  # 其它测试可能已 import flet，不做全局断言
    tree = __import__("ast").parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in __import__("ast").walk(tree):
        if isinstance(node, __import__("ast").Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, __import__("ast").ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {
        "__future__", "datetime", "hashlib", "json", "os", "shutil",
        "subprocess", "dataclasses",
    }, imported
