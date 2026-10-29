#!/usr/bin/env python3
"""合并一期 PDF（第 88 条）：把「合并文件」从 mac 外壳搬进引擎。

这套件要钉住的是**行为等价**，不是「代码看起来像」：
  (a) 文件名规则：序号目录、`03 Jie_Zhou` → `Jie_Zhou`、宽松匹配 `Yang Liu.pdf`；
  (b) 合并顺序与书签：扉页 → 中文目录 → 英文目录 → 各篇（按序号）→ 封底，
      书签名与页码逐个对齐；
  (c) 两种大纲模式：默认**平铺**（上游 PyPDF2 行为），`--nest-outlines` 挂到文章之下；
  (d) 编委页/封底**只并页面、不导入自带大纲**（实测有陈旧同名书签的坑）；
  (e) 容错：缺文件照并、坏文件不炸、非 PDF 与工具箱自己的 `合并*.pdf` 不参与识别；
  (f) CLI 契约：退出码、最后一行产物路径。
"""
from __future__ import annotations

import glob
import os
import pathlib
import subprocess
import sys
import tempfile

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from pypdf import PdfReader, PdfWriter  # noqa: E402
from slcn import pdfmerge as M  # noqa: E402

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


def mk_pdf(path: str, pages: int, bookmarks: list[tuple[str, int]] | None = None) -> str:
    """造一个 N 页的 PDF；`bookmarks` 是 (标题, 页序) 列表。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=300)
    for title, idx in (bookmarks or []):
        w.add_outline_item(title, idx)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def outline_of(path: str) -> list[str]:
    """完整大纲（含层级，用缩进表示），形如 `['cover → p0', ' 小节 → p4']`。"""
    r = PdfReader(path)
    out: list[str] = []

    def walk(items, depth=0):
        for it in items:
            if isinstance(it, list):
                walk(it, depth + 1)
                continue
            n = r.get_destination_page_number(it)
            out.append("  " * depth + f"{it.title} → p{n}")

    walk(list(r.outline))
    return out


def top_outline(path: str) -> list[tuple[str, int]]:
    return M.read_outline(path)


# ─────────────────────────────────────────────────────────────────────────
print("── (a) 文件名规则 ──")
with tempfile.TemporaryDirectory() as tmp:
    for n in ["01 A_Author", "10 J_Author", "000 目录", "封面", "99x", "1 单数字"]:
        os.makedirs(os.path.join(tmp, n), exist_ok=True)
    # `99x` 也符合 `^[0-9][0-9]`，所以它**该**被收进来；`1 单数字`/`封面` 不该。
    check("(a) 只收 `^[0-9][0-9]` 的**目录**，且按名排序",
          M.numbered_entries(tmp) == ["000 目录", "01 A_Author", "10 J_Author", "99x"],
          f"{M.numbered_entries(tmp)}")
    check("(a) `03 Jie_Zhou` → 取最后一段 `Jie_Zhou`",
          M.article_name("03 Jie_Zhou") == "Jie_Zhou")
    check("(a) 目录名没有空格时原样返回", M.article_name("07Chen") == "07Chen")
    check("(a) lax 键：下划线/半角空格/全角空格/连字符/大小写都归一",
          M.loose_key("Yang_Liu") == M.loose_key("yang liu") == M.loose_key("Yang-Liu")
          == M.loose_key("Yang　Liu") == "yangliu")

    d = os.path.join(tmp, "05 X_Y")
    os.makedirs(d, exist_ok=True)
    mk_pdf(os.path.join(d, "X_Y.pdf"), 1)
    check("(a) 精确命中 `<名字>.pdf`", M.resolve_article_pdf(d, "X_Y")[1] == "X_Y.pdf")

    d2 = os.path.join(tmp, "06 C_D")
    os.makedirs(d2, exist_ok=True)
    mk_pdf(os.path.join(d2, "C D.pdf"), 2)          # 下划线被写成空格（实测踩过，丢过 33 页）
    got = M.resolve_article_pdf(d2, "C_D")
    check("(a) 精确找不到时**宽松匹配** `C D.pdf`（原版会整篇跳过且不报错）",
          got is not None and got[1] == "C D.pdf", f"{got}")
    check("(a) 宽松无命中 → None（不瞎猜）",
          M.resolve_article_pdf(d, "不存在的名字") is None)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (b) 合并顺序 / 书签 / 页码 ──")
root = tempfile.mkdtemp(prefix="slcn_merge_")
mk_pdf(f"{root}/扉页.pdf", 2)
mk_pdf(f"{root}/000 目录/Chinese Content.pdf", 1)
mk_pdf(f"{root}/000 目录/contents.pdf", 1)
mk_pdf(f"{root}/01 A_Author/A_Author.pdf", 3, [("小节一", 0), ("小节二", 2)])
mk_pdf(f"{root}/02 B_Author/B_Author.pdf", 2)

res = M.merge_issue(root, log=lambda *a, **k: None)
check("(b) 总页数 = 2+1+1+3+2 = 9", res.page_count == 9, f"{res.page_count}")
check("(b) 输出是 `合并<时间戳>.pdf`，落在期目录里",
      os.path.basename(res.output).startswith("合并") and res.output.endswith(".pdf")
      and os.path.dirname(res.output) == os.path.abspath(root), res.output)
check("(b) 顶层书签顺序 = 封面 → 中文目录 → 英文目录 → 各篇（按序号）",
      [t for t, _ in res.outline] == ["cover", "Chinese Content", "contents",
                                      "A_Author", "B_Author"], f"{res.outline}")
check("(b) 各书签页码 = 0/2/3/4/7（页码随合并偏移）",
      [p for _, p in res.outline] == [0, 2, 3, 4, 7], f"{res.outline}")
check("(b) 默认**平铺**：源文件自带的小节书签排在顶层、且在本程序书签之后",
      outline_of(res.output) == ["cover → p0", "Chinese Content → p2", "contents → p3",
                                 "A_Author → p4", "B_Author → p7",
                                 "小节一 → p4", "小节二 → p6"],
      f"{outline_of(res.output)}")

res_n = M.merge_issue(root, out=os.path.join(root, "nested.pdf"),
                      nest_outlines=True, log=lambda *a, **k: None)
check("(b) `nest_outlines`：小节书签挂到该篇文章之下",
      outline_of(res_n.output) == ["cover → p0", "Chinese Content → p2", "contents → p3",
                                   "A_Author → p4", "  小节一 → p4", "  小节二 → p6",
                                   "B_Author → p7"],
      f"{outline_of(res_n.output)}")
check("(b) `--out` 指定路径时按指定路径写", res_n.output.endswith("nested.pdf"))

# ─────────────────────────────────────────────────────────────────────────
print("\n── (c) 编委页 / 封底：只并页面，不导入自带大纲 ──")
root2 = tempfile.mkdtemp(prefix="slcn_merge2_")
mk_pdf(f"{root2}/扉页.pdf", 1)
mk_pdf(f"{root2}/000 目录/Chinese Content.pdf", 1)
mk_pdf(f"{root2}/01 A_Author/A_Author.pdf", 1)
# 实测坑：编委页文件自带一个叫「Chinese Content」的陈旧书签
mk_pdf(f"{root2}/编委页.pdf", 1, [("Chinese Content", 0)])
mk_pdf(f"{root2}/cover+bottom+Englishi_2024.pdf", 2)

res2 = M.merge_issue(root2, editors_page=True, back_cover=True, log=lambda *a, **k: None)
check("(c) 编委页与封底都并进去了（页数 1+1+1+1+2 = 6）", res2.page_count == 6, f"{res2.page_count}")
names = [t for t, _ in res2.outline]
check("(c) 书签顺序：cover → 编委页 → Chinese Content → A_Author → 封底",
      names == ["cover", "编委页", "Chinese Content", "A_Author", "封底"], f"{names}")
full = outline_of(res2.output)
check("(c) 编委页自带的陈旧「Chinese Content」书签**没有被导入**（只有一个同名书签）",
      sum(1 for x in full if "Chinese Content" in x) == 1, f"{full}")
check("(c) 封底自带的大纲也没被导入（它本来就没有，这里防的是以后有）",
      full.count("封底 → p4") == 1, f"{full}")

# 识别规则本身
root3 = tempfile.mkdtemp(prefix="slcn_merge3_")
mk_pdf(f"{root3}/cneditor左移_2024.pdf", 1)
mk_pdf(f"{root3}/编委页.pdf", 1)
mk_pdf(f"{root3}/合并2026_01_01_00_00_00.pdf", 9)     # 工具箱自己的产物，必须排除
chosen, others = M.detect_editors_page(root3)
check("(c) 编委页优先级：约定名 `编委页.pdf` > `cneditor` 前缀",
      chosen is not None and chosen.file_name == "编委页.pdf" and chosen.matched_by == "约定名",
      f"{chosen}")
check("(c) 低档候选作为「疑似遗留」报出来", others == ["cneditor左移_2024.pdf"], f"{others}")
check("(c) ⚠️「疑似遗留」里**不许出现被选中的那个文件**"
      "（`编委页.pdf` 同时命中「约定名」与「名字含编委」两档，不去重就会自己忽略自己）",
      "编委页.pdf" not in others, f"{others}")
check("(c) **`合并*.pdf` 不参与识别**（否则会把上次的合并结果当编委页）",
      all("合并" not in x for x in others) and chosen.file_name != "合并2026_01_01_00_00_00.pdf")
back = M.detect_back_cover(root2)
check("(c) 封底识别：`cover+bottom` 前缀优先",
      back is not None and back.file_name == "cover+bottom+Englishi_2024.pdf"
      and back.matched_by == "cover+bottom 前缀", f"{back}")
check("(c) 没有封底文件 → None", M.detect_back_cover(tempfile.mkdtemp()) is None)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (d) 容错：缺文件照并、坏文件不炸 ──")
root4 = tempfile.mkdtemp(prefix="slcn_merge4_")
mk_pdf(f"{root4}/扉页.pdf", 1)
mk_pdf(f"{root4}/000 目录/Chinese Content.pdf", 1)      # 故意没有 contents.pdf
mk_pdf(f"{root4}/01 A_Author/A_Author.pdf", 2)
mk_pdf(f"{root4}/02 B_Author/B_Author.pdf", 1)           # 故意没有 03
os.makedirs(f"{root4}/03 C_Author", exist_ok=True)
(root4_path := os.path.join(root4, "03 C_Author"))
open(os.path.join(root4_path, "C_Author.pdf"), "wb").write(b"%PDF-1.4 not a real pdf")

res4 = M.merge_issue(root4, log=lambda *a, **k: None)
check("(d) 缺 `contents.pdf` 与 `03` 的稿件 → 仍然合并成功（页数 1+1+2+1 = 5）",
      res4.page_count == 5, f"{res4.page_count}")
# 缺的只有 `contents.pdf`（`C_Author.pdf` **存在**、只是内容坏 → 归 warnings，不归 missing）
check("(d) 缺了什么如实记在 `missing` 里，不静默",
      res4.missing == ["contents.pdf"], f"{res4.missing}")
check("(d) 坏 PDF 不炸：整篇合并照样出结果，坏的那篇记进 warnings",
      len(res4.warnings) == 1 and "C_Author" in res4.warnings[0], f"{res4.warnings}")
check("(d) 非 PDF 文件不参与识别",
      M.detect_editors_page(root4)[0] is None and M.detect_back_cover(root4) is None)
try:
    M.merge_issue("/tmp/绝对不存在的期目录_zzz", log=lambda *a: None)
    _raised = False
except NotADirectoryError:
    _raised = True
check("(d) 期目录不存在 → 抛 NotADirectoryError（由 CLI 转成退出码 2）", _raised)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (e) CLI 契约 ──")
PY = sys.executable
env = dict(os.environ)
env["PYTHONPATH"] = str(ENGINE / "vendor") + os.pathsep + env.get("PYTHONPATH", "")


def run(*args):
    return subprocess.run([PY, str(ENGINE / "mergepdf.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(ENGINE))


r = run("--version")
check("(e) `--version` 可用且不要求 --folder", r.returncode == 0 and "SLCN 引擎" in r.stdout,
      r.stdout.strip()[:70])
root5 = tempfile.mkdtemp(prefix="slcn_merge5_")
mk_pdf(f"{root5}/扉页.pdf", 1)
mk_pdf(f"{root5}/01 A_Author/A_Author.pdf", 2)
r = run("--folder", root5)
check("(e) 正常合并 → 退出码 0", r.returncode == 0, f"rc={r.returncode} {r.stderr[-120:]}")
last = [ln for ln in r.stdout.splitlines() if ln.strip()][-1]
check("(e) **最后一行是契约行** `完成。合并文件：<路径>（共 N 页）`",
      last.startswith("完成。合并文件：") and "（共 3 页）" in last, last)
check("(e) 契约行里的路径真实存在且等于产物",
      os.path.exists(last.split("：", 1)[1].split("（共")[0].strip()))
# 缺文件时 CLI 要给外壳可高亮的 `⚠` 行（上面 root5 没有 000 目录，所以拿 root4 那套来跑）
r_missing = run("--folder", root4)
check("(e) 缺文件时 CLI 打出 `⚠ … 没找到` 行（外壳可高亮）",
      any("没找到" in ln and "⚠" in ln for ln in r_missing.stdout.splitlines()),
      f"{[l for l in r_missing.stdout.splitlines() if '⚠' in l][:2]}")
r = run("--folder", "/tmp/绝对不存在的期目录_zzz")
check("(e) 期目录不存在 → 退出码 2（错误在 stderr）",
      r.returncode == 2 and "期目录不存在" in r.stderr, f"rc={r.returncode}")
check("(e) 缺 `--folder` → 退出码 2（argparse）", run().returncode == 2)

# ─────────────────────────────────────────────────────────────────────────
print("\n── (f) 真实成品交叉验证：与编辑用 mac 版合出来的那份逐条比对 ──")
# 2026年第5期(90) 的期目录里留着编辑用 mac App 合出来的成品
# `合并2026_10_05_17_42_45.pdf`（176 页 / 102 条大纲）。同一份输入跑引擎，
# **页数、大纲条数、层级、名字、页码必须逐条相同** —— 这是「移植得对不对」最硬的证据。
# 只读原稿、产物写临时目录；文件不在就跳过（换了机器/目录时不至于误报）。
# ⚠️ 相对本文件推仓库根（`tests/` → `Engine/` → `` → 仓库根），**不写绝对路径**：
#    写死某台机器的路径会让这条交叉验证在别人机器上静默 SKIP（2026-10-15 拆仓预演时发现）。
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ISSUE = _REPO_ROOT / "2026年第5期(90)"
REAL = ISSUE / "合并2026_10_05_17_42_45.pdf"


def _flat_outline(path: str) -> list[tuple[str, int]]:
    import io
    import contextlib
    r = PdfReader(path)
    out: list[tuple[str, int]] = []
    with contextlib.redirect_stderr(io.StringIO()):   # pypdf 对老 PDF 的告警，与断言无关
        def walk(items, depth=0):
            for it in items:
                if isinstance(it, list):
                    walk(it, depth + 1)
                    continue
                out.append(("  " * depth + (it.title or ""),
                            r.get_destination_page_number(it)))
        walk(list(r.outline))
    return out


if ISSUE.is_dir() and REAL.exists():
    with tempfile.TemporaryDirectory() as tmp:
        mine = os.path.join(tmp, "engine.pdf")
        M.merge_issue(str(ISSUE), out=mine, editors_page=True, nest_outlines=True,
                      log=lambda *a, **k: None)
        check("(f) 真实期目录：页数与 mac 成品相同",
              len(PdfReader(mine).pages) == len(PdfReader(str(REAL)).pages),
              f"{len(PdfReader(mine).pages)} vs {len(PdfReader(str(REAL)).pages)}")
        a, b = _flat_outline(str(REAL)), _flat_outline(mine)
        check("(f) 真实期目录：**完整大纲（层级+名字+页码）逐条相同**", a == b,
              f"真产物 {len(a)} 条 / 引擎 {len(b)} 条")
else:
    print("SKIP  (f) 真实成品不在（换了机器/目录），跳过交叉验证")

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
print("失败 0 项（全部通过）")
sys.exit(0)
