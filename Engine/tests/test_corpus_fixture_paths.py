#!/usr/bin/env python3.14
r"""语料取样锚点：**唯一**一处决定「按稳定期号/序号，而不是作者名，去挑真实稿」。

为什么需要它（2026-11-15 门禁复盘，案例见 `Engine/tests/test_tail_block.py` ④ 组）：

  · 各套件原来把 ``"<期号>/<NN 作者名>"`` 整段写死在路径里。**期号与序号是真的，
    作者名却是文档里的示例化名**（`slcn/naming.py` 里 `李雷 → Lei_Li` 那套）。
    于是同一份稿件在真实语料里叫 `10 <作者罗马名>`，套件却去找 `10 Lei_Li` —— 文件明明在，
    断言却一律 `SKIP`，「本机没有这份语料」这句话是错的。
  · 修法**不是**把真名抄进公开仓（那既泄真实材料、又换个名字继续脆），而是
    **只按稳定标识取样**：期号目录名 + 序号前缀 + 调用方本来就有的文件名片段（needle）。
    序号 `10` 在同一期里唯一，needle 又把「哪一篇的哪一稿」钉住。

本模块只做三件事，且都是纯函数（不改语料、不写盘）：

  ① `slot_dirs()` / `docx_files()` —— 按期号+序号定位候选目录，列出**能打开**的 .docx；
  ② `resolve()` —— 目录/文件两种取样，返回 `Resolution` 而不是 `""`，
     把「目录显示名变了」「序号重号」「多份都匹配」判成不同的状态；
  ③ `Resolution` 的三档语义 —— `ok` 跑断言、`missing` 如实 SKIP、`fatal` 必须 FAIL。

**为什么 `fatal` 必须 FAIL 而不是 SKIP**：`ambiguous`（同一 needle 命中多份）与
`dup_ordinal`（同期同序号两个目录）都是**取样代码自己的问题**，不是「本机没有语料」。
把它们算成 SKIP，等于用一句「本机没有」盖住一个已知能定位却没定位的缺陷 ——
这正是 2026-11-15 复盘点名的那类假绿。反过来，`missing`（期号目录/序号目录/needle
确实不存在）才是合法的 SKIP：那种稿子真的不在授权语料里。

**根目录解析一律走 `slcn.paths`**（`manuscripts_root()`），带两个刻意的性质：

  · 设了 `SLCN_MANUSCRIPTS` 就以它为准，**指错也不回退**（→ `NO_ROOT`，如实 SKIP）；
  · 没设才自动探测。绝不「这一层没找到，就悄悄换一层」。

匿名临时布局的自检（5 类反例）本文件自带，只有 `__main__` 才跑；被别的套件 import 时
只提供函数，不产生任何输出。反例布局全是 `AAAA`/`needle` 这类占位符，不含任何真实
作者名、标题或正文。
"""
import os
import re
import sys
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.dirname(_HERE)
if _ENGINE not in sys.path:
    sys.path.insert(0, _ENGINE)

from slcn import paths as _paths  # noqa: E402

#: 期号目录名：`2025年第4期(83)` / `2022年第6A期(67A)`
ISSUE_RE = re.compile(r"^\d{4}年第\S*期\(\S+\)$")

#: 序号目录名的前缀：`10 <作者罗马名>` / `10 <作者 罗马名>`（分隔符可能是空格或下划线）
_ORD_RE = re.compile(r"^(\d+)(?:[ _]|$)")

# ── 状态（故意用短字符串，日志里眼读即可）───────────────────────────────────
OK = "ok"                      # 定位到唯一一份 → 跑原断言
NO_ROOT = "no-root"            # 本机没有授权语料库 → 合法 SKIP
NO_ISSUE = "no-issue"          # 语料库里没有这个期号 → 合法 SKIP
NO_ORDINAL = "no-ordinal"      # 该期没有这个序号 → 合法 SKIP
NO_MATCH = "no-match"          # 序号目录在，但没有文件名含 needle → 合法 SKIP
DUP_ORDINAL = "dup-ordinal"    # 同期同序号两个目录 → 取样代码的错，必须 FAIL
AMBIGUOUS = "ambiguous"        # 同一 needle 命中多份 → 不许随便挑，必须 FAIL
BAD_REF = "bad-ref"            # ref 写法就不对 → 取样代码的错，必须 FAIL

_MISSING = frozenset({NO_ROOT, NO_ISSUE, NO_ORDINAL, NO_MATCH})
_FATAL = frozenset({DUP_ORDINAL, AMBIGUOUS, BAD_REF})


class Resolution:
    """一次取样结论。`path` 只在 `ok` 时有意义；`detail` 一律可直接打进日志。"""

    __slots__ = ("status", "path", "detail", "candidates", "ref")

    def __init__(self, status, ref="", path="", detail="", candidates=()):
        self.status = status
        self.ref = ref
        self.path = path
        self.detail = detail
        self.candidates = tuple(candidates)

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def missing(self) -> bool:
        """真的没有 → 调用方 SKIP（合法）。"""
        return self.status in _MISSING

    @property
    def fatal(self) -> bool:
        """取样代码的错 → 调用方 FAIL（不许降级成 SKIP）。"""
        return self.status in _FATAL

    def __repr__(self) -> str:
        return (f"Resolution({self.status!r}, ref={self.ref!r}, path={self.path!r}, "
                f"detail={self.detail!r})")


# ══ 根目录 ══════════════════════════════════════════════════════════════════
def default_root() -> str | None:
    """授权语料库根；没有就 None（**不**回退到别的层）。"""
    return _paths.manuscripts_root()


def root_hint() -> str:
    """SKIP 行里那句「为什么没语料 / 怎么配」。"""
    return _paths.describe()


# ══ 定位 ════════════════════════════════════════════════════════════════════
def parse_ref(ref: str):
    """`"2025年第4期(83)/02 Ming_Tang"` → `("2025年第4期(83)", "02")`。

    序号后面那截（作者名）**一律忽略** —— 它可以是任何化名，也不影响取样。
    期号后面不带序号时返回 `(issue, None)`，表示「在这一期里按 needle 找」。
    """
    s = str(ref).strip().replace("\\", "/").strip("/")
    if "/" in s:
        issue, rest = s.split("/", 1)
    else:
        issue, rest = s, ""
    issue = issue.strip()
    if not ISSUE_RE.match(issue):
        raise ValueError(f"期号目录名不认识：{issue!r}（应形如 2025年第4期(83)）")
    m = _ORD_RE.match(rest.strip())
    return issue, (m.group(1) if m else None)


def slot_dirs(root: str, issue: str, ordinal: str | None = None) -> list[str]:
    """`<root>/<issue>/` 下按序号前缀命中的目录（绝对路径，已排序）。

    `ordinal is None` ⇒ 该期所有序号目录。**显示名不参与判断**：目录叫
    `10 <作者罗马名>` 还是 `10 某某` 都只认前两位序号。
    """
    idir = os.path.join(root, issue)
    if not os.path.isdir(idir):
        return []
    out = []
    for name in sorted(os.listdir(idir)):
        p = os.path.join(idir, name)
        if not os.path.isdir(p):
            continue
        if ordinal is None:
            out.append(p)
            continue
        m = _ORD_RE.match(name)
        if m and m.group(1) == ordinal:
            out.append(p)
    return out


def docx_files(slot: str) -> list[tuple[str, str]]:
    """`slot` 下**能打开**的 .docx：`[(绝对路径, 文件名)]`，跳过 Word 锁文件与坏 zip。"""
    out = []
    for cur, _dirs, files in os.walk(slot):
        for fn in sorted(files):
            if fn.startswith("~$") or not fn.lower().endswith(".docx"):
                continue
            p = os.path.join(cur, fn)
            if zipfile.is_zipfile(p):
                out.append((p, fn))
    return out


def _match(slot: str, needle: str):
    return [(p, fn) for p, fn in docx_files(slot) if needle in fn]


def resolve(root: str | None, ref: str, needle: str = "",
            exclude: str = "") -> Resolution:
    """在 `root` 里按 `ref`（+ `needle`）取一份 .docx；结论见 `Resolution`。

    判定顺序（**先找料、再谈歧义**）：
      · `needle` 非空 ⇒ 在该序号（同序号若有多个目录，则**逐目录**）里找文件名含 needle 的；
        命中 0 份 ⇒ `no-match`（材料真不在，合法 SKIP）；1 份 ⇒ `ok`；多份 ⇒ `ambiguous`（必须 FAIL）。
      · `needle` 为空 ⇒ 要求该定位下**恰好一份** .docx，否则 `ambiguous`。

    `exclude` 是可选的**稿件类型**排除词（例如把「作者信息」这类元数据附表排除掉）。
    它只按文件名里的类型字面量排除，**不参与**断言结果判断；排除后仍要求唯一命中。
    """
    if root is None:
        return Resolution(NO_ROOT, ref, detail=root_hint())
    try:
        issue, ordinal = parse_ref(ref)
    except ValueError as e:
        return Resolution(BAD_REF, ref, detail=str(e))

    slots = slot_dirs(root, issue, ordinal)
    if not slots:
        idir = os.path.join(root, issue)
        if not os.path.isdir(idir):
            return Resolution(NO_ISSUE, ref, detail=f"语料库里没有期号目录 {issue}")
        return Resolution(NO_ORDINAL, ref, detail=f"{issue} 下没有序号 {ordinal} 的目录")
    if ordinal is None and not needle:
        return Resolution(BAD_REF, ref,
                          detail="只给期号时必须给 needle，否则等于在该期里瞎挑一份")

    if not needle:
        if len(slots) > 1:
            return Resolution(DUP_ORDINAL, ref,
                              detail=f"{issue} 序号 {ordinal} 有 {len(slots)} 个目录：" +
                                     "、".join(os.path.basename(s) for s in slots) +
                                     "（没给 needle，无法判断是哪一份）",
                              candidates=[os.path.basename(s) for s in slots])
        slot = slots[0]
        found = docx_files(slot)
        if not found:
            return Resolution(NO_MATCH, ref,
                              detail=f"{os.path.basename(slot)} 下没有可打开的 .docx")
        if len(found) > 1:
            return Resolution(AMBIGUOUS, ref,
                              detail=f"{os.path.basename(slot)} 下有 {len(found)} 份 .docx，"
                                     "而这里没有给 needle 去钉住是哪一份",
                              candidates=[os.path.relpath(p, slot) for p, _ in found])
        return Resolution(OK, ref, path=found[0][0],
                          detail=os.path.relpath(found[0][0], root))

    # 同期同序号多个目录时，序号本身**不足以定身份**；此时靠 needle 唯一命中来定，
    # 并把「重号」这件事写进 detail（诊断，不静默）。命中多份仍必须 FAIL。
    dup = ""
    if len(slots) > 1:
        dup = (f"[{issue} 序号 {ordinal} 有 {len(slots)} 个目录："
               + "、".join(os.path.basename(s_) for s_ in slots) + "] ")

    hits = [h for s_ in slots for h in _match(s_, needle)]
    dropped = [(p, fn) for p, fn in hits if exclude and exclude in fn]
    if dropped:
        hits = [(p, fn) for p, fn in hits if p not in {q for q, _ in dropped}]
    if not hits:
        where = ("、".join(os.path.basename(s_) for s_ in slots) if len(slots) > 1
                 else os.path.basename(slots[0]))
        n = sum(len(docx_files(s_)) for s_ in slots)
        tail = ""
        if dropped:
            tail = "（已按稿件类型排除：" + "、".join(fn for _p, fn in dropped) + "）"
        return Resolution(NO_MATCH, ref,
                          detail=dup + f"{where} 下 {n} 份 .docx 里没有文件名含 "
                                        f"{needle!r} 的" + tail)
    if len(hits) > 1:
        return Resolution(AMBIGUOUS, ref,
                          detail=dup + f"文件名含 {needle!r} 的有 {len(hits)} 份，"
                                       "取样不许随便挑一份",
                          candidates=[os.path.relpath(p, root) for p, _ in hits])
    return Resolution(OK, ref, path=hits[0][0],
                      detail=dup + os.path.relpath(hits[0][0], root))


def resolve_dir(root: str | None, ref: str) -> Resolution:
    """取序号目录本身（调用方要自己再往下走 `original/` 之类）。"""
    if root is None:
        return Resolution(NO_ROOT, ref, detail=root_hint())
    try:
        issue, ordinal = parse_ref(ref)
    except ValueError as e:
        return Resolution(BAD_REF, ref, detail=str(e))
    if ordinal is None:
        return Resolution(BAD_REF, ref, detail="resolve_dir 需要带序号，例如 2026年第5期(90)/10")
    slots = slot_dirs(root, issue, ordinal)
    if not slots:
        idir = os.path.join(root, issue)
        if not os.path.isdir(idir):
            return Resolution(NO_ISSUE, ref, detail=f"语料库里没有期号目录 {issue}")
        return Resolution(NO_ORDINAL, ref, detail=f"{issue} 下没有序号 {ordinal} 的目录")
    if len(slots) > 1:
        return Resolution(DUP_ORDINAL, ref,
                          detail=f"{issue} 序号 {ordinal} 有 {len(slots)} 个目录：" +
                                 "、".join(os.path.basename(s) for s in slots),
                          candidates=[os.path.basename(s) for s in slots])
    return Resolution(OK, ref, path=slots[0], detail=os.path.relpath(slots[0], root))


#: 排版模板文件名里出现这些词的 `.tex` 不是「人工成品」：`EN_template.tex` / `SLCN.sty` 之类
_TEX_NOT_MAIN = ("template", "slcn", "slen", "empty", "sample")


def main_tex(slot: str) -> list[tuple[str, str]]:
    """`slot` 下**人工成品** `.tex`：`[(绝对路径, 文件名)]`，按名排序。

    与 `docx_files()` 同一套纪律：只回**候选**，一个以上由调用方判歧义，
    绝不 `hits[0]`。模板类（`EN_template.tex`）按文件名里的类型词排除 ——
    排除依据是「文件类型」，不是「哪份能让断言过」。
    """
    out = []
    for fn in sorted(os.listdir(slot)):
        low = fn.lower()
        if not low.endswith(".tex") or any(k in low for k in _TEX_NOT_MAIN):
            continue
        p = os.path.join(slot, fn)
        if os.path.isfile(p):
            out.append((p, fn))
    return out


def resolve_tex(root: str | None, ref: str) -> Resolution:
    """取该序号目录下的**人工成品** `.tex`（用于「与人工成品逐条对账」这类断言）。

    素材身份同样只按期号+序号定；成品文件名里那截作者名是文档示例化名，**不参与**。
    0 份 ⇒ `no-match`（合法 SKIP，成品确实不在）；多份 ⇒ `ambiguous`（必须 FAIL）。
    """
    res = resolve_dir(root, ref)
    if not res.ok:
        return res
    found = main_tex(res.path)
    if not found:
        return Resolution(NO_MATCH, ref,
                          detail=f"{os.path.basename(res.path)} 下没有人工成品 .tex")
    if len(found) > 1:
        return Resolution(AMBIGUOUS, ref,
                          detail=f"{os.path.basename(res.path)} 下有 {len(found)} 份人工成品 .tex，"
                                 "取样不许随便挑一份",
                          candidates=[fn for _p, fn in found])
    return Resolution(OK, ref, path=found[0][0],
                      detail=os.path.relpath(found[0][0], root))


def resolve_main(root: str | None, ref: str) -> Resolution:
    """取该序号目录下的**正文档**（不靠文件名：`slcn.sources.pick_main_docx` 按内容打分）。

    适用场景：调用方要的就是「这篇稿的正文」，并不关心它被作者存成了什么名字。
    判据全在内容里（长度、有无参考文献/摘要/关键词/章节标题，文件名只作减分项），
    因此**不可能**按「哪份能让断言过」来挑 —— 挑选逻辑不在测试里，也没读断言。

    该目录一份 docx 都没有 ⇒ `no-match`（合法 SKIP）；有 docx 但都不像正文
    （例如正文其实只有 PDF）⇒ 也是 `no-match`，并把 `pick_main_docx` 的理由原样带出，
    这是**材料形态**问题而非取样缺陷；目录本身不唯一 ⇒ 交给 `resolve_dir` 判 `fatal`。
    """
    res = resolve_dir(root, ref)
    if not res.ok:
        return res
    found = [p_ for p_, _fn in docx_files(res.path)]
    if not found:
        return Resolution(NO_MATCH, ref,
                          detail=f"{os.path.basename(res.path)} 下没有可打开的 .docx")
    from slcn import sources as _sources

    best, why = _sources.pick_main_docx(found)
    if not best:
        return Resolution(NO_MATCH, ref,
                          detail=f"{os.path.basename(res.path)} 下没有正文档（{why}）")
    return Resolution(OK, ref, path=best,
                      detail=f"{os.path.basename(res.path)}｜{why}")


def resolve_issue(root: str | None, issue: str) -> Resolution:
    """取**期号目录本身**（整期级材料：合并好的成品 pdf、整期统计等）。

    只认期号，不涉及序号——因此也不会因为某一篇的作者名而对不上。
    期号目录不存在 ⇒ `no-issue`（合法 SKIP）；期号写法不认识 ⇒ `bad-ref`（必须 FAIL）。
    """
    if root is None:
        return Resolution(NO_ROOT, issue, detail=root_hint())
    issue = str(issue).strip().replace("\\", "/").strip("/")
    if "/" in issue:
        return Resolution(BAD_REF, issue, detail="resolve_issue 只接受期号，不接受带序号的 ref")
    if not ISSUE_RE.match(issue):
        return Resolution(BAD_REF, issue,
                          detail=f"期号目录名不认识：{issue!r}（应形如 2025年第4期(83)）")
    idir = os.path.join(root, issue)
    if not os.path.isdir(idir):
        return Resolution(NO_ISSUE, issue, detail=f"语料库里没有期号目录 {issue}")
    return Resolution(OK, issue, path=idir, detail=os.path.relpath(idir, root))


def any_files(slot: str, suffix: str) -> list[tuple[str, str]]:
    """`slot` 下后缀为 `suffix` 的文件：`[(绝对路径, 文件名)]`（跳过 Word 锁文件）。

    与 `docx_files()` 的区别只有一个：**不要求能被 zipfile 打开**。成品件
    （`reference.bib`、合并好的 `.pdf`）本来就不是 docx，用 `docx_files` 找只会永远
    `no-match` —— 那正是「材料在、断言却 SKIP」的第二种成因。
    """
    out = []
    low = suffix.lower()
    for cur, _dirs, files in os.walk(slot):
        for fn in sorted(files):
            if fn.startswith("~$") or not fn.lower().endswith(low):
                continue
            p = os.path.join(cur, fn)
            if os.path.isfile(p):
                out.append((p, fn))
    return out


def resolve_file(root: str | None, ref: str, needle: str, suffix: str,
                 exclude: str = "") -> Resolution:
    """取该序号目录下的**成品件**（非 .docx）：`suffix` 定类型，`needle` 定是哪一份。

    只按期号 + 序号 + 文件名片段定位，**不写作者名**（目录里那截作者名是文档示例化名）。
    0 份 ⇒ `no-match`（合法 SKIP：成品确实不在本机）；多份 ⇒ `ambiguous`（必须 FAIL：
    取样代码没把身份钉死，不许随便挑一份让断言过）。
    """
    if root is None:
        return Resolution(NO_ROOT, ref, detail=root_hint())
    if not needle:
        return Resolution(BAD_REF, ref,
                          detail="resolve_file 必须给 needle，否则等于在该序号目录里瞎挑一份")
    try:
        issue, ordinal = parse_ref(ref)
    except ValueError as e:
        return Resolution(BAD_REF, ref, detail=str(e))
    if ordinal is None:
        # 只给期号：成品就摆在期号目录里（例如整期合并好的 `合并*.pdf`）。
        # 这里**只看本层**，不往各序号目录里递归 —— 否则整期的稿子会冒充「本期成品」。
        idir = os.path.join(root, issue)
        if not os.path.isdir(idir):
            return Resolution(NO_ISSUE, ref, detail=f"语料库里没有期号目录 {issue}")
        hits = [(os.path.join(idir, fn), fn) for fn in sorted(os.listdir(idir))
                if fn.lower().endswith(suffix.lower())
                and os.path.isfile(os.path.join(idir, fn))]
        hits = [(p_, fn) for p_, fn in hits if needle in fn]
        if not hits:
            return Resolution(NO_MATCH, ref,
                              detail=f"{issue} 目录本层没有文件名含 {needle!r} 的 {suffix}")
        if len(hits) > 1:
            return Resolution(AMBIGUOUS, ref,
                              detail=f"{issue} 目录本层文件名含 {needle!r} 的 {suffix} "
                                     f"有 {len(hits)} 份，取样不许随便挑一份",
                              candidates=[fn for _p_, fn in hits])
        return Resolution(OK, ref, path=hits[0][0],
                          detail=os.path.relpath(hits[0][0], root))
    res = resolve_dir(root, ref)
    if not res.ok:
        return res
    slot = res.path
    hits = [(p, fn) for p, fn in any_files(slot, suffix) if needle in fn]
    dropped = [(p, fn) for p, fn in hits if exclude and exclude in fn]
    if dropped:
        hits = [(p, fn) for p, fn in hits if p not in {q for q, _ in dropped}]
    if not hits:
        n = len(any_files(slot, suffix))
        tail = ("（已按类型排除：" + "、".join(fn for _p, fn in dropped) + "）") if dropped else ""
        return Resolution(NO_MATCH, ref,
                          detail=f"{os.path.basename(slot)} 下 {n} 份 {suffix} 里没有文件名含 "
                                 f"{needle!r} 的" + tail)
    if len(hits) > 1:
        return Resolution(AMBIGUOUS, ref,
                          detail=f"{os.path.basename(slot)} 下文件名含 {needle!r} 的 {suffix} "
                                 f"有 {len(hits)} 份，取样不许随便挑一份",
                          candidates=[os.path.relpath(p, root) for p, _ in hits])
    return Resolution(OK, ref, path=hits[0][0],
                      detail=os.path.relpath(hits[0][0], root))


def skip_line(res: Resolution) -> str:
    """合法 SKIP 的统一措辞：把**真实**的材料身份与原因写清楚（不是「本机没有」了事）。

    结构护栏：`fatal`（重号 / 多命中 / ref 写坏）是**取样代码自己的错**，必须 FAIL。
    这里直接抛 `AssertionError`，于是任何调用点漏写 `.fatal` 守卫都会把套件炸成
    rc≠0 的 FAIL，而不会被 `if not ok: skip(...)` 顺手吞成一条 SKIP —— 那正是
    「拿 SKIP 盖住自己的 bug」的入口。

    调用点有两种都**安全**的写法（本仓两种都在用）：

        # 甲：互斥两支，报错位置最准（推荐）
        if res.fatal:
            check(f"取样代码的错（{res.status}）：{res.ref}", False, res.detail)
        elif not res.ok:
            skip("...", skip_line(res))
        else:
            ...用 res.path...

        # 乙：fatal 已经在 helper 里 check() 记过 FAIL，调用点只管缺料
        if not res.ok:
            print(f"SKIP  {skip_line(res)}")      # fatal 走到这里会抛 → rc≠0
        else:
            ...用 res.path...

    乙之所以也安全：`fatal` 同样满足 `not ok`，于是 `skip_line()` 当场抛异常，套件以
    非零码退出（`fatal` 的 FAIL 行在 helper 里已经打印过）。代价是报错位置不如甲清楚，
    但**绝不会**把取样 bug 变成一条绿色 SKIP。任何试图「先 catch 住再 skip」的写法
    都会重新打开这个洞。"""
    if res.fatal:
        raise AssertionError(
            f"取样代码的错（{res.status}）：{res.ref}  {res.detail}"
            "  —— fatal 不许当 SKIP；调用点应先 check() 记 FAIL"
        )
    return f"{res.ref}（{res.detail}）"


# ══ 自检：匿名临时布局的 5 类反例（只在 __main__ 跑）══════════════════════════
def _selftest() -> int:
    import shutil
    import tempfile

    FAILS = []

    def check(name, cond, detail=""):
        print(("PASS  " if cond else "FAIL  ") + name + ("  " + detail if detail else ""))
        if not cond:
            FAILS.append(name)

    def mkdocx(path):
        """造一个**真的**能打开的 zip（`is_zipfile` 要过）。"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", "<w:document/>")

    tmp = tempfile.mkdtemp(prefix="slcn-fixture-selftest-")
    try:
        root = os.path.join(tmp, "corpus")
        # ① 目录显示名变了，稳定标识（期号+序号）没变 —— 必须照样取到
        mkdocx(os.path.join(root, "2024年第1期(21)", "01 ZZZZ_Renamed", "needle-a.docx"))
        r = resolve(root, "2024年第1期(21)/01 AAAA_Original", "needle-a")
        check("① 显示名变了、序号没变 → 仍取到", r.ok and r.path.endswith("needle-a.docx"),
              r.detail)

        # ② 错期/错序号不许串到别的样本上
        mkdocx(os.path.join(root, "2024年第1期(21)", "02 BBBB_Other", "needle-b.docx"))
        r_same_issue = resolve(root, "2024年第1期(21)/01", "needle-b")
        check("② 同期错序号 → 不串（no-match）",
              r_same_issue.status == NO_MATCH, r_same_issue.detail)
        mkdocx(os.path.join(root, "2024年第2期(22)", "01 CCCC_Later", "needle-c.docx"))
        r_wrong_issue = resolve(root, "2024年第1期(21)/01", "needle-c")
        check("② 错期号 → 不串（no-match）", r_wrong_issue.status == NO_MATCH,
              r_wrong_issue.detail)

        # ③ 多份都匹配 → 不许随便挑一份
        mkdocx(os.path.join(root, "2024年第1期(21)", "03 DDDD_Two", "needle-d-v1.docx"))
        mkdocx(os.path.join(root, "2024年第1期(21)", "03 DDDD_Two", "needle-d-v2.docx"))
        r_amb = resolve(root, "2024年第1期(21)/03", "needle-d")
        check("③ 两份都匹配 → ambiguous 且必须 FAIL（不是挑第一份）",
              r_amb.status == AMBIGUOUS and r_amb.fatal and len(r_amb.candidates) == 2,
              r_amb.detail)

        # ③b 同期同序号两个目录（重号）：needle 两边都命中 ⇒ 仍然是 ambiguous，必须 FAIL
        mkdocx(os.path.join(root, "2024年第1期(21)", "05 EEEE_A", "needle-e.docx"))
        mkdocx(os.path.join(root, "2024年第1期(21)", "05 FFFF_B", "needle-e.docx"))
        r_dup = resolve(root, "2024年第1期(21)/05", "needle-e")
        check("③b 序号重号且两边都命中 → ambiguous 且必须 FAIL",
              r_dup.status == AMBIGUOUS and r_dup.fatal, r_dup.detail)

        # ③b2 序号重号、但 needle 只命中其中一个目录 ⇒ 可取（detail 里必须写明重号）
        mkdocx(os.path.join(root, "2024年第1期(21)", "05 FFFF_B", "needle-e2.docx"))
        r_dup2 = resolve(root, "2024年第1期(21)/05", "needle-e2")
        check("③b2 重号但 needle 唯一命中 → ok，且 detail 明写重号",
              r_dup2.ok and "2 个目录" in r_dup2.detail, r_dup2.detail)

        # ③b3 序号重号、needle 两边都没有 ⇒ no-match（合法 SKIP，不升格成 FAIL）
        r_dup3 = resolve(root, "2024年第1期(21)/05", "needle-absent")
        check("③b3 重号但 needle 谁都不命中 → no-match（合法 SKIP）",
              r_dup3.status == NO_MATCH and r_dup3.missing and "2 个目录" in r_dup3.detail,
              r_dup3.detail)

        # ③b4 序号重号、又没给 needle ⇒ 无法判断是哪一份，必须 FAIL
        r_dup4 = resolve(root, "2024年第1期(21)/05")
        check("③b4 重号且没给 needle → dup-ordinal 且必须 FAIL",
              r_dup4.status == DUP_ORDINAL and r_dup4.fatal, r_dup4.detail)

        # ③c 不要求 needle 时，目录里多于一份也算 ambiguous（不猜）
        r_amb2 = resolve(root, "2024年第1期(21)/03")
        check("③c 没给 needle 而目录里两份 → ambiguous", r_amb2.status == AMBIGUOUS, r_amb2.detail)

        # ④ 显式指错的根不许悄悄回退到别的层
        old = os.environ.get(_paths.ENV_VAR)
        bogus = os.path.join(tmp, "not-a-corpus")
        os.makedirs(bogus, exist_ok=True)
        os.environ[_paths.ENV_VAR] = bogus
        try:
            got = _paths.manuscripts_root()
            r_bad = resolve(default_root(), "2024年第1期(21)/01", "needle-a")
            check("④ 环境变量指到非语料目录 → 根为 None 且 no-root（不回退）",
                  got is None and r_bad.status == NO_ROOT, f"root={got!r} {r_bad.detail}")
            check("④b 指错时 describe() 明说是环境变量指错了",
                  "没有" in _paths.describe() or "跳过" in _paths.describe(),
                  _paths.describe()[:60])
        finally:
            if old is None:
                os.environ.pop(_paths.ENV_VAR, None)
            else:
                os.environ[_paths.ENV_VAR] = old

        # ⑤ 没有语料库 → 如实 SKIP（missing），不是 ok 也不是 fatal
        r_none = resolve(None, "2024年第1期(21)/01", "needle-a")
        check("⑤ 根为 None → no-root / missing（如实 SKIP，不崩）",
              r_none.status == NO_ROOT and r_none.missing and not r_none.fatal, r_none.detail)

        # ⑥ 坏 zip 与 Word 锁文件不算候选
        bad = os.path.join(root, "2024年第1期(21)", "04 GGGG_Bad", "needle-f.docx")
        os.makedirs(os.path.dirname(bad), exist_ok=True)
        open(bad, "wb").write(b"not a zip")
        mkdocx(os.path.join(root, "2024年第1期(21)", "04 GGGG_Bad", "~$needle-f.docx"))
        r_badzip = resolve(root, "2024年第1期(21)/04", "needle-f")
        check("⑥ 坏 zip / `~$` 锁文件不算候选（no-match）",
              r_badzip.status == NO_MATCH, r_badzip.detail)

        # ⑦ 期号语义：ref 里没有序号 ⇒ 在该期里按 needle 找
        r_issue_only = resolve(root, "2024年第2期(22)", "needle-c")
        check("⑦ 只给期号 + needle → 仍取到",
              r_issue_only.ok and r_issue_only.path.endswith("needle-c.docx"),
              r_issue_only.detail)

        # ⑧ 期号写法不对 → bad-ref（取样代码的错，必须 FAIL）
        r_badref = resolve(root, "2019/q1", "needle-a")
        check("⑧ ref 期号写法不认识 → bad-ref 且必须 FAIL",
              r_badref.status == BAD_REF and r_badref.fatal, r_badref.detail)

        # ⑨ 成品件（非 .docx）：`docx_files` 天生看不见它，必须走 `resolve_file`
        slot9 = os.path.join(root, "2024年第3期(23)", "05 HHHH_Fin")
        os.makedirs(slot9, exist_ok=True)
        open(os.path.join(slot9, "needle-g.bib"), "w", encoding="utf-8").write("@misc{k,}\n")
        check("⑨ 成品 .bib 不在 docx 候选里（说明必须有非 docx 取样）",
              docx_files(slot9) == [], repr(docx_files(slot9)))
        r_fin = resolve_file(root, "2024年第3期(23)/05 AAAA_Old", "needle-g", ".bib")
        check("⑨ 显示名变了、序号没变 → 成品 .bib 仍取到",
              r_fin.ok and r_fin.path.endswith("needle-g.bib"), r_fin.detail)

        # ⑨b 同序号两份都含 needle → ambiguous 且必须 FAIL（不许挑第一份）
        open(os.path.join(slot9, "needle-g2.pdf"), "wb").write(b"%PDF-1.4\n")
        open(os.path.join(slot9, "needle-g2-copy.pdf"), "wb").write(b"%PDF-1.4\n")
        r_dup = resolve_file(root, "2024年第3期(23)/05", "needle-g2", ".pdf")
        check("⑨b 成品两份都匹配 → ambiguous 且必须 FAIL",
              r_dup.status == AMBIGUOUS and r_dup.fatal and len(r_dup.candidates) == 2,
              r_dup.detail)

        # ⑨c 不给 needle → bad-ref（否则等于在该目录里瞎挑）
        r_non = resolve_file(root, "2024年第3期(23)/05", "", ".pdf")
        check("⑨c 成品取样不给 needle → bad-ref 且必须 FAIL",
              r_non.status == BAD_REF and r_non.fatal, r_non.detail)

        # ⑨f 整期目录本身也能按「只给期号」取到（不碰序号、不碰作者名）
        r_issdir = resolve_issue(root, "2024年第3期(23)")
        check("⑨f 只给期号 → 取到期号目录本身",
              r_issdir.ok and os.path.basename(r_issdir.path) == "2024年第3期(23)",
              r_issdir.detail)
        check("⑨f2 期号写法不认识 → bad-ref 且必须 FAIL",
              resolve_issue(root, "2024/q1").fatal, "")
        check("⑨f3 期号目录确实没有 → no-issue（合法 SKIP）",
              resolve_issue(root, "2019年第4期(99)").status == NO_ISSUE,
              resolve_issue(root, "2019年第4期(99)").detail)

        # ⑨e 只给期号 → 只在本层找（整期合并好的成品就在这里），不往序号目录里递归
        open(os.path.join(root, "2024年第3期(23)", "needle-h-merged.pdf"), "wb").write(b"%PDF-1.4\n")
        open(os.path.join(slot9, "needle-h-inslot.pdf"), "wb").write(b"%PDF-1.4\n")
        r_iss = resolve_file(root, "2024年第3期(23)", "needle-h", ".pdf")
        check("⑨e 只给期号 → 取到本层成品，且不会递归进序号目录",
              r_iss.ok and r_iss.path.endswith("needle-h-merged.pdf"), r_iss.detail)

        # ⑨d 类型对但确实没有这份 → no-match（合法 SKIP，不是取样错）
        r_none = resolve_file(root, "2024年第3期(23)/05", "needle-zzz", ".pdf")
        check("⑨d 成品确实不在 → no-match（合法 SKIP）",
              r_none.status == NO_MATCH and r_none.missing, r_none.detail)

        # ⑩ `skip_line()` 是结构护栏：fatal 不许被写成 SKIP 行。
        # 合法缺料照常出措辞：把真实身份（ref + 具体原因）写清楚，不是一句「本机没有」
        _line = skip_line(r_none)
        check("⑩ 合法缺料：skip_line() 正常出品（写清身份+原因）",
              isinstance(_line, str) and r_none.ref in _line and r_none.detail in _line,
              _line)
        # ……而 fatal（多命中/重号/ref 写坏）必须当场炸，逼调用点先 check() 记 FAIL，
        # 否则「漏写 .fatal 守卫」就会把取样 bug 悄悄降级成一条 SKIP。
        _thrown = []
        for _r in (r_dup, r_dup4, resolve(root, "2019/q1")):
            try:
                skip_line(_r)
                _thrown.append(f"{_r.status}: 没炸")
            except AssertionError:
                _thrown.append(f"{_r.status}: 炸了")
        check("⑩ fatal 三种（ambiguous / dup-ordinal / bad-ref）都拒绝当 SKIP",
              _thrown == ["ambiguous: 炸了", "dup-ordinal: 炸了", "bad-ref: 炸了"],
              " / ".join(_thrown))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    if FAILS:
        print(f"失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
