"""CLEANUP-R07 契约回归：回收清单版本闸 + 内容摘要闸（T1–T12）。

对应任务卡 §6 与 `$EVID/CONTRACT.md` DRAFT-v2（已 APPROVED，sha256 091b1775…）：

· T1 版本矩阵（C2/C4.3/C4.4）：未知 schema 整批拒绝；schema=1/2 与旧 v1 允许恢复+告警。
· T2 摘要矩阵（C1.2/C3.2–C3.4/C3.8/C4.2）：schema=3 逐文件 sha256；篡改/缺 sha 整批拒绝；
  dry-run 同样预检；`status=restored` 的过期账本重试也过闸；清单+文件同改是已知限制。
· T3 旧批兼容（C2.1/C2.2/C6.3）：v1 字符串/dict files、schema=2、schema=1、v1 升级台账；
  恢复成功后除账本 status/entries 外不改 manifest。
· T4 empty-trash（C5.1–C5.5）：未知版本批次保留+warning 不计 n；缺失/坏 manifest 照删；
  dry-run 同样识别；只有真删空才 rmdir。
· T5 `clean.restore()` 旁路（C7.2–C7.4）：与 CLI 同一套判定，无旧行为旁路。
· T6 既有结构文案逐字回归（C4.4）。
· T7 file+dir 混合：file 校验、dir 不校验（已知限制）。
· T8 中断/重试（C1.2/C3.7）：搬移中断留下的 `moving` 条目带 sha 可过闸；`planned` 不恢复、
  不要求 sha、不判失败。
· T9 写入口覆盖（C1.3）：trash/delivery/workspace/retire_coop_bak/toc_work_clean 五类批次
  schema=3、file 有 sha、dir 无 sha，均可恢复。
· T10 daily 写入口（C1.1/C1.2）：schema=3、file sha 与源内容一致、先落盘后搬移。
· T11 `--json` 顶层字段名/顺序/类型不变量（C8.1）。
· T12 既有断言变更清单（C8.4）：每处改动附理由。

夹具约定（C10.4）：每个用例独立临时目录（套件自建并在用例结束后清理；pytest 下同样自建，
不依赖 `tmp_path` fixture）；初始 sha 自算；零搬运证明=恢复前后 root（排除 `.toolbox-trash`）
与批次树 sha 清单不变；不触碰真实 notes/库/稿件仓。

运行（两种口径等价，均不 skip、不弱化断言）：

    # 1) 原生独立脚本（仓库既有风格；系统 python3.14 无 pytest 也可跑）
    cd Engine && PYTHONPATH=.:vendor python3.14 tests/test_cleanup_manifest_contract.py

    # 2) pytest 收集（44 passed）
    cd Engine && PYTHONPATH=.:vendor python -m pytest tests/test_cleanup_manifest_contract.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ENGINE = pathlib.Path(__file__).resolve().parents[1]
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from slcn import clean as C  # noqa: E402
from slcn import clean_daily as CD  # noqa: E402

TRASH = ".toolbox-trash"
ENV = dict(os.environ)
ENV["PYTHONPATH"] = os.pathsep.join(
    p for p in (str(ENGINE), str(ENGINE / "vendor")) if p
)
ENV["PYTHONDONTWRITEBYTECODE"] = "1"

# C8.1：--json 顶层字段名与相对顺序（不新增顶层字段）。
BASE_KEYS = [
    "ok", "command", "mode", "dryRun", "root", "count", "entryCount", "entries",
    "trashDir", "restoreDir", "failures", "warnings", "error",
]
DAILY_KEYS = BASE_KEYS + ["operationId", "status", "kept"]
RESTORE_KEYS = DAILY_KEYS + ["alreadyRestored", "skipped", "summary"]

# C10.3 T12：本批对既有测试断言的变更清单（每项必须给出理由）。
ASSERTION_CHANGES = {
    "tests/test_clean_daily.py::scope.manifest_daily":
        "C1.1：daily 新写批次 schema 2→3，并新增 file 条目 sha256 格式/值断言。",
    "tests/test_clean_daily.py::same_second.manifests_complete":
        "C1.1：新写批次 schema 2→3；kind/paths/operationId 语义断言未改。",
    "tests/test_b211_cleanup.py::(b) manifest":
        "C1.3：aux `--trash` 新写批次 schema 2→3，并新增 file 条目 sha256 与批次内容一致断言。",
    "tests/test_b211_cleanup.py::(d) delivery manifest":
        "C1.3：delivery 批次 schema 2→3，并新增 29 个 file 条目 sha256 断言（目录条目无 sha）。",
    "tests/test_b211_cleanup.py::(e) workspace manifest":
        "C1.3：workspace 批次 schema 2→3；dir 条目计数断言未改。",
}


# ── 通用夹具/工具 ────────────────────────────────────────────────────────

def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write(root: str, rel: str, data: bytes) -> str:
    full = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)
    return full


def make_root(tmp_path: pathlib.Path, name: str = "root") -> str:
    root = str(tmp_path / name)
    os.makedirs(root, exist_ok=True)
    return root


def tree(root: str) -> dict:
    """root 下（排除 .toolbox-trash）相对路径 → 内容 sha256 清单。"""
    out: dict = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != TRASH)
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            out[os.path.relpath(full, root).replace(os.sep, "/")] = sha_file(full)
    return out


def batch_tree(batch: str) -> dict:
    out: dict = {}
    for dirpath, dirnames, filenames in os.walk(batch):
        dirnames[:] = sorted(dirnames)
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            if fname == "manifest.json" and dirpath == batch:
                continue
            out[os.path.relpath(full, batch).replace(os.sep, "/")] = sha_file(full)
    return out


def read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def load_manifest(batch: str) -> dict:
    return read_json(os.path.join(batch, "manifest.json"))


def run_cli(root: str, *args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(ENGINE / "cleantemp.py"), "--folder", root, *args]
    return subprocess.run(cmd, cwd=str(ENGINE), env=ENV, capture_output=True, text=True)


def cli_json(root: str, *args: str) -> tuple[int, dict, str]:
    proc = run_cli(root, *args, "--json")
    payload: dict = {}
    for line in proc.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
    assert payload, f"stdout 无 JSON：{proc.stdout!r} stderr={proc.stderr!r}"
    return proc.returncode, payload, proc.stderr


def aux_trash(root: str) -> tuple[dict, str]:
    rc, payload, err = cli_json(root, "--trash")
    assert rc == 0, (rc, payload, err)
    batch = payload.get("trashDir") or ""
    assert batch and os.path.isdir(batch)
    return payload, batch


def trash_batches(root: str) -> list[str]:
    base = os.path.join(root, TRASH)
    if not os.path.isdir(base):
        return []
    return sorted(os.path.join(base, n) for n in os.listdir(base)
                  if os.path.isdir(os.path.join(base, n)))


def handcraft(root: str, name: str, manifest: dict,
              files: dict | None = None) -> str:
    """在 root/.toolbox-trash/<name> 手写一个批次（manifest + 文件）；返回批次绝对路径。"""
    batch = os.path.join(root, TRASH, name)
    os.makedirs(batch, exist_ok=True)
    for rel, data in (files or {}).items():
        write(batch, rel, data)
    manifest = dict(manifest)
    manifest.setdefault("root", os.path.abspath(root))
    write_json(os.path.join(batch, "manifest.json"), manifest)
    return batch


def all_file_shas_valid(batch: str, manifest: dict) -> bool:
    for entry in manifest.get("entries") or []:
        if entry.get("type") == "dir":
            if "sha256" in entry:
                return False
            continue
        val = entry.get("sha256")
        if not (isinstance(val, str) and re.fullmatch(r"[0-9a-f]{64}", val)):
            return False
        full = os.path.join(batch, *entry["path"].split("/"))
        if os.path.isfile(full) and sha_file(full) != val:
            return False
    return True


# ── 套件运行器（仓库既有“独立脚本”风格 + pytest 可收集） ─────────────────
# O-01：卡面字面 G2 用系统 python3.14 直接执行 tests/test_*.py，而系统 python 无 pytest；
# 本套件因此不能依赖 pytest 才能运行。这里用与 tests/test_clean_daily.py 同型的自建运行器：
# 每条用例独立临时目录、结束后清理；原生执行时打印 PASS/FAIL 与结尾 SUMMARY；同时每个
# test_* 仍是无 fixture 的零参数函数，venv pytest 可收集运行（44 项）。

TOTAL = 0
PASS = 0
FAIL = 0
TESTS: list = []


def _cleanup_tmp(tmp: pathlib.Path) -> None:
    shutil.rmtree(tmp, ignore_errors=True)
    if tmp.exists():
        for path in tmp.rglob("*"):
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def case(fn):
    """登记用例：自建独立临时目录，结束后清理；返回零参数包装以便 pytest 收集。"""
    takes_tmp = bool(fn.__code__.co_argcount)

    def runner() -> None:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="cln_r07_"))
        try:
            if takes_tmp:
                fn(tmp)
            else:
                fn()
        finally:
            _cleanup_tmp(tmp)

    runner.__name__ = fn.__name__
    runner.__qualname__ = fn.__name__
    runner.__doc__ = fn.__doc__
    TESTS.append(runner)
    return runner


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:240]


def _run_native() -> int:
    global TOTAL, PASS, FAIL
    for runner in TESTS:
        TOTAL += 1
        try:
            runner()
        except AssertionError as exc:
            FAIL += 1
            detail = _one_line(exc)
            print("FAIL  " + runner.__name__ + ("  [" + detail + "]" if detail else ""))
        except Exception as exc:  # noqa: BLE001 —— 原生运行需给出可诊断的失败行
            FAIL += 1
            print("FAIL  " + runner.__name__
                  + "  [" + type(exc).__name__ + ": " + _one_line(exc) + "]")
        else:
            PASS += 1
            print("PASS  " + runner.__name__)
    print("SUMMARY total=%d pass=%d fail=%d" % (TOTAL, PASS, FAIL))
    return 0 if FAIL == 0 else 1


# ── T1 版本矩阵 ──────────────────────────────────────────────────────────

def _t1_unknown_schema_case(tmp_path: pathlib.Path, value) -> None:
    root = make_root(tmp_path)
    write(root, "a.aux", b"alpha\n")
    _, batch = aux_trash(root)
    man = load_manifest(batch)
    original_entries = list(man["entries"])
    man["schema"] = value
    man["futureField"] = {"unknownVersion": True}
    write_json(os.path.join(batch, "manifest.json"), man)
    before = tree(root)
    batch_before = batch_tree(batch)

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 2
    assert payload["status"] == "preflight_failed"
    assert payload["count"] == 0 and payload["entries"] == []
    assert "不认识的回收清单版本（schema=" in payload["error"]
    assert "批次保留、未搬运任何文件" in payload["error"]
    assert tree(root) == before and not os.path.exists(os.path.join(root, "a.aux"))
    assert batch_tree(batch) == batch_before
    assert load_manifest(batch)["entries"] == original_entries


@case
def test_t1_unknown_schema_rejected_int99(tmp_path: pathlib.Path) -> None:
    _t1_unknown_schema_case(tmp_path, 99)


@case
def test_t1_unknown_schema_rejected_str3(tmp_path: pathlib.Path) -> None:
    _t1_unknown_schema_case(tmp_path, "3")


@case
def test_t1_unknown_schema_rejected_none(tmp_path: pathlib.Path) -> None:
    _t1_unknown_schema_case(tmp_path, None)


@case
def test_t1_unknown_schema_rejected_neg1(tmp_path: pathlib.Path) -> None:
    _t1_unknown_schema_case(tmp_path, -1)


@case
def test_t1_unknown_schema_rejected_float3(tmp_path: pathlib.Path) -> None:
    _t1_unknown_schema_case(tmp_path, 3.0)


@case
def test_t1_unknown_schema_rejected_bool_true(tmp_path: pathlib.Path) -> None:
    _t1_unknown_schema_case(tmp_path, True)


@case
def test_t1_unknown_schema_handcraft_single_file(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = handcraft(root, "handcraft-99", {
        "schema": 99,
        "createdAt": "2026_11_10_000000",
        "mode": "aux",
        "entries": [{"path": "x.aux", "type": "file", "status": "moved"}],
    }, {"x.aux": b"x\n"})
    before = tree(root)

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 2 and payload["count"] == 0
    assert "schema=99" in payload["error"]
    assert tree(root) == before


def _t1_legacy_schema_case(tmp_path: pathlib.Path, value, needle) -> None:
    root = make_root(tmp_path)
    write(root, "a.aux", b"legacy-alpha\n")
    initial_sha = sha_file(os.path.join(root, "a.aux"))
    _, batch = aux_trash(root)
    man = load_manifest(batch)
    man["schema"] = value
    for entry in man["entries"]:
        entry.pop("sha256", None)
    write_json(os.path.join(batch, "manifest.json"), man)

    rc, payload, stderr = cli_json(root, "--restore", batch, "--dry-run")
    assert rc == 0
    assert any("RESTORE_UNVERIFIED" in w for w in payload["warnings"])

    rc, payload, stderr = cli_json(root, "--restore", batch)
    assert rc == 0 and payload["status"] == "success"
    assert payload["count"] == 1
    assert needle in " ".join(payload["warnings"])
    assert "RESTORE_UNVERIFIED" in stderr
    assert sha_file(os.path.join(root, "a.aux")) == initial_sha


@case
def test_t1_legacy_schema1_allowed_with_warning(tmp_path: pathlib.Path) -> None:
    _t1_legacy_schema_case(tmp_path, 1, "schema=1")


@case
def test_t1_legacy_schema2_allowed_with_warning(tmp_path: pathlib.Path) -> None:
    _t1_legacy_schema_case(tmp_path, 2, "schema=2")


def _t1_legacy_v1_files_case(tmp_path: pathlib.Path, files_form: str) -> None:
    root = make_root(tmp_path)
    entry = {"path": "x.aux"} if files_form == "dict" else "x.aux"
    batch = handcraft(root, f"v1-{files_form}", {
        "createdAt": "2026_11_10_000000",
        "files": [entry],
    }, {"x.aux": b"v1-bytes\n"})
    initial = sha_bytes(b"v1-bytes\n")
    before = tree(root)

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 0 and payload["status"] == "success" and payload["count"] == 1
    assert any("RESTORE_UNVERIFIED" in w and "schema=v1" in w
               for w in payload["warnings"])
    assert tree(root)["x.aux"] == initial
    # v1 首恢复升级台账：manifest 出现 entries，但 schema 仍缺失、files 保留。
    upgraded = load_manifest(batch)
    assert "schema" not in upgraded and upgraded["files"] == [entry]
    assert upgraded["entries"][0]["path"] == "x.aux"


@case
def test_t1_legacy_v1_files_string_form_allowed(tmp_path: pathlib.Path) -> None:
    _t1_legacy_v1_files_case(tmp_path, "string")


@case
def test_t1_legacy_v1_files_dict_form_allowed(tmp_path: pathlib.Path) -> None:
    _t1_legacy_v1_files_case(tmp_path, "dict")


@case
def test_t1_legacy_v1_upgraded_ledger_still_legacy(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = handcraft(root, "v1-upgraded", {
        "createdAt": "2026_11_10_000000",
        "files": ["x.aux"],
        "entries": [{"path": "x.aux", "type": "file", "status": "moved"}],
    }, {"x.aux": b"up\n"})

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 0 and payload["count"] == 1
    assert any("schema=v1" in w for w in payload["warnings"])


@case
def test_t1_v3_planned_failed_entries_need_no_sha_and_never_restore(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = handcraft(root, "v3-planned", {
        "schema": 3,
        "createdAt": "2026_11_10_000000",
        "mode": "aux",
        "entries": [
            {"path": "a.aux", "type": "file", "status": "moving",
             "sha256": sha_bytes(b"A")},
            {"path": "b.aux", "type": "file", "status": "planned"},
            {"path": "c.aux", "type": "file", "status": "failed"},
        ],
    }, {"a.aux": b"A", "b.aux": b"B", "c.aux": b"C"})
    batch_before = batch_tree(batch)

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 0 and payload["status"] == "success" and payload["count"] == 1
    assert tree(root) == {"a.aux": sha_bytes(b"A")}
    # planned/failed 从不恢复，原样留在批次里（moving 的 a.aux 已搬回 root）。
    assert batch_tree(batch) == {"b.aux": sha_bytes(b"B"), "c.aux": sha_bytes(b"C")}
    assert batch_before == {"a.aux": sha_bytes(b"A"), "b.aux": sha_bytes(b"B"),
                            "c.aux": sha_bytes(b"C")}
    assert payload["warnings"] == []


# ── T2 摘要矩阵 ──────────────────────────────────────────────────────────

@case
def test_t2_v3_normal_restore_verifies_sha(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    files = {"a.aux": b"alpha\n", "sub/b.out": b"beta\n"}
    initial = {rel: sha_bytes(data) for rel, data in files.items()}
    for rel, data in files.items():
        write(root, rel, data)
    _, batch = aux_trash(root)
    man = load_manifest(batch)
    assert man["schema"] == 3 and all_file_shas_valid(batch, man)

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 0 and payload["status"] == "success" and payload["count"] == 2
    assert payload["warnings"] == []
    assert tree(root) == initial
    assert all(e["status"] == "restored" for e in load_manifest(batch)["entries"])


def _t2_tampered_file_case(tmp_path: pathlib.Path, mode: str) -> None:
    root = make_root(tmp_path)
    write(root, "a.aux", b"0123456789\n")
    _, batch = aux_trash(root)
    target = os.path.join(batch, "a.aux")
    original = open(target, "rb").read()
    tampered = (b"abcdefghij\n" if mode == "same-len"
                else original + b"tampered-extension\n")
    original_sha = sha_bytes(original)
    assert len(tampered) != len(original) or mode == "same-len"
    with open(target, "wb") as fh:
        fh.write(tampered)
    before = tree(root)
    batch_before = batch_tree(batch)

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 2 and payload["status"] == "preflight_failed"
    assert payload["count"] == 0 and payload["entries"] == []
    assert "回收清单内容摘要不符" in payload["error"]
    assert "sha256" in payload["error"] and "a.aux" in payload["error"]
    assert tree(root) == before and batch_tree(batch) == batch_before
    # 清单未被同步改写：摘要闸以清单 sha256 为对照（aux 条目无 status 字段）。
    assert load_manifest(batch)["entries"][0]["sha256"] == original_sha


@case
def test_t2_tampered_file_same_len_rejected_zero_move(tmp_path: pathlib.Path) -> None:
    _t2_tampered_file_case(tmp_path, "same-len")


@case
def test_t2_tampered_file_diff_len_rejected_zero_move(tmp_path: pathlib.Path) -> None:
    _t2_tampered_file_case(tmp_path, "diff-len")


@case
def test_t2_dry_run_tamper_rejected_without_will_restore(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    write(root, "a.aux", b"dry\n")
    _, batch = aux_trash(root)
    write(batch, "a.aux", b"DRY\n")
    before = tree(root)

    proc = run_cli(root, "--restore", batch, "--dry-run", "--json")

    assert proc.returncode == 2
    assert "Will restore:" not in proc.stdout and "Will restore:" not in proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["status"] == "preflight_failed" and payload["count"] == 0
    assert tree(root) == before


@case
def test_t2_retry_with_stale_restored_ledger_still_gated(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    write(root, "a.aux", b"retry\n")
    _, batch = aux_trash(root)
    man = load_manifest(batch)
    man["entries"][0]["status"] = "restored"  # 账本过期：目标其实已缺失
    write_json(os.path.join(batch, "manifest.json"), man)
    write(batch, "a.aux", b"TAMPERED\n")
    before = tree(root)

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 2 and payload["status"] == "preflight_failed" and payload["count"] == 0
    assert "a.aux" in payload["error"]
    assert tree(root) == before


@case
def test_t2_missing_sha_rejected(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    write(root, "a.aux", b"missing\n")
    _, batch = aux_trash(root)
    man = load_manifest(batch)
    man["entries"][0].pop("sha256", None)
    write_json(os.path.join(batch, "manifest.json"), man)
    before = tree(root)

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 2 and payload["status"] == "preflight_failed" and payload["count"] == 0
    assert "sha256" in payload["error"] and "a.aux" in payload["error"]
    assert tree(root) == before


@case
def test_t2_manifest_and_file_tamper_is_known_limitation(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    write(root, "a.aux", b"known\n")
    _, batch = aux_trash(root)
    write(batch, "a.aux", b"KNOWN\n")
    man = load_manifest(batch)
    man["entries"][0]["sha256"] = sha_bytes(b"KNOWN\n")
    write_json(os.path.join(batch, "manifest.json"), man)

    rc, payload, _ = cli_json(root, "--restore", batch)

    # C12 已知限制：摘要无签名，清单与文件同改不可检测（不判 FAIL，文档写明）。
    assert rc == 0 and payload["status"] == "success" and payload["count"] == 1
    assert sha_file(os.path.join(root, "a.aux")) == sha_bytes(b"KNOWN\n")


# ── T3 旧批兼容 ──────────────────────────────────────────────────────────

@case
def test_t3_schema2_restore_and_manifest_kept(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = handcraft(root, "v2", {
        "schema": 2,
        "createdAt": "2026_11_10_000000",
        "mode": "aux",
        "entries": [{"path": "paper.bbl", "type": "file", "status": "moved"}],
    }, {"paper.bbl": b"v2-bbl\n"})
    initial = sha_bytes(b"v2-bbl\n")

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 0 and payload["status"] == "success" and payload["count"] == 1
    assert any("schema=2" in w and "RESTORE_UNVERIFIED" in w for w in payload["warnings"])
    assert tree(root)["paper.bbl"] == initial
    man = load_manifest(batch)
    assert man["schema"] == 2 and man["entries"][0]["status"] == "restored"


@case
def test_t3_clean_restore_bypass_legacy_warns(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = handcraft(root, "v1-direct", {
        "createdAt": "2026_11_10_000000",
        "files": ["x.aux"],
    }, {"x.aux": b"direct\n"})
    logs: list[str] = []

    n, paths = C.restore(root, batch, log=logs.append)

    assert n == 1 and paths == ["x.aux"]
    assert any("RESTORE_UNVERIFIED" in line for line in logs)


# ── T4 empty-trash ───────────────────────────────────────────────────────

def _four_batches(root: str) -> tuple[str, str]:
    """v1 / v2 / v3 / schema=99 四类批次；返回 (v3批次, 未知批次)。"""
    handcraft(root, "old-v1", {
        "createdAt": "2026_11_10_000000",
        "files": ["v1.aux"],
    }, {"v1.aux": b"v1\n"})
    handcraft(root, "old-v2", {
        "schema": 2,
        "createdAt": "2026_11_10_000000",
        "mode": "aux",
        "entries": [{"path": "v2.aux", "type": "file", "status": "moved"}],
    }, {"v2.aux": b"v2\n"})
    write(root, "v3.aux", b"v3\n")
    _, v3_batch = aux_trash(root)
    unknown = handcraft(root, "unknown-99", {
        "schema": 99,
        "createdAt": "2026_11_10_000000",
        "mode": "aux",
        "entries": [{"path": "u.aux", "type": "file", "status": "moved"}],
    }, {"u.aux": b"unknown\n"})
    return v3_batch, unknown


@case
def test_t4_empty_trash_keeps_unknown_schema_batch(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    _, unknown = _four_batches(root)
    unknown_tree = batch_tree(unknown)

    rc, payload, stderr = cli_json(root, "--empty-trash")

    assert rc == 0 and payload["count"] == 3
    assert any("保留未知版本的回收目录" in w and "schema=99" in w
               for w in payload["warnings"])
    assert any("保留未知版本的回收目录" in line for line in stderr.splitlines())
    assert os.path.isdir(unknown) and batch_tree(unknown) == unknown_tree
    assert os.path.isdir(os.path.join(root, TRASH))  # 有保留批次 → 不 rmdir
    assert len(trash_batches(root)) == 1


@case
def test_t4_empty_trash_normal_removes_all_and_rmdir(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    _four_batches(root)
    # 删掉未知批次，只留 v1/v2/v3。
    shutil.rmtree(os.path.join(root, TRASH, "unknown-99"))

    rc, payload, _ = cli_json(root, "--empty-trash")

    assert rc == 0 and payload["count"] == 3 and payload["warnings"] == []
    assert not os.path.exists(os.path.join(root, TRASH))


@case
def test_t4_empty_trash_dry_run_keeps_unknown_and_counts_rest(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    _, unknown = _four_batches(root)
    unknown_tree = batch_tree(unknown)

    rc, payload, _ = cli_json(root, "--empty-trash", "--dry-run")

    assert rc == 0 and payload["count"] == 3
    assert any("schema=99" in w for w in payload["warnings"])
    assert os.path.isdir(unknown) and batch_tree(unknown) == unknown_tree
    assert len(trash_batches(root)) == 4


# ── T5 clean.restore() 旁路 ──────────────────────────────────────────────

@case
def test_t5_restore_bypass_rejects_tampered_v3(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    write(root, "a.aux", b"bypass\n")
    _, batch = aux_trash(root)
    write(batch, "a.aux", b"BYPASS\n")
    before = tree(root)
    logs: list[str] = []

    n, paths = C.restore(root, batch, log=logs.append)

    assert n == 0 and paths == []
    assert tree(root) == before
    assert any("sha256" in line for line in logs)


@case
def test_t5_restore_bypass_rejects_unknown_schema(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = handcraft(root, "bypass-99", {
        "schema": 99,
        "createdAt": "2026_11_10_000000",
        "mode": "aux",
        "entries": [{"path": "a.aux", "type": "file", "status": "moved"}],
    }, {"a.aux": b"x\n"})

    n, paths = C.restore(root, batch, log=lambda _s: None)

    assert n == 0 and paths == []
    assert not os.path.exists(os.path.join(root, "a.aux"))


@case
def test_t5_restore_bypass_unreadable_manifest_keeps_old_behavior(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = os.path.join(root, TRASH, "no-manifest")
    os.makedirs(batch)
    logs: list[str] = []

    n, paths = C.restore(root, batch, log=logs.append)

    assert n == 0 and paths == []
    assert any("读不到回收清单" in line for line in logs)


# ── T6 结构文案逐字回归 ──────────────────────────────────────────────────

@case
def test_t6_no_entries_no_files_error_verbatim(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = handcraft(root, "struct-none", {
        "schema": 99,
        "createdAt": "2026_11_10_000000",
    })
    rc, payload, _ = cli_json(root, "--restore", batch)
    assert rc == 2 and payload["status"] == "preflight_failed"
    assert payload["error"] == "不认识的回收清单（既无 entries 也无 files）"
    assert payload["count"] == 0


@case
def test_t6_missing_and_bad_manifest_error(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    empty = os.path.join(root, TRASH, "empty-batch")
    os.makedirs(empty)
    rc, payload, _ = cli_json(root, "--restore", empty)
    assert rc == 2
    assert "回收清单缺失或不是合法 JSON" in payload["error"]

    batch = os.path.join(root, TRASH, "bad-json")
    os.makedirs(batch)
    with open(os.path.join(batch, "manifest.json"), "w", encoding="utf-8") as fh:
        fh.write("{")
    rc, payload, _ = cli_json(root, "--restore", batch)
    assert rc == 2 and "回收清单缺失或不是合法 JSON" in payload["error"]


@case
def test_t6_unsafe_paths_rejected_verbatim(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    valid_sha = sha_bytes(b"x")
    for name, rel in [("traversal", "sub/../victim.aux"), ("absolute", "/tmp/victim.aux")]:
        batch = handcraft(root, name, {
            "schema": 3,
            "createdAt": "2026_11_10_000000",
            "mode": "aux",
            "entries": [{"path": rel, "type": "file", "status": "moved",
                         "sha256": valid_sha}],
        }, {"victim.aux": b"x"})
        before = tree(root)
        rc, payload, _ = cli_json(root, "--restore", batch)
        assert rc == 2 and payload["count"] == 0
        assert "清单条目不安全" in payload["error"]
        assert "路径穿越" in payload["error"] or "绝对路径" in payload["error"]
        assert tree(root) == before


# ── T7 file + dir 混合 ───────────────────────────────────────────────────

def _mixed_batch(root: str, name: str, file_sha: str) -> str:
    return handcraft(root, name, {
        "schema": 3,
        "createdAt": "2026_11_10_000000",
        "mode": "aux",
        "entries": [
            {"path": "a.aux", "type": "file", "status": "moved", "sha256": file_sha},
            {"path": "d", "type": "dir", "status": "moved"},
        ],
    }, {"a.aux": b"mixed-file\n", "d/x.txt": b"dir-payload\n"})


@case
def test_t7_mixed_restore_file_checked_dir_not(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = _mixed_batch(root, "mixed-ok", sha_bytes(b"mixed-file\n"))

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 0 and payload["status"] == "success" and payload["count"] == 2
    assert open(os.path.join(root, "a.aux"), "rb").read() == b"mixed-file\n"
    assert open(os.path.join(root, "d", "x.txt"), "rb").read() == b"dir-payload\n"


@case
def test_t7_mixed_tampered_file_rejects_whole_batch(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = _mixed_batch(root, "mixed-tamper", sha_bytes(b"mixed-file\n"))
    write(batch, "a.aux", b"MIXED-FILE\n")
    before = tree(root)

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 2 and payload["status"] == "preflight_failed" and payload["count"] == 0
    assert tree(root) == before
    assert not os.path.exists(os.path.join(root, "d"))


@case
def test_t7_dir_contents_not_checked_known_limitation(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = _mixed_batch(root, "mixed-dir", sha_bytes(b"mixed-file\n"))
    write(batch, "d/x.txt", b"DIR-TAMPERED\n")

    rc, payload, _ = cli_json(root, "--restore", batch)

    # C12 已知限制：dir 条目无摘要，内容变化不可检测（文档写明）。
    assert rc == 0 and payload["count"] == 2
    assert open(os.path.join(root, "d", "x.txt"), "rb").read() == b"DIR-TAMPERED\n"


# ── T8 中断/重试（moving/planned） ───────────────────────────────────────

@case
def test_t8_interrupted_moving_entry_has_sha_and_is_retryable(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = handcraft(root, "interrupt", {
        "schema": 3,
        "createdAt": "2026_11_10_000000",
        "mode": "daily",
        "kind": "daily",
        "status": "cancelled",
        "entries": [
            {"path": "a.aux", "type": "file", "status": "moving",
             "sha256": sha_bytes(b"interrupted-a\n")},
            {"path": "b.aux", "type": "file", "status": "planned"},
        ],
        "intent": [
            {"path": "a.aux", "type": "file", "status": "moved"},
            {"path": "b.aux", "type": "file", "status": "planned"},
        ],
    }, {"a.aux": b"interrupted-a\n", "b.aux": b"planned-b\n"})

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 0 and payload["status"] == "success" and payload["count"] == 1
    assert tree(root) == {"a.aux": sha_bytes(b"interrupted-a\n")}
    man = load_manifest(batch)
    statuses = {e["path"]: e["status"] for e in man["entries"]}
    assert statuses == {"a.aux": "restored", "b.aux": "planned"}


# ── T9 写入口覆盖（C1.3） ────────────────────────────────────────────────

def _assert_batch_contract(batch: str) -> dict:
    man = load_manifest(batch)
    assert man["schema"] == 3
    assert all_file_shas_valid(batch, man)
    return man


@case
def test_t9_trash_aux_writes_schema3_with_sha(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    write(root, "a.aux", b"aux-write\n")
    n, batch = C.trash(root, log=lambda _s: None)
    assert n == 1 and batch
    man = _assert_batch_contract(batch)
    assert man["entries"][0]["sha256"] == sha_bytes(b"aux-write\n")


@case
def test_t9_delivery_writer_schema3_with_sha(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    write(root, "main.tex", b"\\documentclass{article}\n")
    write(root, "junk.aux", b"delivery-junk\n")
    n, batch = C.delivery_clean(root, log=lambda _s: None)
    assert n >= 1 and batch
    man = _assert_batch_contract(batch)
    assert any(e["path"] == "junk.aux" and e.get("sha256") for e in man["entries"])
    n2, paths = C.restore(root, batch, log=lambda _s: None)
    assert n2 == n and sorted(paths) == sorted(e["path"] for e in man["entries"]
                                               if e.get("type") != "dir" and e.get("path") == "junk.aux")


@case
def test_t9_workspace_writer_schema3_with_sha(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    write(root, "x.bak", b"workspace-junk\n")
    n, batch = C.workspace_clean(root, log=lambda _s: None)
    assert n >= 1 and batch
    man = _assert_batch_contract(batch)
    assert any(e["path"] == "x.bak" and e.get("sha256") for e in man["entries"])


@case
def test_t9_retire_coop_bak_writes_dir_entry_without_sha(tmp_path: pathlib.Path) -> None:
    base = str(tmp_path / "delivery")
    final = os.path.join(base, "2020_01_01")
    backup = final + ".coop-bak-20200101_000000"
    write(final, "main.tex", b"\\documentclass{article}\n")
    write(backup, "old.tex", b"old\n")

    dst = C.retire_coop_bak(final, backup)

    batch = os.path.dirname(dst)
    man = _assert_batch_contract(batch)
    entry = [e for e in man["entries"] if e["path"] == os.path.basename(backup)][0]
    assert entry["type"] == "dir" and "sha256" not in entry


@case
def test_t9_toc_work_writer_schema3_with_sha(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    write(root, ".toolbox-tmp/run.tmp", b"toc-temp\n")
    n, batch = C.toc_work_clean(root, log=lambda _s: None)
    assert n >= 1 and batch
    man = _assert_batch_contract(batch)
    assert any(e["path"].endswith("run.tmp") and e.get("sha256") for e in man["entries"])


# ── T10 daily 写入口 ─────────────────────────────────────────────────────

@case
def test_t10_daily_writes_schema3_and_source_sha(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    files = {"a.aux": b"daily-a\n", "sub/c.aux": b"daily-c\n"}
    for rel, data in files.items():
        write(root, rel, data)
    initial = {rel: sha_bytes(data) for rel, data in files.items()}

    rc, payload, _ = cli_json(root, "--mode", "daily")

    assert rc == 0 and payload["status"] == "success" and payload["count"] == 2
    batch = payload["trashDir"]
    man = _assert_batch_contract(batch)
    by_path = {e["path"]: e for e in man["entries"]}
    assert set(by_path) == set(initial)
    assert {rel: e["sha256"] for rel, e in by_path.items()} == initial
    assert man["kind"] == "daily" and man["status"] == "success"


# ── T11 JSON 不变量 ──────────────────────────────────────────────────────

@case
def test_t11_json_top_level_keys_and_order(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    write(root, "a.aux", b"json\n")

    rc, payload, _ = cli_json(root, "--trash")
    assert rc == 0
    assert list(payload.keys()) == BASE_KEYS
    assert isinstance(payload["ok"], bool) and isinstance(payload["count"], int)
    assert isinstance(payload["warnings"], list) and isinstance(payload["entries"], list)

    batch = payload["trashDir"]
    rc, payload, _ = cli_json(root, "--restore", batch)
    assert rc == 0
    assert list(payload.keys()) == RESTORE_KEYS
    assert isinstance(payload["summary"], dict) and "restored" in payload["summary"]

    rc, payload, _ = cli_json(root, "--empty-trash")
    assert rc == 0
    assert list(payload.keys()) == BASE_KEYS


@case
def test_t11_warnings_array_only_gains_contract_texts(tmp_path: pathlib.Path) -> None:
    root = make_root(tmp_path)
    batch = handcraft(root, "warn-v1", {
        "createdAt": "2026_11_10_000000",
        "files": ["x.aux"],
    }, {"x.aux": b"w\n"})

    rc, payload, _ = cli_json(root, "--restore", batch)

    assert rc == 0
    assert len(payload["warnings"]) == 1
    assert payload["warnings"][0].startswith("RESTORE_UNVERIFIED: ")


# ── T12 断言变更清单 ─────────────────────────────────────────────────────

@case
def test_t12_assertion_change_log_has_reasons() -> None:
    assert ASSERTION_CHANGES
    for name, reason in ASSERTION_CHANGES.items():
        assert name and isinstance(reason, str) and reason.strip()


# ── 套件自跑入口（run_engine_tests.sh full 会把本文件当套件直接执行） ──────
# 直接执行：自建运行器逐条跑 44 个用例，打印 PASS/FAIL 与 SUMMARY；失败 rc=1。
# pytest 收集：44 个零参数 test_* 包装函数，等价于 pytest 逐条运行。

if __name__ == "__main__":
    raise SystemExit(_run_native())
