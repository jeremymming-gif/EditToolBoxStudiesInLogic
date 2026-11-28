"""Detailed legacy compiler result; tuple API and structured fields share one executor."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
from pathlib import Path
import uuid

from .files import RepairError, absolute, digest, read

_SINK = ContextVar("compilefix_detailed_sink", default=None)


@dataclass(frozen=True)
class DetailedResult:
    ok: bool
    info: str
    phase: str
    returncode: int | None
    run_id: str
    candidate_sha256: str | None
    cleaned: tuple[str, ...] = ()
    cleanup_remaining: tuple[str, ...] = ()

    @property
    def identity(self):
        return self.run_id

    def legacy(self):
        return self.ok, self.info


@contextmanager
def collecting_results():
    results = []
    token = _SINK.set(results)
    try:
        yield results
    finally:
        _SINK.reset(token)


def execute(cc, out_dir, stem, *, run_xelatex=None, run_biber=None):
    """Keep historical messages, cleanup and tolerated Biber statuses exactly."""
    run_id = uuid.uuid4().hex
    try:
        candidate_hash = digest(read(absolute(Path(out_dir) / (stem + ".tex"))))
    except (OSError, RepairError):
        candidate_hash = None
    cleaned = tuple(cc.clean_stale_compile_artifacts(out_dir, stem))
    remaining = tuple(ext for ext in cc._STALE_COMPILE_EXTS
                      if os.path.lexists(os.path.join(out_dir, stem + ext)))

    def finish(ok, info, phase, code=None):
        result = DetailedResult(ok, info, phase, code, run_id, candidate_hash, cleaned, remaining)
        if (sink := _SINK.get()) is not None:
            sink.append(result)
        return result

    steps = [["xelatex", "-interaction=nonstopmode", f"{stem}.tex"],
             ["biber", "-l=zh_pinyin", stem],
             ["xelatex", "-interaction=nonstopmode", f"{stem}.tex"],
             ["xelatex", "-interaction=nonstopmode", f"{stem}.tex"]]
    for i, cmd in enumerate(steps, 1):
        phase = "biber" if i == 2 else f"xelatex-{i}"
        if cc.shutil.which(cmd[0]) is None:
            return finish(False, f"找不到命令 {cmd[0]}，请确认已安装 TeX Live", "missing_" + cmd[0])
        from .. import platform as plat, sortlocale as sort
        if i == 2 and run_biber is not None:
            result = run_biber(plat.run_no_window, cmd, out_dir)
        elif i != 2 and run_xelatex is not None:
            result = run_xelatex(plat.run_no_window, cmd, out_dir, i)
        else:
            result = plat.run_no_window(cmd, cwd=out_dir, quiet=True, check=False)
        if i == 2 and (err := sort.guard_after_biber(out_dir, stem)):
            return finish(False, err, phase, result.returncode)
        if result.returncode == 0:
            continue
        if i == 2:
            if not os.path.exists(os.path.join(out_dir, f"{stem}.bcf")):
                continue
            bbl = os.path.join(out_dir, f"{stem}.bbl")
            bbl_ok = os.path.exists(bbl) and os.path.getsize(bbl) > 0
            errors = cc._tail_log_lines(os.path.join(out_dir, f"{stem}.blg"), prefix="", contains="ERROR")
            if bbl_ok and not errors:
                continue
            detail = "；".join(errors) or "未生成 .bbl（参考文献为空）"
            return finish(False, f"biber 第 {i} 步失败（退出码 {result.returncode}）：{detail}"
                          f"（请检查 .bib 是否有语法错误，见 {stem}.blg）", phase, result.returncode)
        detail = "；".join(cc._tail_log_lines(os.path.join(out_dir, f"{stem}.log")))
        return finish(False, f"{cmd[0]} 第 {i} 步失败（退出码 {result.returncode}）"
                      f"{('：' + detail) if detail else ''}", phase, result.returncode)
    pdf = os.path.join(out_dir, f"{stem}.pdf")
    return (finish(True, pdf, "complete", 0) if os.path.exists(pdf)
            else finish(False, "编译结束但未生成 PDF", "missing_pdf"))
