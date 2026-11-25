"""Fresh, restricted TeX runs with artifact bindings for the exact candidate."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from xml.etree import ElementTree

from .files import (ARTIFACT_LIMIT, DENIED_COMPONENTS, TEXT_LIMIT, RepairError, absolute, atomic_write,
                    digest, directory, read, regular, within)


TOOL_TIMEOUT = 60
LOG_LIMIT = 2 * 1024 * 1024
SUPPORT_COUNT = 256
SUPPORT_LIMIT = 128 * 1024 * 1024
DISPLACED_DIR = ".compilefix-displaced"
COMPILE_EXTENSIONS = (".aux", ".bbl", ".bcf", ".blg", ".run.xml", ".out", ".toc", ".lof", ".lot",
                      ".log", ".pdf", ".fls", ".xdv", ".synctex", ".synctex.gz", ".nav", ".snm")
_STYLES = {"SLEN.sty", "SLCN-MIX.sty", "SLCN-MIX4.sty", "SLCN.sty"}
_ASSET_EXTENSIONS = {".bib", ".pdf", ".png", ".jpg", ".jpeg"}
_LOCAL_CODE_EXTENSIONS = {".tex", ".sty", ".cls", ".ltx", ".cfg", ".def", ".fd", ".clo",
                          ".lua", ".fmt", ".cnf", ".bbx", ".cbx", ".lbx", ".dbx", ".map",
                          ".tfm", ".vf", ".enc", ".pfb", ".otf", ".ttf", ".icc"}
_ACTIVE_FILE_COMMAND = re.compile(
    rb"\^\^|\\(?:@{0,2}input|include|InputIfFileExists|openin|openout|read|write|special|"
    rb"directlua|latelua|pdfextension|pdffilesize|pdffilemoddate|pdfmdfivesum|XeTeXpicfile|"
    rb"XeTeXpdffile|font|ShellEscape|writefile|verbatiminput)\b"
)
_BIB_NAME = re.compile(r"[\w .-]+\.bib\Z", re.IGNORECASE)
_SYSTEM_PREFIXES = tuple(Path(path) for path in (
    "/usr/local/texlive", "/usr/share/texlive", "/usr/share/texmf", "/var/lib/texmf",
    "/System/Library/Fonts", "/Library/Fonts", "/usr/share/fonts", "/usr/local/share/fonts",
))


@dataclass(frozen=True)
class CompileResult:
    state: str
    candidate_sha256: str
    run_id: str
    phase: str
    exit_code: int | None = None
    pdf_sha256: str | None = None
    log_sha256: str | None = None
    recorder_sha256: str | None = None


def support_manifest(out_dir, stem, *, exclude_dir=None):
    """Snapshot only local styles, bibliography and graphics, never other TeX files."""
    excluded = {stem + extension for extension in COMPILE_EXTENSIONS}
    manifest = {}
    count = total = 0
    for parent, names, files in os.walk(out_dir, followlinks=False, onerror=_raise_walk_error):
        location = Path(parent)
        # The converter archives source materials under original/. It is never a TeX input.
        names[:] = [name for name in names if name.casefold() not in DENIED_COMPONENTS | {"original"}
                    and not (location == out_dir and name.casefold() == DISPLACED_DIR)
                    and location / name != exclude_dir and not re.match(r"^\d{4}年第", name)]
        for name in names:
            directory(location / name)
        for name in files:
            count += 1
            if count > SUPPORT_COUNT:
                raise RepairError("产物目录文件数超过复编隔离上限")
            path = location / name
            regular(path)
            if location == out_dir and name in excluded:
                continue
            if location == out_dir and name == f"{stem}.tex":
                continue
            bundled_style = location == out_dir and name in _STYLES
            if not bundled_style and (path.suffix.lower() in _LOCAL_CODE_EXTENSIONS
                                      or name.lower() == "biber.conf"):
                raise RepairError("产物目录含未签发的本地 TeX 支持代码")
            if (path.suffix.lower() not in _ASSET_EXTENSIONS
                    and not bundled_style):
                continue
            if any(part.startswith(".") for part in path.relative_to(out_dir).parts):
                raise RepairError("复编支持文件路径不安全")
            content = read(path, ARTIFACT_LIMIT)
            if bundled_style:
                from ..templates import TPL_DIR

                bundled = Path(TPL_DIR) / name
                if digest(content) != digest(read(bundled)):
                    raise RepairError("本地样式与引擎模板不符，请人工复编")
            total += len(content)
            if total > SUPPORT_LIMIT:
                raise RepairError("支持文件超过复编隔离上限")
            manifest[path.relative_to(out_dir).as_posix()] = digest(content)
    return manifest


def _raise_walk_error(error):
    raise error


def copy_support(out_dir, isolated, manifest):
    for relative, expected in manifest.items():
        source = out_dir / relative
        directory(source.parent)
        contents = read(source, ARTIFACT_LIMIT)
        if digest(contents) != expected:
            raise RepairError("复编支持文件在复制前变化")
        target = isolated / relative
        directory(target.parent, create=True, parents=True)
        atomic_write(target, contents)


def _environment(home):
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update({"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home),
                "LOCALAPPDATA": str(home), "XDG_CACHE_HOME": str(home), "XDG_CONFIG_HOME": str(home),
                "TMPDIR": str(home), "TEMP": str(home), "TMP": str(home), "TEXMFHOME": str(home),
                "openin_any": "p", "openout_any": "p", "shell_escape": "f"})
    return env


def _invoke(command, cwd, env, leases):
    supervisor = Path(__file__).with_name("tool_runner.py")
    invocation = [sys.executable, "-I", "-B", str(supervisor), str(TOOL_TIMEOUT), *command]
    process = subprocess.Popen(invocation, cwd=cwd, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=os.name != "nt",
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               pass_fds=leases if os.name != "nt" else ())
    try:
        code = process.wait(timeout=TOOL_TIMEOUT + 5)
        if code == 124:
            raise subprocess.TimeoutExpired(command, TOOL_TIMEOUT)
        return code
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


_BIBER_DTD = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _biber_sources(path, cwd, support):
    """Validate the Biber control file; return True when it has no datasource entries."""
    content = read(path, TEXT_LIMIT)
    if b"\0" in content or _BIBER_DTD.search(content):
        raise RepairError("Biber 控制文件有外部实体或不受控编码")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise RepairError("Biber 控制文件无法解析") from exc
    datasources = 0
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "datasource":
            continue
        datasources += 1
        name = element.text or ""
        if (not _BIB_NAME.fullmatch(name) or name not in support
                or element.get("type", "file") != "file"
                or element.get("glob", "0") not in {"0", "false"}):
            raise RepairError("Biber 仅允许已冻结的本地 Bib 文件")
        if digest(read(cwd / name, ARTIFACT_LIMIT)) != support[name]:
            raise RepairError("Biber 输入身份已变化")
    return datasources == 0


def _verify_recorder(recorder, cwd, stem, support, tex_executable, tex_path, artifact_dir):
    rows = recorder.decode("utf-8", errors="strict").splitlines()
    driver = absolute(Path(tex_executable).resolve())
    guesses = driver.parents[:3]
    system = _SYSTEM_PREFIXES + tuple(path for path in guesses if (path / "texmf-dist").is_dir())
    local_outputs = {(artifact_dir / (stem + extension)).relative_to(cwd).as_posix()
                     for extension in COMPILE_EXTENSIONS}
    local_inputs = set(support) | local_outputs | {tex_path.name}
    candidate_seen = False
    if not rows or rows[0] != f"PWD {cwd}":
        return False
    for row in rows[1:]:
        verb, _, entry = row.partition(" ")
        if verb not in {"INPUT", "OUTPUT"} or not entry:
            return False
        path = absolute(cwd / entry)
        if within(path, cwd):
            relative = path.relative_to(cwd).as_posix()
            if relative not in (local_inputs if verb == "INPUT" else local_outputs):
                return False
            if verb == "INPUT" and path == tex_path:
                candidate_seen = True
        elif verb != "INPUT" or not any(within(path, base) for base in system):
            return False
    return candidate_seen


def clear_outputs(cwd, stem):
    for extension in COMPILE_EXTENSIONS:
        path = cwd / (stem + extension)
        if regular(path, optional=True) is not None:
            path.unlink()


def compile_candidate(cwd, stem, expected_hash, support, private_dir, *, leases=(), artifact_dir=None):
    """Run three XeLaTeX passes and any local Biber pass, then bind the fresh PDF and logs."""
    start = uuid.uuid4().hex
    phase = "prepare"
    code = None
    tex_path = cwd / f"{stem}.tex"
    artifact_dir = cwd if artifact_dir is None else artifact_dir
    tool = shutil.which("xelatex")
    if tool is None:
        return CompileResult("unknown", expected_hash, start, "missing_xelatex")
    command = [tool, "-no-shell-escape", "-halt-on-error", "-file-line-error",
               "-interaction=nonstopmode", "-recorder"]
    biber_dirs = []
    if artifact_dir != cwd:
        if artifact_dir.parent != cwd:
            return CompileResult("unknown", expected_hash, start, "prepare")
        command.append(f"-output-directory={artifact_dir.name}")
        biber_dirs = [f"--input-directory={artifact_dir.name}", f"--output-directory={artifact_dir.name}"]
    command.append(tex_path.name)
    try:
        raw = read(tex_path)
        if digest(raw) != expected_hash:
            raise RepairError("复编前候选身份已变化")
        if _ACTIVE_FILE_COMMAND.search(raw):
            raise RepairError("候选包含不受控的 TeX 文件访问或执行命令")
        clear_outputs(artifact_dir, stem)
        with tempfile.TemporaryDirectory(prefix="compiler-home-", dir=private_dir) as home:
            env = _environment(home)
            for step in range(1, 4):
                phase = f"xelatex-{step}"
                for extension in (".pdf", ".log", ".fls"):
                    path = artifact_dir / (stem + extension)
                    if regular(path, optional=True) is not None:
                        path.unlink()
                code = _invoke(command, cwd, env, leases)
                if code:
                    return CompileResult("failed", expected_hash, start, phase, code)
                if digest(read(tex_path)) != expected_hash:
                    raise RepairError("复编期间候选发生变化")
                recorder = read(artifact_dir / f"{stem}.fls", LOG_LIMIT)
                if not _verify_recorder(recorder, cwd, stem, support, tool, tex_path, artifact_dir):
                    raise RepairError("编译文件记录出现未冻结的输入或输出")
                if step == 1 and (artifact_dir / f"{stem}.bcf").exists():
                    phase = "biber"
                    entryless_bcf = _biber_sources(artifact_dir / f"{stem}.bcf", cwd, support)
                    biber = shutil.which("biber")
                    if biber is None:
                        return CompileResult("unknown", expected_hash, start, "missing_biber")
                    code = _invoke([biber, "-l=zh_pinyin", *biber_dirs, stem], cwd, env, leases)
                    if code:
                        return CompileResult("failed", expected_hash, start, phase, code)
                    from ..sortlocale import guard_after_biber

                    bbl = read(artifact_dir / f"{stem}.bbl")
                    # A zero-citation biblatex document has an entryless .bcf and biber
                    # legitimately writes a 0-byte .bbl.  B-377: such documents must not
                    # be turned into compile failures merely for lacking entries.
                    bbl_missing = not bbl and not entryless_bcf
                    if bbl_missing or guard_after_biber(str(artifact_dir), stem):
                        return CompileResult("failed", expected_hash, start, phase, code)
        phase = "bind"
        pdf = read(artifact_dir / f"{stem}.pdf", ARTIFACT_LIMIT)
        log = read(artifact_dir / f"{stem}.log", LOG_LIMIT)
        recorder = read(artifact_dir / f"{stem}.fls", LOG_LIMIT)
        if (not pdf.startswith(b"%PDF-") or not pdf.rstrip().endswith(b"%%EOF") or not log
                or not _verify_recorder(recorder, cwd, stem, support, tool, tex_path, artifact_dir)):
            raise RepairError("PDF 或编译记录无法绑定本轮候选")
        return CompileResult("passed", expected_hash, start, phase, 0,
                             digest(pdf), digest(log), digest(recorder))
    except subprocess.TimeoutExpired:
        return CompileResult("unknown", expected_hash, start, phase + "-timeout")
    except (OSError, RepairError, UnicodeError):
        return CompileResult("unknown", expected_hash, start, phase, code)
