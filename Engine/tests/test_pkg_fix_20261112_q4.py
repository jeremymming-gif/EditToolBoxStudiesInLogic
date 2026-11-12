#!/usr/bin/env python3
r"""FIX-20261112 / Q4（EN11-MOVER-01 + §15 v2）：语序搬移的**全部命中**与位序矩阵。

钉住的事实（改前改后都按这一页读）：
  (a) `en_move_marks` 句号分支原来用 `tex[po+1:]` 重建，每次只保住最左一处；
      修复后 `N` 处候选**全部**搬完，且 `n` 与实际修正一致（旧稿自报 450、实改 12）；
  (b) 中英两语 × 句末标点（`。`/`.` 与 `，`/`,`）× 标记类型（脚注/引文）矩阵，
      逐格断言 输入 → 输出；引用依据《排校手册》§校对规范·常规 4.1 / 5.1–5.2；
  (c) 中文 mover（ZH-10 逗号脚注、ZH-11 逗号引文、ZH-21 句号脚注、ZH-23 句号引文组）
      同样一次处理全部、二次调用幂等；
  (d) ZH-23 从「只报」升级为「初排自动改」，但守卫 A/B/同句两组/组内已有句末标点/
      冒号不作句末标点 一律不动。
"""
from __future__ import annotations

import pathlib
import sys

ENGINE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE))

from slcn import proofcheck as P  # noqa: E402

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


def hits(tex: str, lang: str, *rids: str):
    want = set(rids)
    return [h for h in P.check(tex, lang=lang).hits if h.rule_id in want]


def moved(tex: str, spans):
    out, log = P.move_marks_before_punct(tex, spans=spans)
    return out, log


def fixed(tex: str, lang: str, *rids: str) -> tuple[str, int]:
    rep = P.check(tex, lang=lang)
    out, applied = P.apply_fixes(tex, rep, rule_ids=set(rids))
    return out, len(applied)


# ── 0. 根因回归：EN-11 句号后置（多处 = 全处理，不再只剩最左一处）────────────
print("\n[0] EN-11 根因：句号在引文之后的多处搬运")
t = (r"A claim (\parencite{k1}). Another (\parencite{k2}). "
     "Third (\\parencite{k3}).\n"
     r"Fourth (\parencite{k4}). Fifth (\parencite{k5}).")
out, n = P.en_move_marks(t)
check("(0a) 5 处候选一次全部搬完（旧实现只剩最左 1 处）",
      n == 5 and out.count(").") == 0, f"n={n} out={out!r}")
check("(0b) 5 处输出逐处正确",
      out == ("A claim. (\\parencite{k1}) Another. (\\parencite{k2}) "
              "Third. (\\parencite{k3})\nFourth. (\\parencite{k4}) "
              "Fifth. (\\parencite{k5})"),
      repr(out))

# fix_en_marks：外层括号(EN-10) + 句末搬运(EN-11) 一轮做完，条数=实际
t = (r"X \parencite{a}. Y \parencite{b}. Z \parencite{c}. "
     r"W \parencite{d}.")
out, counts = P.fix_en_marks(t)
check("(0c) fix_en_marks 报出的条数 = 实际改动的条数（不再 40× 虚报）",
      counts.get("EN-11") == 4 and counts.get("EN-10") == 4,
      f"counts={counts}")
check("(0d) fix_en_marks 一次性改完 4 处",
      out == ("X. (\\parencite{a}) Y. (\\parencite{b}) Z. (\\parencite{c}) "
              "W. (\\parencite{d})"),
      repr(out))
out2, counts2 = P.fix_en_marks(out)
check("(0e) fix_en_marks 二次调用幂等（0 处、不再变）",
      out2 == out and not counts2, f"counts2={counts2}")

# ── 1. 位序矩阵：2 语言 × 2 标点 × 2 标记类型 ────────────────────────────────
print("\n[1] 位序矩阵（输入 → 输出）")

# 中文 · 逗号 + 脚注（ZH-10，mov er）
t = r"甲说，\footnote{注}乙说。"
hs = hits(t, "zh", "ZH-10")
zh_cf, log = moved(t, [(h.start, h.end) for h in hs])
check("(1-zh-，-footnote) 甲说，\\footnote{注}乙说。 → 甲说\\footnote{注}，乙说。",
      zh_cf == r"甲说\footnote{注}，乙说。" and len(log) == 1, repr(zh_cf))

# 中文 · 逗号 + 引文（ZH-11）
t = r"甲说，\parencite{k}乙说。"
hs = hits(t, "zh", "ZH-11")
zh_cc, log = moved(t, [(h.start, h.end) for h in hs])
check("(1-zh-，-parencite) 甲说，\\parencite{k}乙说。 → 甲说\\parencite{k}，乙说。",
      zh_cc == r"甲说\parencite{k}，乙说。" and len(log) == 1, repr(zh_cc))

# 中文 · 句号 + 脚注（ZH-21，自动改）
t = r"甲说\footnote{注}。乙说。"
hs = hits(t, "zh", "ZH-21")
zh_pf, n = fixed(t, "zh", "ZH-21")
check("(1-zh-。-footnote) 甲说\\footnote{注}。 → 甲说。\\footnote{注}（ZH-21 自动改）",
      bool(hs) and hs[0].fixable and zh_pf == r"甲说。\footnote{注}乙说。" and n == 1,
      f"{zh_pf!r} n={n}")

# 中文 · 句号 + 引文组（ZH-23，v2 起自动改）
t = r"甲说（\parencite{k}）。乙说。"
hs = hits(t, "zh", "ZH-23")
zh_pc, n = fixed(t, "zh", "ZH-23")
check("(1-zh-。-parencite) 甲说（\\parencite{k}）。 → 甲说。（\\parencite{k}）（ZH-23 自动改）",
      bool(hs) and hs[0].fixable and zh_pc == r"甲说。（\parencite{k}）乙说。" and n == 1,
      f"{zh_pc!r} n={n}")

# 英文 · 句号 + 脚注
t = r"Claim (\footnote{x}). More."
en_pf = P.en_move_marks(t)[0]
check("(1-en-.-footnote) Claim (\\footnote{x}). → Claim. (\\footnote{x})",
      en_pf == r"Claim. (\footnote{x}) More.", repr(en_pf))

# 英文 · 句号 + 引文
t = r"Claim (\parencite{k}). More."
en_pc, n = P.en_move_marks(t)
check("(1-en-.-parencite) Claim (\\parencite{k}). → Claim. (\\parencite{k})",
      n == 1 and en_pc == r"Claim. (\parencite{k}) More.", repr(en_pc))

# 英文 · 逗号 + 脚注
t = r"Claim, \footnote{x} more."
en_cf, n = P.en_move_marks(t)
check("(1-en-,-footnote) Claim, \\footnote{x} → Claim \\footnote{x},",
      n == 1 and en_cf == r"Claim \footnote{x}, more.", repr(en_cf))

# 英文 · 逗号 + 引文
t = r"Claim, \parencite{k} more."
en_cc, n = P.en_move_marks(t)
check("(1-en-,-parencite) Claim, \\parencite{k} → Claim \\parencite{k},",
      n == 1 and en_cc == r"Claim \parencite{k}, more.", repr(en_cc))

# ── 2. 中文 mover：多处全处理 + 幂等（不只剩最左）────────────────────────────
print("\n[2] 中文 mover 多处 / 幂等")
t = (r"甲说，\footnote{一}乙说，\footnote{二}丙说，\footnote{三}丁说，"
     r"\footnote{四}戊说。")
hs = hits(t, "zh", "ZH-10")
out, log = moved(t, [(h.start, h.end) for h in hs])
check("(2a) ZH-10 四处全搬（一次到位）",
      out == (r"甲说\footnote{一}，乙说\footnote{二}，丙说\footnote{三}，"
              r"丁说\footnote{四}，戊说。") and len(log) == 4, repr(out))
check("(2b) ZH-10 搬后重查无 ZH-10（幂等）", not hits(out, "zh", "ZH-10"))
out2, _ = P.move_marks_before_punct(out, spans=[])
check("(2c) ZH-10 mover 二次调用不再变", out2 == out)

t = r"甲说，\parencite{a}乙说，\parencite{b}丙说。"
hs = hits(t, "zh", "ZH-11")
out, log = moved(t, [(h.start, h.end) for h in hs])
check("(2d) ZH-11 两处全搬",
      out == r"甲说\parencite{a}，乙说\parencite{b}，丙说。" and len(log) == 2, repr(out))

# ZH-21 多处
t = r"甲说\footnote{注}。乙说\footnote{注2}。"
out, n = fixed(t, "zh", "ZH-21")
check("(2e) ZH-21 两处全改", n == 2 and out == r"甲说。\footnote{注}乙说。\footnote{注2}", repr(out))

# ── 3. ZH-23 守卫：该改的改、该跳的跳 ───────────────────────────────────────
print("\n[3] ZH-23 守卫（§15 v2：守卫 A/B 及其余例外）")


def zh23(tex: str) -> list:
    return [h for h in P.check(tex, lang="zh").hits if h.rule_id == "ZH-23"]


for word in ("参见", "详见", "例如", "譬如", "诸如", "即", "见"):
    t = word + r"（\parencite{k}）。"
    check(f"(3a) 守卫 A：{word} 前置不报", not zh23(t), t)

check("(3a') 守卫 A「见」的例外：意见 后照改（词尾不是独立「见」）",
      bool(zh23(r"意见（\parencite{k}）。")))
check("(3b) 守卫 B：以「；」收尾不报（分号不是句末标点）",
      not zh23(r"甲说（\parencite{k}）；乙说。"))
check("(3c) 同句两组整句跳过",
      not zh23(r"甲说（\parencite{k}）和（\parencite{j}）。"))
check("(3d) 组内已有句末句读不动（正文里有「。」）",
      not zh23(r"甲说（\parencite{k}。另说）。乙说。"))
check("(3e) 冒号不作句末标点（冒号后不搬）",
      not zh23(r"甲说：（\parencite{k}）。"))
check("(3f) 行首/段首无前文不搬", not zh23("\n" + r"（\parencite{k}）。"))
check("(3g) 普通场景仍报（对照组）", bool(zh23(r"甲说（\parencite{k}）。")))

# ── 4. 幂等（EN-10/EN-11/ZH-23/ZH-21）───────────────────────────────────────
print("\n[4] 幂等")
t = (r"X \parencite{a}. Y \footnote{b}. Z (\parencite{c}).")
o1, _ = P.fix_en_marks(t)
o2, c2 = P.fix_en_marks(o1)
check("(4a) EN fix_en_marks 幂等", o1 == o2 and not c2, f"{c2}")

t = r"甲说（\parencite{k}）。乙说（\parencite{j}）。"
o1, _ = fixed(t, "zh", "ZH-23")
o2, n2 = fixed(o1, "zh", "ZH-23")
check("(4b) ZH-23 二次调用 0 改且不再动",
      o1 == o2 and n2 == 0 and not zh23(o1), f"o1={o1!r} n2={n2}")

print("\n" + "=" * 66)
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
    sys.exit(1)
print(f"全部 {pass_n} 项断言通过")
sys.exit(0)
