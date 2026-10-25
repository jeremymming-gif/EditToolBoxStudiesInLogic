#!/usr/bin/env python3.14
r"""`checkbib.py --merge-into`（手动「并回」通道）的判据测试。

为什么单独一套：并回是**机器直接改学术事实**的动作（第 64 条的用户决定从自动通道
扩到手动通道），它的每一条安全轨都必须有反例钉着：

  ① 合并：命中 key 的条目并进去；`author` 倒装；字段顺序按任务书第三节重排；
     **未返回的条目逐字节不动**（含文件级头注释、空行结构）。
  ② 三条保障在手动通道同样生效：key 改名**永不接受**（跳过 + 中文原因）、
     **不新增条目**（交回 bib 里多出来的键只记一笔）、**条目数与键序列不变**。
  ③ 回滚副本 `<目标>.ai前`：先备份后覆盖；第二次并回时它代表「本次之前」。
  ④ 合并记录落点：同级有任务书 → 追加；没有 → 新建 `<目标 stem>_合并记录.md`；
     同级有多份任务书 → 不猜，单独新建。
  ⑤ 退出码与「未改动」：0 = 一条都没并进去且**一个文件都没写**；1 = 已并入 ≥1 条；
     2 = 用法 / IO 错误。
  ⑦ 只有一份合并实现：`checkbib` 与 `convert` 走的是 `slcn.aibib` 的同一个函数。
     （⑥ 这个号位空着 —— 段号跟着本文件的 `══ ⑦/⑧/⑨ ══` 小标题走，不为对齐而重排。）
  ⑧ **粘贴模式**（`--returned-stdin`，phase 2c）：同一段文本走 stdin 与走 `--returned <文件>`
     必须**逐字节同结果**（合并结果、回滚副本、退出码都相同）；stdin 与 `--returned` 互斥；
     空 stdin = 用法错误（退出码 2、目标一个字节不动、不写任何文件）。
  ⑨ **列表模式**（`--list-keys`）：只打一行「N 条：key, key…」，**永不写文件**
     （同时给 `--out` / `--merge-into` 也不写、不并回）。

不联网、不写 out/、不跑 corpus.py / build.sh；所有产物都在 tempfile 目录里。

用法：
    cd Engine && /opt/homebrew/bin/python3.14 tests/test_merge_into.py
"""

import contextlib
import hashlib
import io
import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
for _p in (_ENGINE, os.path.join(_ENGINE, "vendor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import checkbib  # noqa: E402
from slcn import aibib  # noqa: E402
from slcn.bibparse import parse_entries  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = checkbib.main(list(argv))
    except SystemExit as e:  # argparse 用法错误
        code = e.code if isinstance(e.code, int) else 2
    return code, out.getvalue(), err.getvalue()


class _StdinStub:
    """给 `checkbib.main()` 用的假 stdin。

    `_read_stdin()` 读的是 `sys.stdin.buffer`（原始字节，避免终端 locale 影响解码），
    所以替身只要有 `.buffer` 就够——不真的开管道，测试因此没有超时风险。
    """

    def __init__(self, data: bytes):
        self.buffer = io.BytesIO(data)


def run_cli_stdin(stdin_text, *argv, encoding="utf-8"):
    """把 `stdin_text` 当作**标准输入**跑 CLI（粘贴模式的等价物）。"""
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdin
    sys.stdin = _StdinStub(stdin_text.encode(encoding))
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = checkbib.main(list(argv))
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    finally:
        sys.stdin = old
    return code, out.getvalue(), err.getvalue()


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(tmp, name, text):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return p


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def mtime(path):
    return os.stat(path).st_mtime_ns


def entry_span(text, key):
    """条目 `key` 的 [start, end)（含结尾花括号），找不到 None。"""
    m = re.search(r"@\w+\s*\{\s*" + re.escape(key) + r"\s*,", text)
    if not m:
        return None
    i = text.index("{", m.start())
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return m.start(), j + 1
        j += 1
    return m.start(), len(text)


def keys_of(text):
    return [e.key for e in parse_entries(text)[0]]


# ── 语料：目标 bib（3 条，含一条**不交回**的条目）与交回的整份 bib ─────────────

TARGET = """\
% ── 由脚注 / 文末编号清单中的文献条目生成 ──

@article{Burge1984,
  author  = {T. Burge},
  title   = {Epistemic Paradox},
  journal = {The Journal of Philosophy},
  volume  = {81},
  number  = {1},
  pages   = {5--29},
  year    = {1984},
  doi     = {10.1000/x}
}

@book{Almog1989,
  title     = {Themes from Kaplan},
  author    = {Joseph Almog and John Perry and Howard Wettstein},
  publisher = {Oxford University Press},
  address   = {Oxford},
  year      = {1989}
}

@article{Mei2018,
  author  = {T. Mei},
  title   = {Untouched Entry},
  journal = {Some Journal},
  volume  = {3},
  number  = {2},
  pages   = {10--20},
  year    = {2018}
}
"""

# 交回的整份 bib：Burge1984 的 author 是「名 姓」且字段乱序（要倒装 + 重排）；
# Almog1989 内容一字未改（照样算「并入」，只是文本重写）；
# Mei2018 被**改名**成 Mei2018a（最危险的一类）；另多一条 New2099（不许新增）。
RETURNED = """\
% ── 由脚注 / 文末编号清单中的文献条目生成 ──

@article{Mei2018a,
  author  = {T. Mei},
  title   = {Untouched Entry},
  journal = {Some Journal},
  volume  = {3},
  number  = {2},
  pages   = {10--20},
  year    = {2018}
}

@article{Burge1984,
  journal = {The Journal of Philosophy},
  title   = {Epistemic Paradox},
  author  = {T. Burge},
  year    = {1984},
  pages   = {5--29},
  number  = {1},
  volume  = {81},
  doi     = {10.1000/x}
}

@book{Almog1989,
  title     = {Themes from Kaplan},
  author    = {Joseph Almog and John Perry and Howard Wettstein},
  publisher = {Oxford University Press},
  address   = {Oxford},
  year      = {1989}
}

@article{New2099,
  author  = {N. New},
  title   = {A Brand New Entry},
  journal = {Nowhere},
  year    = {2099}
}
"""

NOTHING = """\
@article{Nowhere2099,
  author  = {N. Nobody},
  title   = {Not In Target},
  journal = {Nowhere},
  year    = {2099}
}
"""


def fixture(tmp, name="reference.bib", target=TARGET, returned=RETURNED):
    """在 `tmp` 下造一份目标 + 一份交回文件，返回 (目标路径, 交回路径)。"""
    return write(tmp, name, target), write(tmp, "returned.bib", returned)


# ── ① 合并本体 ─────────────────────────────────────────────────────────────

print("\n══ ① 合并：倒装 + 重排 + 未返回的逐字节不动 ══")

with tempfile.TemporaryDirectory() as tmp:
    target, returned = fixture(tmp)
    before = read(target)
    code, out, err = run_cli("--returned", returned, "--merge-into", target)
    after = read(target)

    check("① 并回成功 → 退出码 1", code == 1, f"code={code} err={err.strip()[:80]}")
    check("① 控制台给出并入/跳过条数", "并入 2 条 / 跳过 3 条" in out, repr(out.splitlines()[:3]))
    check("① 覆盖前先声明了目标与备份名",
          f"将覆盖 {target}" in out and f"{target}.ai前" in out)
    check("① key 序列与条目数**一个都没变**",
          keys_of(before) == keys_of(after) == ["Burge1984", "Almog1989", "Mei2018"],
          f"{keys_of(before)} → {keys_of(after)}")

    b_span = entry_span(after, "Burge1984")
    b_txt = after[b_span[0]:b_span[1]] if b_span else ""
    check("① author 由引擎倒装（`T. Burge` → `Burge, T.`）",
          "author  = {Burge, T.}" in b_txt and "T. Burge" not in b_txt, b_txt.splitlines()[1] if b_txt else "")
    order = [m.group(1) for m in re.finditer(r"^\s*(\w+)\s*=", b_txt, re.M)]
    check("① 字段顺序按任务书第三节重排（未知字段 doi 留末尾）",
          order == ["author", "title", "journal", "volume", "number", "pages", "year", "doi"],
          f"{order}")
    check("① 交回 bib 里多出来的键**没有**被新增进产物",
          "New2099" not in after and "@article{New2099" not in after)
    check("① 文件级头注释与空行结构留在原处",
          after.startswith("% ── 由脚注 / 文末编号清单中的文献条目生成 ──\n\n@article{Burge1984,"),
          repr(after[:40]))

    m_span = entry_span(after, "Mei2018")
    old_m_span = entry_span(before, "Mei2018")
    check("① **未返回**的条目（Mei2018）逐字节不动",
          bool(m_span) and bool(old_m_span) and after[m_span[0]:m_span[1]] == before[old_m_span[0]:old_m_span[1]])

# ── ② 三条保障在手动通道同样生效 ────────────────────────────────────────────

print("\n══ ② 保障：key 改名永不接受 / 不新增条目 ══")

with tempfile.TemporaryDirectory() as tmp:
    target, returned = fixture(tmp)
    code, out, _ = run_cli("--returned", returned, "--merge-into", target)
    rec = read(os.path.join(tmp, "reference_合并记录.md"))
    check("② 改名的键被跳过并记下中文原因",
          "`Mei2018a`：AI 返回的键在原件里不存在——工具**不新增条目**" in rec
          and "`Mei2018`：AI 未返回这一条" in rec, "")
    check("② 交回 bib 里改了 key 的那条**没有**被写成新条目",
          "Mei2018a" not in read(target))
    check("② 合并记录写明写入者是手动通道（不是 convert.py）",
          "`checkbib.py --merge-into` 在**并入之后**写入" in rec)

# ── ③ 回滚副本 ─────────────────────────────────────────────────────────────

print("\n══ ③ 回滚副本 `<目标>.ai前` ══")

with tempfile.TemporaryDirectory() as tmp:
    target, returned = fixture(tmp)
    before = read(target)
    run_cli("--returned", returned, "--merge-into", target)
    roll = target + ".ai前"
    check("③ 写了 `<目标>.ai前`，且内容 = 合并前那一份（逐字节）",
          os.path.isfile(roll) and read(roll) == before)
    first_result = read(target)
    # 第二次并回：.ai前 必须变成「第一次并回之后」的那一份（它代表「本次之前」）
    code2, out2, _ = run_cli("--returned", returned, "--merge-into", target)
    check("③ 第二次并回仍然是「有东西并入」（幂等重写也记一笔）",
          code2 == 1 and "并入 2 条" in out2, f"code={code2}")
    check("③ 第二次并回后 `.ai前` = 第一次并回的结果",
          read(target + ".ai前") == first_result)
    check("③ 合并结果幂等（第二次没有再改动字节）", read(target) == first_result)

# ── ④ 合并记录的落点 ───────────────────────────────────────────────────────

print("\n══ ④ 合并记录写到哪儿 ══")

with tempfile.TemporaryDirectory() as tmp:
    target, returned = fixture(tmp)
    _, out, _ = run_cli("--returned", returned, "--merge-into", target)
    p = os.path.join(tmp, "reference_合并记录.md")
    check("④ 同级没有任务书 → 新建 `<目标 stem>_合并记录.md`",
          os.path.isfile(p) and "已新建《reference_合并记录.md》" in out)
    body = read(p)
    check("④ 新文件有标题 + `## 六、合并记录` 一节（内容 = render_merge_section 输出）",
          body.startswith("# 合并记录（reference.bib）")
          and "## 六、合并记录（本次已并入 reference.bib）" in body
          and "### 已跳过（3 条）" in body and "已忽略" in body,
          repr(body[:40]))

with tempfile.TemporaryDirectory() as tmp:
    target, returned = fixture(tmp)
    tb = write(tmp, "reference_文献核实.md", "# 任务书\n\n（原有内容）\n")
    _, out, _ = run_cli("--returned", returned, "--merge-into", target)
    check("④ 同级有同名任务书 → **追加**到它，不新建合并记录",
          "（原有内容）" in read(tb)
          and "## 六、合并记录（本次已并入 reference.bib）" in read(tb)
          and not os.path.exists(os.path.join(tmp, "reference_合并记录.md"))
          and "已追加到同级任务书《reference_文献核实.md》" in out)

with tempfile.TemporaryDirectory() as tmp:
    # 真实形态：任务书按**稿件**命名（Meimei_Han_文献核实.md），bib 叫 reference.bib
    target, returned = fixture(tmp)
    tb = write(tmp, "Meimei_Han_文献核实.md", "# 任务书\n")
    _, out, _ = run_cli("--returned", returned, "--merge-into", target)
    check("④ 同级只有一份 `*_文献核实.md`（名字按稿件）→ 追加到它",
          "## 六、合并记录（本次已并入 reference.bib）" in read(tb)
          and not os.path.exists(os.path.join(tmp, "reference_合并记录.md"))
          and "已追加到同级任务书《Meimei_Han_文献核实.md》" in out)

with tempfile.TemporaryDirectory() as tmp:
    target, returned = fixture(tmp)
    write(tmp, "A_文献核实.md", "# A\n")
    write(tmp, "B_文献核实.md", "# B\n")
    _, out, _ = run_cli("--returned", returned, "--merge-into", target)
    check("④ 同级有多份任务书 → 分不清就不猜，单独新建 `_合并记录.md`",
          os.path.isfile(os.path.join(tmp, "reference_合并记录.md"))
          and "同级有多份任务书" in out)

# ── ⑤ 退出码：未改动 / 用法与 IO 错误 ──────────────────────────────────────

print("\n══ ⑤ 退出码与「未改动」 ══")

with tempfile.TemporaryDirectory() as tmp:
    target, _ = fixture(tmp, returned=NOTHING)
    before, mt = read(target), mtime(target)
    rep = os.path.join(tmp, "报告.md")
    code, out, err = run_cli("--returned", os.path.join(tmp, "returned.bib"),
                             "--merge-into", target, "--out", rep)
    check("⑤ 一条都没并进去 → 退出码 0 且明说「未改动」",
          code == 0 and "未改动" in out and "一个字节都没动" in out, f"code={code}")
    check("⑤ 未改动时目标**逐字节不动**（mtime 也没变）",
          read(target) == before and mtime(target) == mt)
    check("⑤ 未改动时**一个文件都没写**（不回滚副本、不写记录、不写报告）",
          not os.path.exists(target + ".ai前")
          and not os.path.exists(os.path.join(tmp, "reference_合并记录.md"))
          and not os.path.exists(rep))
    check("⑤ 未改动时逐条给出未并入的原因",
          "Nowhere2099" in out and "不存在" in out, repr(out.splitlines()[-1:]))

with tempfile.TemporaryDirectory() as tmp:
    target, returned = fixture(tmp)
    code, _, err = run_cli("--returned", returned,
                           "--merge-into", os.path.join(tmp, "nope.bib"))
    check("⑤ 目标不存在 → 退出码 2", code == 2, f"code={code} err={err.strip()[:60]}")
    code, _, err = run_cli("--returned", os.path.join(tmp, "nope.bib"), "--merge-into", target)
    check("⑤ 交回文件不存在 → 退出码 2", code == 2, f"code={code}")
    code, _, err = run_cli("--returned", returned, "--merge-into", tmp)
    check("⑤ 目标是目录 → 退出码 2", code == 2, f"code={code}")
    code, _, err = run_cli("--merge-into", target)
    check("⑤ 缺 --returned → 退出码 2（argparse）", code == 2, f"code={code}")

# ── ⑥ --out 与报告结论 ─────────────────────────────────────────────────────

print("\n══ ⑥ 合并后回读校验报告 ══")

with tempfile.TemporaryDirectory() as tmp:
    target, returned = fixture(tmp)
    rep = os.path.join(tmp, "合并后.md")
    code, out, _ = run_cli("--returned", returned, "--merge-into", target, "--out", rep)
    body = read(rep) if os.path.isfile(rep) else ""
    check("⑥ --out 写出「合并后参考文献核对报告」（四节 + 附录）",
          "合并后参考文献核对报告" in body and "## 附录：逐条状态" in body)
    check("⑥ 控制台有报告的结论一行",
          re.search(r"结论：\d+ 处问题 / \d+ 处提示", out) is not None, repr(out.splitlines()[-3:]))
    check("⑥ 报告末尾的「谁生成的」写明是并入之后 + 不联网",
          "并入 AI 结果之后" in body and "不联网" in body)

# ── ⑦ 只有一份合并实现 ─────────────────────────────────────────────────────

print("\n══ ⑦ 合并核心只有一份 ══")

check("⑦ checkbib 与 convert 用的是 `slcn.aibib` 的同一个合并入口",
      checkbib.aibib is aibib and checkbib.aibib.merge_returned_bib is aibib.merge_returned_bib,
      "checkbib.aibib is slcn.aibib")
check("⑦ CLI 走 `merge_returned_bib()`（源码级：没有自己的合并循环）",
      "aibib.merge_returned_bib(" in read(os.path.join(_ENGINE, "checkbib.py")))
check("⑦ 落盘「先备份、后覆盖」也是共用的 `apply_merge_to_file()`",
      "aibib.apply_merge_to_file(" in read(os.path.join(_ENGINE, "checkbib.py"))
      # B-229：convert.py 已拆分，`apply_ai_merge()`（含这次共用落盘）在 owning 模块里。
      and "aibib.apply_merge_to_file("
      in read(os.path.join(_ENGINE, "slcn", "convert_stage.py")))

# `replies_from_bib()` 的单元判据：整份 bib → replies 形状
_r = aibib.replies_from_bib(TARGET)
check("⑦ `replies_from_bib()` 逐条取原文（键 → 条目文本）",
      sorted(_r) == ["Almog1989", "Burge1984", "Mei2018"]
      and _r["Burge1984"].startswith("@article{Burge1984,")
      and _r["Burge1984"].rstrip().endswith("}"))
check("⑦ 重复键只取第一次",
      list(aibib.replies_from_bib("@book{X,\n title = {a}\n}\n@book{X,\n title = {b}\n}\n")) == ["X"])

# ── ⑧ 粘贴模式：`--returned-stdin` 与 `--returned <文件>` 同判据 ─────────────

print("\n══ ⑧ 粘贴模式 --returned-stdin ══")

with tempfile.TemporaryDirectory() as tmp_f, tempfile.TemporaryDirectory() as tmp_s:
    target_f, returned_f = fixture(tmp_f)
    target_s, _ = fixture(tmp_s)
    code_f, out_f, _ = run_cli("--returned", returned_f, "--merge-into", target_f)
    code_s, out_s, _ = run_cli_stdin(RETURNED, "--returned-stdin", "--merge-into", target_s)

    check("⑧ 同一段文本：stdin 与 `--returned <文件>` 退出码相同（都 = 1）",
          code_f == 1 and code_s == 1, f"file={code_f} stdin={code_s}")
    check("⑧ **合并结果逐字节相同**（stdin 只是另一种来源，判据一条没变）",
          read(target_f) == read(target_s))
    check("⑧ 回滚副本也逐字节相同（都 = 合并前的目标）",
          read(target_f + ".ai前") == read(target_s + ".ai前") == TARGET)
    check("⑧ 控制台说明交回文本来自**标准输入**，不打印 None",
          "（标准输入/粘贴的文本）" in out_s and "None" not in out_s,
          repr(out_s.splitlines()[:2]))

with tempfile.TemporaryDirectory() as tmp:
    target, _ = fixture(tmp)
    before, mt = read(target), mtime(target)
    code, out, err = run_cli_stdin("   \n\t\n", "--returned-stdin", "--merge-into", target)
    check("⑧ 空 stdin → 退出码 2 + 中文用法错误（不许长得像「0 条、没问题」）",
          code == 2 and "--returned-stdin 收到的是空输入" in err, f"code={code}")
    check("⑧ 空 stdin 时目标一个字节没动、也没有任何新文件",
          read(target) == before and mtime(target) == mt
          and not os.path.exists(target + ".ai前")
          and not os.path.exists(os.path.join(tmp, "reference_合并记录.md")))

with tempfile.TemporaryDirectory() as tmp:
    target, returned = fixture(tmp)
    code, _, err = run_cli_stdin(RETURNED, "--returned-stdin",
                                 "--returned", returned, "--merge-into", target)
    check("⑧ 同给 `--returned` 与 `--returned-stdin` → 退出码 2（互斥：体检的是哪一份必须唯一）",
          code == 2, f"code={code} err={err.strip().splitlines()[-1][:60] if err.strip() else ''}")
    check("⑧ 互斥报错时目标没动", read(target) == TARGET)

# GB18030 的 stdin：编码提示要跟着进合并记录/报告链（与读文件的降级口径一致）
with tempfile.TemporaryDirectory() as tmp:
    target, _ = fixture(tmp)
    gbk = "% ── 中文注释：粘贴来的 bib ──\n\n" + RETURNED
    code, out, _ = run_cli_stdin(gbk, "--returned-stdin", "--merge-into", target, encoding="gb18030")
    check("⑧ GB18030 的 stdin → 提示编码、照旧并回（退出码 1）",
          code == 1 and "gb18030" in out, f"code={code} 含提示={'gb18030' in out}")

# ── ⑨ 列表模式：`--list-keys` 只预览、永不写文件 ────────────────────────────

print("\n══ ⑨ 列表模式 --list-keys ══")

with tempfile.TemporaryDirectory() as tmp:
    target, returned = fixture(tmp)
    rep = os.path.join(tmp, "报告.md")
    before = read(target)
    code, out, err = run_cli("--returned", returned, "--list-keys",
                             "--out", rep, "--merge-into", target)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    check("⑨ 只打**一行**「N 条：…」（stdout 干净到界面可以直接当预览行）",
          code == 0 and len(lines) == 1 and lines[0].startswith("4 条：") and "Burge1984" in lines[0],
          repr(lines))
    check("⑨ **永不写文件**：给了 --out 与 --merge-into 也不写、不并回",
          read(target) == before
          and not os.path.exists(rep)
          and not os.path.exists(target + ".ai前")
          and not os.path.exists(os.path.join(tmp, "reference_合并记录.md")))
    check("⑨ 「不并回」这句只在 stderr 说（stdout 保持一行）",
          "--list-keys" in err and "不并回" in err)

    code2, out2, _ = run_cli_stdin(RETURNED, "--returned-stdin", "--list-keys")
    check("⑨ 也支持 --returned-stdin，且与文件模式同一行",
          code2 == 0 and out2.strip() == lines[0], repr(out2))

    # 0 条：粘贴半截 / 粘成散文时最常见——必须给一句人话，而不是空行
    code3, out3, _ = run_cli_stdin("这不是 bib，只是一段普通的话。\n", "--returned-stdin", "--list-keys")
    check("⑨ 解析不出条目 → 一行中文提示、退出码仍是 0（预览不判好坏）",
          code3 == 0 and out3.startswith("0 条：") and "没解析出任何条目" in out3, repr(out3))

    # 超过 8 个键 → `…` 收尾（预览要短）
    many = "".join(f"@book{{K{i:02d},\n  title = {{T{i}}}\n}}\n\n" for i in range(12))
    code4, out4, _ = run_cli_stdin(many, "--returned-stdin", "--list-keys")
    check("⑨ 超过 8 个键 → 前 8 个 + `…` 收尾",
          code4 == 0 and out4.startswith("12 条：K00, K01,") and "K08" not in out4 and out4.rstrip().endswith("…"),
          repr(out4))

print(f"\n{'=' * 66}")
print(f"失败 {len(FAILS)} 项" + (f"：{FAILS}" if FAILS else "（全部通过）"))
sys.exit(1 if FAILS else 0)
