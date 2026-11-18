"""对比预览（DESIGN §10）：给 Bib AI 处理结果做双文件 diff，**只读**用户原文件。

一次预览 = 一个隐藏子目录 ``<work_dir>/.diff/<preview_id>/``，里面放两份**标准文件名**
的副本（``reference.bib`` / ``reference_proposed.bib``，或同名 ``.tex``）与一份
``preview.json`` 元数据。应用前必须用 :func:`verify_preview` 重算两侧 sha256 与元数据
比对 —— **过期副本一律拒绝**，这是「预览作废后不能再 apply」的唯一闸门。

硬约束（逐条对应设计稿）：

* ``preview_id`` 由两侧内容 sha256 派生（``"p" + 12 hex``）：同内容重复预览复用同一
  目录，不堆积临时目录；元数据里同时记两侧 sha，便于事后判定副本是否被改过。
* 只写自己 ``preview_id`` 子目录里的三个文件。``.diff`` 已存在但不是目录 → 报错，
  **不删不改**；``.diff`` 下其它目录、其它 preview 的内容一律不碰。
* 打开对比只用列表 argv、``shell=False``，绝不做字符串拼接（防注入）；找不到编辑器 /
  启动抛异常 / 非零退出码 → ``(False, 中文原因)``，且**必须把两个副本路径写进文案**。
* **绝不**把「已给出路径」说成「打开成功」：只有编辑器真的执行成功（rc == 0）才返回
  ``True``。失败文案里不会出现任何「打开成功」式的措辞。
* 纯标准库（os / json / hashlib / shutil / subprocess / datetime / dataclasses），
  不依赖 flet / tkinter —— pytest 与无窗口环境都能直接 import。
* 所有文本读写显式 ``encoding="utf-8"``；写副本用 ``newline=""`` 保字节稳定
  （调用方给什么字节就落什么字节，不做换行翻译）。

本轮**不做** ``code -m`` 三方合并：只做两文件 diff。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass

#: 预览元数据文件名（位于 ``<work_dir>/.diff/<preview_id>/`` 里）
PREVIEW_META = "preview.json"

#: 隐藏子目录名：预览副本一律放在 ``<work_dir>/.diff/`` 下，不进产物目录本体
DIFF_DIRNAME = ".diff"

#: 初排交付前的对比副本目录名（合同 §3：``<work_dir>/compare/``）。
#: 与 ``.diff`` 的区别只是**显式传参**：``preview_dir(work, COMPARE_DIRNAME)``。
#: 为什么另起一个名字而不是改 ``DIFF_DIRNAME``：既有 26 个 editor_bridge 测试把
#: ``.diff`` 钉成模块默认值（``test_editor_bridge_20261117.py``），改默认值等于
#: 推翻已验收的契约；而交付路径要求副本落 ``compare/``，所以由调用方显式选目录。
COMPARE_DIRNAME = "compare"

#: 新版副本改名后缀：``reference.bib`` → ``reference_proposed.bib``
PROPOSED_SUFFIX = "_proposed"

#: 默认主文件名（扩展名由 ``ext`` 决定）
DEFAULT_STEM = "reference"

#: 默认扩展名
DEFAULT_EXT = ".bib"

#: 编辑器命令行名（``shutil.which`` 探测）
CODE_COMMANDS = ("code",)

#: ``shutil.which`` 找不到时的补充探测路径（macOS / Homebrew / Linux 常见装法）
CODE_PATHS = (
    "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
    "/usr/local/bin/code",
    "/opt/homebrew/bin/code",
)

#: 启动 ``code --diff`` 的等待上限（秒）：真实使用里它一般立刻返回
OPEN_TIMEOUT = 20.0

_SHA_CHARS = "0123456789abcdef"
_TOKEN_CHARS = ("0123456789abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ_-")


@dataclass
class Preview:
    """一次对比预览的落盘结果（两份副本 + 元数据都在 :attr:`dir` 里）。"""

    preview_id: str        # 稳定唯一 id（内容派生："p" + 12 hex）
    dir: str               # 预览子目录绝对路径
    old_path: str          # 旧版副本（标准文件名，如 reference.bib）
    new_path: str          # 新版副本（如 reference_proposed.bib）
    old_sha256: str        # 旧版副本字节 sha256
    new_sha256: str        # 新版副本字节 sha256
    ext: str               # ".bib" / ".tex"
    created_at: str        # ISO8601 本地时间


# ── 小工具：校验 / 求 sha ──────────────────────────────────────────────────
def _is_sha256(text: str) -> bool:
    """是不是 64 位小写十六进制摘要（元数据可信度检查用）。"""
    return len(text) == 64 and all(ch in _SHA_CHARS for ch in text)


def _is_token(text: str, *, limit: int = 64) -> bool:
    """文件名片段是否安全：只允许 ASCII 字母数字与 ``_``/``-``，且不能为空。

    这条是路径穿越的正门防守：``preview_id`` 来自调用方（也可能来自磁盘上的旧值），
    一旦允许 ``.`` / ``/`` / ``\\``，就能拼出 ``.diff`` 之外的路径。
    """
    if not text or len(text) > limit:
        return False
    return all(ch in _TOKEN_CHARS for ch in text)


def _clean_ext(ext: str) -> str:
    """规范化扩展名：空 → ``.bib``；缺前导点补上；只允许「点 + 字母数字」。"""
    text = str(ext or "").strip()
    if not text:
        return DEFAULT_EXT
    if not text.startswith("."):
        text = "." + text
    body = text[1:]
    if not body or not body.isascii() or not body.isalnum():
        raise ValueError(f"扩展名不合法：{ext!r}（只支持 .bib / .tex 这类「点 + 字母数字」）。")
    return "." + body.lower()


def _clean_stem(stem: str) -> str:
    """规范化主文件名：空 → ``reference``；只允许安全字符。"""
    text = str(stem or "").strip()
    if not text:
        return DEFAULT_STEM
    if not _is_token(text):
        raise ValueError(
            f"主文件名不合法：{stem!r}（只允许 ASCII 字母、数字、下划线与短横，最长 64）。")
    return text


def file_sha256(path: str) -> str:
    """文件字节 sha256；读不到抛 ``OSError``（由调用方翻成中文原因）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preview_id_for(old_sha256: str, new_sha256: str) -> str:
    """由两侧内容摘要派生预览 id（``"p" + 12 hex``）。

    纯内容派生 → 同内容重复预览复用同一目录；两侧任一字节变化都会换 id，
    旧目录因此不会被误当成新预览。
    """
    seed = f"{old_sha256}\n{new_sha256}".encode("utf-8")
    return "p" + hashlib.sha256(seed).hexdigest()[:12]


def preview_dir(work_dir: str, dir_name: str = DIFF_DIRNAME) -> str:
    """``<work_dir>/<dir_name>`` 的绝对路径（默认 ``.diff``）。

    ``work_dir`` 为空 → 抛 ``ValueError``。``dir_name`` 只接受单个安全目录名
    （字母/数字/``_``/``-``/前导点），不给就沿用 ``.diff`` —— 既有 26 个测试
    钉的是这个默认值。初排交付的对比副本由调用方显式传
    :data:`COMPARE_DIRNAME`（``compare``），这样「预览目录」与「交付对比目录」
    两个语义各有各的常量，不会互相冒充。
    """
    base = str(work_dir or "").strip()
    if not base:
        raise ValueError("没有产物目录（work_dir 为空），无法定位预览目录。")
    name = _clean_dirname(dir_name)
    return os.path.join(os.path.abspath(base), name)


def _clean_dirname(dir_name: str) -> str:
    """校验子目录名：只允许 ``.`` / ASCII 字母数字 / ``_`` / ``-``，不能是 ``..``。

    与 :func:`_clean_stem` 同一套防穿越思路：目录名一旦能带 ``/`` 或 ``..``，
    副本就会跑到 ``work_dir`` 之外（甚至落进 ``stage/``），合同 §3 明令禁止。
    """
    text = str(dir_name or "").strip()
    if not text:
        return DIFF_DIRNAME
    body = text.lstrip(".")
    if (not body or body in (".", "..") or "/" in text or "\\" in text
            or not body.isascii() or not all(ch.isalnum() or ch in "_-" for ch in body)):
        raise ValueError(
            f"预览子目录名不合法：{dir_name!r}（只允许字母、数字、下划线与短横，"
            "可带前导点；不允许路径分隔符）。")
    return text


def _require_dir(path: str, label: str) -> None:
    if not os.path.isdir(path):
        raise ValueError(f"{label}不存在或不是目录：{path}（没有替你新建，请检查路径）。")


def _ensure_subdir(parent: str, label: str) -> None:
    """安全地确保 ``parent`` 是目录：已存在但不是目录 → 报错，**不删不改**。"""
    if os.path.lexists(parent) and not os.path.isdir(parent):
        raise ValueError(
            f"{label} {parent} 已存在但不是目录（可能是你自己建的同名文件）。"
            "没有删除或覆盖它，请先自行处理后再生成预览。")
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"建不了目录 {parent}：{exc}") from exc


def make_preview(*, old_file: str, new_text: str, ext: str = DEFAULT_EXT,
                 work_dir: str, stem: str = DEFAULT_STEM,
                 dir_name: str = DIFF_DIRNAME) -> Preview:
    """把「旧版文件 + 新版文本」写成一对标准文件名副本，返回 :class:`Preview`。

    只读 ``old_file``（一个字节都不写它），只写
    ``<work_dir>/<dir_name>/<preview_id>/{<stem><ext>, <stem>_proposed<ext>, preview.json}``。

    ``dir_name`` 默认 ``.diff``（既有契约）；初排交付传 ``compare``（合同 §3：
    对比副本必须落 ``<work_dir>/compare/``，绝不落进 ``stage/`` 或最终交付目录）。

    失败一律抛 ``ValueError``（消息是给用户看的中文原因），**绝不静默成功**：
    参数不合法、产物目录不存在、目录被一个同名文件占着、读写失败、复制后
    内容对不上，各走各自的消息。
    """
    source = str(old_file or "").strip()
    if not source:
        raise ValueError("没有原文件路径（old_file 为空），无法生成对比预览。")
    if not isinstance(new_text, str):
        raise ValueError(f"新版内容必须是文本（拿到 {type(new_text).__name__}），无法生成对比预览。")
    suffix = _clean_ext(ext)
    name = _clean_stem(stem)

    base_dir = str(work_dir or "").strip()
    if not base_dir:
        raise ValueError("没有产物目录（work_dir 为空），无法生成对比预览。")
    base_dir = os.path.abspath(base_dir)
    _require_dir(base_dir, "产物目录")

    if not os.path.isfile(source):
        raise ValueError(f"原文件不存在或不是普通文件：{source}（预览只读它，不会新建）。")
    source = os.path.abspath(source)

    try:
        with open(source, "rb") as handle:
            old_bytes = handle.read()
    except OSError as exc:
        raise ValueError(f"读不了原文件 {source}：{exc}") from exc
    old_sha = hashlib.sha256(old_bytes).hexdigest()

    try:
        new_bytes = new_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"新版内容含无法按 UTF-8 编码的字符：{exc}") from exc
    new_sha = hashlib.sha256(new_bytes).hexdigest()

    diff_dir = preview_dir(base_dir, dir_name)
    _ensure_subdir(diff_dir, "预览根目录")
    preview_id = preview_id_for(old_sha, new_sha)
    target = os.path.join(diff_dir, preview_id)
    _ensure_subdir(target, "本次预览目录")

    old_path = os.path.join(target, name + suffix)
    new_path = os.path.join(target, name + PROPOSED_SUFFIX + suffix)
    # 绝不写用户原文件：万一算出来的目标路径就是它自己，直接拒绝。
    if os.path.abspath(old_path) == source:
        raise ValueError(
            f"预览副本的目标路径与原文件相同（{source}），拒绝覆盖你的原文件；"
            "请先把原文件复制到别处再生成预览。")

    try:
        with open(old_path, "wb") as handle:
            handle.write(old_bytes)
    except OSError as exc:
        raise ValueError(f"写不了旧版副本 {old_path}：{exc}") from exc

    try:
        with open(new_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(new_text)
    except OSError as exc:
        raise ValueError(f"写不了新版副本 {new_path}：{exc}") from exc

    # 落盘后再算一次：元数据里的 sha 描述的是磁盘上的字节，而不是内存里的假设。
    try:
        written_old = file_sha256(old_path)
        written_new = file_sha256(new_path)
    except OSError as exc:
        raise ValueError(f"写完副本后读不回来（{exc}），已放弃本次预览。") from exc
    if written_old != old_sha:
        raise ValueError(f"旧版副本落盘后内容对不上：{old_path}（可能磁盘/权限异常）。")
    if written_new != new_sha:
        raise ValueError(f"新版副本落盘后内容对不上：{new_path}（可能磁盘/权限异常）。")

    created_at = datetime.datetime.now().isoformat(timespec="seconds")
    meta = {
        "preview_id": preview_id,
        "stem": name,
        "ext": suffix,
        "old_sha256": old_sha,
        "new_sha256": new_sha,
        "created_at": created_at,
    }
    meta_path = os.path.join(target, PREVIEW_META)
    try:
        with open(meta_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    except OSError as exc:
        raise ValueError(f"写不了预览元数据 {meta_path}：{exc}") from exc

    return Preview(preview_id=preview_id, dir=target, old_path=old_path,
                   new_path=new_path, old_sha256=written_old,
                   new_sha256=written_new, ext=suffix, created_at=created_at)


def verify_preview(preview_id: str, work_dir: str,
                   dir_name: str = DIFF_DIRNAME) -> tuple[bool, str]:
    """应用前的闸门：重算两侧副本 sha256，与 ``preview.json`` 比对。

    **成功返回 ``(True, "")``**；任何不一致都返回 ``(False, 中文原因)``：id 不合法/
    目录缺失/元数据缺失或损坏/字段不全/两侧副本缺失或被改动。过期副本拿不到 ``True``，
    因此「预览作废后继续 apply」在服务层就被挡住。

    ``dir_name`` 与 :func:`make_preview` 一致：默认 ``.diff``；初排交付用
    ``compare``，两边必须传同一个名字，否则校验会去找错目录（宁可报「目录不存在」
    也不许跨目录放行）。
    """
    pid = str(preview_id or "").strip()
    if not pid:
        return False, "没有预览 id，无法校验副本（请先生成预览）。"
    if not _is_token(pid):
        return False, f"预览 id 不合法：{pid!r}（只允许 ASCII 字母、数字、下划线与短横）。"
    try:
        base = preview_dir(work_dir, dir_name)
    except ValueError as exc:
        return False, str(exc)
    folder = os.path.join(base, pid)
    if not os.path.isdir(folder):
        return False, f"预览目录不存在（可能已被清理）：{folder}"

    meta_path = os.path.join(folder, PREVIEW_META)
    if not os.path.isfile(meta_path):
        return False, f"预览元数据缺失：{meta_path}（无法确认副本是否过期，已拒绝）。"
    try:
        with open(meta_path, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, ValueError) as exc:
        return False, f"预览元数据读不出来或不是合法 JSON：{meta_path}（{exc}）"
    if not isinstance(meta, dict):
        return False, f"预览元数据格式不对（应为 JSON 对象）：{meta_path}"
    if str(meta.get("preview_id") or "") != pid:
        return False, (f"预览元数据的 preview_id（{meta.get('preview_id')!r}）"
                       f"与目录名（{pid!r}）不一致，已拒绝。")

    try:
        suffix = _clean_ext(str(meta.get("ext") or ""))
    except ValueError as exc:
        return False, f"预览元数据里的扩展名不合法（{exc}），已拒绝。"
    name = str(meta.get("stem") or "").strip() or DEFAULT_STEM
    if not _is_token(name):
        return False, f"预览元数据里的主文件名不合法：{name!r}，已拒绝。"

    old_sha = str(meta.get("old_sha256") or "")
    new_sha = str(meta.get("new_sha256") or "")
    if not _is_sha256(old_sha) or not _is_sha256(new_sha):
        return False, f"预览元数据里的 sha256 缺失或格式不对：{meta_path}，已拒绝。"

    old_path = os.path.join(folder, name + suffix)
    new_path = os.path.join(folder, name + PROPOSED_SUFFIX + suffix)
    for label, path in (("旧版", old_path), ("新版", new_path)):
        if not os.path.isfile(path):
            return False, (f"预览{label}副本缺失：{path}"
                           "（已拒绝，请重新生成预览后再应用）。")
    try:
        actual_old = file_sha256(old_path)
        actual_new = file_sha256(new_path)
    except OSError as exc:
        return False, (f"预览副本读不出来：{exc}"
                       "（已拒绝，请重新生成预览后再应用）。")
    if actual_old != old_sha:
        return False, (f"预览旧版副本与元数据不一致（sha256 不符）：{old_path}"
                       "—— 副本可能被改过或已过期，不能用于应用，请重新生成预览。")
    if actual_new != new_sha:
        return False, (f"预览新版副本与元数据不一致（sha256 不符）：{new_path}"
                       "—— 副本可能被改过或已过期，不能用于应用，请重新生成预览。")
    return True, ""


def find_editor() -> str:
    """探测 VS Code 命令行：先 ``shutil.which``，再查常见安装路径；找不到返回空串。"""
    for command in CODE_COMMANDS:
        found = shutil.which(command)
        if found:
            return found
    for path in CODE_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return ""


def _one_line(text: str, *, limit: int = 200) -> str:
    """把编辑器输出压成一行短摘要（进用户可见文案前先去脏）。"""
    return " ".join(str(text or "").split())[:limit]


def open_diff(preview: Preview) -> tuple[bool, str]:
    """用 ``code --diff <旧> <新>`` 打开两文件对比。

    成功（编辑器真的执行且 rc == 0）返回 ``(True, 简短成功文案)``；其余情况一律
    ``(False, 中文原因)``，原因里**一定带上两个副本路径**，方便用户自己打开或复制。
    只用列表 argv + ``shell=False``，不做任何字符串拼接。
    """
    old_path = str(getattr(preview, "old_path", "") or "")
    new_path = str(getattr(preview, "new_path", "") or "")
    if not old_path or not new_path:
        return False, "预览对象缺少副本路径，无法打开对比（请重新生成预览）。"

    missing = [path for path in (old_path, new_path) if not os.path.isfile(path)]
    if missing:
        return False, ("对比副本不存在："
                       + "；".join(missing)
                       + f"。没有打开对比。两个文件路径：{old_path} / {new_path}")

    # 记录里的 sha 对得上才值得给用户看（副本被改过就没什么可比的了）。
    for label, path, expect in (("旧版", old_path, str(getattr(preview, "old_sha256", "") or "")),
                                ("新版", new_path, str(getattr(preview, "new_sha256", "") or ""))):
        if not _is_sha256(expect):
            continue          # 手工构造的 Preview 没带 sha：只做存在性检查
        try:
            actual = file_sha256(path)
        except OSError as exc:
            return False, (f"读不了{label}副本 {path}：{exc}。"
                           f"没有打开对比。两个文件路径：{old_path} / {new_path}")
        if actual != expect:
            return False, (f"{label}副本与预览记录不一致（副本可能在生成后被改动）。"
                           f"没有打开对比。两个文件路径：{old_path} / {new_path}")

    editor = find_editor()
    if not editor:
        return False, ("没找到 VS Code（命令行 code 与常见安装路径都没探测到），"
                       f"对比没有打开。两个文件路径：{old_path} / {new_path}")

    argv = [editor, "--diff", old_path, new_path]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=OPEN_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return False, (f"启动 {editor} 打开对比超时（>{OPEN_TIMEOUT:.0f}s），对比没有打开。"
                       f"两个文件路径：{old_path} / {new_path}")
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001 - 一律翻成文案
        return False, (f"启动 {editor} 打开对比失败：{exc}。"
                       f"两个文件路径：{old_path} / {new_path}")

    if proc.returncode != 0:
        detail = _one_line(getattr(proc, "stderr", "") or getattr(proc, "stdout", ""))
        suffix = f"（输出：{detail}）" if detail else ""
        return False, (f"VS Code 返回退出码 {proc.returncode}{suffix}，对比没有打开。"
                       f"两个文件路径：{old_path} / {new_path}")
    return True, "已在 VS Code 中打开对比"
