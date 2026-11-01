#!/usr/bin/env python3
r"""源码树静默快照：给「构建期间源码被写手改动」留只读证据。

**来历（历史）**：这段逻辑原先写在根目录 `build.sh` 的 `SOURCE-TREE-SNAPSHOT` 标记块里
（v1.5.0，mac 构建脚本；shell 版）。批108 删除 mac 外壳与 `build.sh` 后，快照能力本身仍是
「构建输入是否前后一致」的证据，故原样迁到本工具；**sha 语义与旧 shell 版逐字节等价**：

- 输入清单：`build.sh`（若存在）、`Engine/**`（排除 `Engine/out/**`、`__pycache__`、
  `*.pyc`、`*.pyo`、`.DS_Store`）、`sources-mac/**/*.swift`、`sources-win/**`、
  `编辑笔记/模板文件/**`、`Resources/**`（后三者排除 `.DS_Store`）。
- 算法：相对仓库根、POSIX 分隔符的文件清单按**字节序**排序 → 逐文件 sha256 →
  `"<hex>  <path>\n"` 行再聚合 sha256。

> 说明：`sources-mac/`、`sources-win/` 两条规则属于**历史**（旧外壳已于批108 删除）；保留它们
> 只为 sha 语义与旧版完全等价，目录不存在时自然贡献 0 个文件。

用法：

    python3 Engine/tools/source_tree_sha.py                # 打印人类可读摘要
    python3 Engine/tools/source_tree_sha.py --json         # {"sha": ..., "files": n}
    python3 Engine/tools/source_tree_sha.py --check-against <开始态 sha>

`--check-against` 复刻旧 shell 的 `_source_tree_check`：不一致时在 stdout 打
`source_tree_changed=1`、在 stderr 打明示警告，**退出码仍为 0**（快照不改变构建判定）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

REPO_DEFAULT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _sha256_file(path: str) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None  # 与旧 shell 一致：读不到的文件整行缺失（列表计数仍算它）
    return h.hexdigest()


def _walk(root: str, rel_dir: str, keep) -> list[str]:
    """遍历 `root/rel_dir`，返回相对仓库根、`/` 分隔的文件路径（跳过符号链接，对齐 find -type f）。"""
    base = os.path.join(root, rel_dir)
    if not os.path.isdir(base):
        return []
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if keep(rel, name):
                out.append(rel)
    return out


def _keep_engine(rel: str, name: str) -> bool:
    if rel.startswith("Engine/out/"):
        return False
    if "__pycache__" in rel.split("/")[:-1]:
        return False
    return not (name.endswith(".pyc") or name.endswith(".pyo") or name == ".DS_Store")


def _keep_sources_win(rel: str, name: str) -> bool:
    return _keep_engine(rel, name)


def _keep_swift(_rel: str, name: str) -> bool:
    return name.endswith(".swift")


def _keep_plain(_rel: str, name: str) -> bool:
    return name != ".DS_Store"


def collect_files(root: str) -> list[str]:
    """按旧 shell 版的输入口径收集文件清单（未排序）。"""
    files: list[str] = []
    build_sh = os.path.join(root, "build.sh")
    if os.path.isfile(build_sh) and not os.path.islink(build_sh):
        files.append("build.sh")
    files += _walk(root, "Engine", _keep_engine)
    files += _walk(root, "sources-mac", _keep_swift)      # 历史外壳：批108 已删，规则保留
    files += _walk(root, "sources-win", _keep_sources_win)  # 历史外壳：批108 已删，规则保留
    files += _walk(root, os.path.join("编辑笔记", "模板文件"), _keep_plain)
    files += _walk(root, "Resources", _keep_plain)
    return files


def snapshot(root: str) -> tuple[str, int]:
    """返回 `(聚合 sha, 文件数)`；无文件或读不到时 sha=`unknown`、文件数 0（与旧 shell 一致）。"""
    files = collect_files(root)
    # LC_ALL=C sort -z 的等价：按文件系统字节序排（macOS/Linux 为 UTF-8，Windows 亦为 UTF-8）
    files.sort(key=os.fsencode)
    agg = hashlib.sha256()
    hashed = 0
    for rel in files:
        digest = _sha256_file(os.path.join(root, rel.replace("/", os.sep)))
        if digest is None:
            continue
        agg.update(digest.encode("ascii") + b"  " + os.fsencode(rel) + b"\n")
        hashed += 1
    if not files or hashed == 0:
        return "unknown", 0
    return agg.hexdigest(), len(files)


def main(argv: list[str] | None = None) -> int:
    # 与旧 shell 版一致：输出一律 UTF-8（构建日志/CI 直接按字节比），且控制台编码装不下
    # 中文标点/⚠ 时不崩（cp936 下 print 会抛 UnicodeEncodeError，把快照变成构建事故）。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="源码树静默快照（构建输入前后一致性证据）")
    ap.add_argument("--root", default=REPO_DEFAULT, help="仓库根（默认本脚本上三级）")
    ap.add_argument("--json", action="store_true", help='输出 JSON：{"sha": ..., "files": n}')
    ap.add_argument("--check-against", default=None, metavar="SHA",
                    help="复算并与开始态 sha 比对；不一致时告警但退出码仍为 0")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"❌ 不是目录：{root}", file=sys.stderr)
        return 2
    sha, count = snapshot(root)

    if args.check_against is not None:
        start = (args.check_against or "").strip()
        changed = int(start not in ("", "unknown") and sha != "unknown" and start != sha)
        if args.json:
            print(json.dumps({"sha": sha, "files": count, "changed": changed}, ensure_ascii=False))
        else:
            print(f"source_tree_sha_end={sha}")
            print(f"source_tree_changed={changed}")
        if changed:
            print(f"⚠ 构建期间源码被修改（写手未静默）：source_tree changed {start} -> {sha}；"
                  "本次构建产物可能不一致，请重跑 build", file=sys.stderr)
            print("   （请确认写手已停手后重跑构建；本警告不改变退出码，manifest 记 "
                  "source_tree_changed=1）", file=sys.stderr)
        return 0

    if args.json:
        print(json.dumps({"sha": sha, "files": count}, ensure_ascii=False))
    else:
        if sha == "unknown":
            print("源码树 sha unknown（没有可读输入文件）")
        else:
            print(f"源码树 sha {sha}（{count} 个文件；构建结束复算，不一致会告警）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
