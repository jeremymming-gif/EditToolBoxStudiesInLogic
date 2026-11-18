"""P09 打包契约回归（静态）。

不真打包（真构建在 CI 与 P09 证据链里跑）；本测试只锁 **macOS 打包口径**与文档一致性，
防止后续有人把 sidecar 改回 onefile、丢掉随包客户端/版本收口/launcher，或让文档再次漂移。

对应 P09 R01/R02/R06/R08：版本单源（Engine/slcn/__init__.py）、包内 sidecar 目录形态、
随包官方 Flet 客户端（file picker entitlement 修复）、Info.plist 版本收口。
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
PACKAGING = REPO_ROOT / "sources-flet" / "packaging"

# macOS 打包口径（build-flet.yml 与 release.yml 必须同时满足）
_MACOS_CONTRACT = (
    "--onedir",
    "Contents/Resources/SLCNEngine",
    "Contents/MacOS/SLCNEngine",
    "flet-client/Flet.app",
    "ensure_client_cached",
    "FLET_VIEW_PATH",
    "LSMultipleInstancesProhibited",
    "CFBundleShortVersionString",
    "CFBundleVersion",
)

# 只有 P09 修过的 build-flet.yml 负责「断言随包客户端仍带 entitlement」；
# release.yml 是正式发布契约（禁改），只核它随包了官方客户端。
_BUILD_FLET_EXTRA = ("com.apple.security.files.user-selected.read-write",)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_build_flet_workflow_macos_contract():
    """build-flet.yml 的 macOS 段：onedir sidecar + MacOS launcher + 随包客户端 + plist 版本。"""
    text = _text(WORKFLOWS / "build-flet.yml")
    for needle in _MACOS_CONTRACT + _BUILD_FLET_EXTRA:
        assert needle in text, f"build-flet.yml 缺少 macOS 打包口径：{needle}"
    # 版本号必须仍取自引擎单源，不得恢复成 0.0.0 兜底或写死
    assert "Engine/slcn/__init__.py" in text, "版本号必须取自 Engine/slcn/__init__.py 单源"
    assert 'sed -n \'s/^__version__ = "\\(.*\\)"/\\1/p\'' in text
    # sidecar 不得回退 onefile（flet pack 自身不需要该参数，出现即为残留）
    assert "--onefile" not in text, "sidecar 构建不得残留 --onefile"


def test_release_workflow_macos_contract_matches():
    """release.yml（正式发布契约，禁改）与 build-flet.yml 的 macOS 口径必须一致。"""
    text = _text(WORKFLOWS / "release.yml")
    for needle in _MACOS_CONTRACT:
        assert needle in text, f"release.yml 缺少 macOS 打包口径：{needle}"
    assert "--onefile" not in text, "release.yml 的 sidecar 构建不得残留 --onefile"


def test_packaging_readme_documents_new_layout():
    """packaging/README.md 必须描述新布局，且不再教 onefile sidecar。"""
    readme = _text(PACKAGING / "README.md")
    for needle in (
        "Contents/Resources/SLCNEngine/",
        "flet-client/Flet.app",
        "FLET_VIEW_PATH",
        "ENTITLEMENT_NOT_FOUND",
        "--onedir",
        "workflow_dispatch",
    ):
        assert needle in readme, f"packaging/README.md 缺少新布局说明：{needle}"
    assert "--onefile" not in readme, "README 不得再教 onefile sidecar"
    ci_section = readme.split("## CI 行为", 1)[1].split("##", 1)[0]
    assert "workflow_dispatch" in ci_section and "手动" in ci_section, (
        "CI 触发说明必须写清仅手动 workflow_dispatch"
    )
    assert "手动 + `push`" not in ci_section, (
        "CI 触发说明不得再写「手动 + push」（实际只有 workflow_dispatch）"
    )


def test_first_open_doc_keeps_sidecar_self_check_path():
    """FIRST-OPEN 文档里的自检命令必须仍可用（launcher 保住 Contents/MacOS/SLCNEngine）。"""
    text = _text(PACKAGING / "FIRST-OPEN-macOS.txt")
    assert "Contents/MacOS/SLCNEngine --version" in text
    assert "Contents/Resources/SLCNEngine" in text, "文档应说明 sidecar 真身在 Resources（launcher 转调）"


def test_workflow_resigns_after_plist_and_before_zip():
    """R08 封条：launcher+外层 ad-hoc 重签必须在 sha256.txt 之后、zip 之前，且不得 --deep 重签。"""
    text = _text(WORKFLOWS / "build-flet.yml")
    launcher_sign = 'codesign --force --sign - "$APPDIR/Contents/MacOS/SLCNEngine"'
    outer_sign = 'codesign --force --sign - "$APPDIR"'
    assert launcher_sign in text, "缺 launcher 自签（嵌套代码，否则外层报 not signed）"
    assert outer_sign in text, "缺外层 ad-hoc 重签（PlistBuddy 后原封条失效）"
    assert "codesign --verify --deep --strict \"$APPDIR\"" in text, "重签后必须自检 --deep --strict"
    # 顺序：sha256.txt → launcher 自签 → 外层重签 → verify → zip
    idx_sha = text.index("shasum -a 256 SLCNEngine > sha256.txt")
    idx_launcher = text.index(launcher_sign)
    idx_outer = text.index(outer_sign)
    idx_verify = text.index("codesign --verify --deep --strict \"$APPDIR\"")
    # 只锚 `ditto -c -k` 前缀：zip 步骤可能追加 --norsrc（去 AppleDouble），
    # 但「zip 必须排在重签/verify 之后」这条顺序契约不变。
    idx_zip = text.index("ditto -c -k")
    assert idx_sha < idx_launcher < idx_outer < idx_verify < idx_zip
    # 不得对 .app 用 --deep 重签（会剥掉随包 flet-client 的 entitlements）
    assert "codesign --force --sign - --deep" not in text
    assert 'codesign --force --deep' not in text


def test_macos_zip_creation_strips_appledouble():
    """两个 workflow 的 macOS zip 步骤都必须删掉多余的 `._*` 影子条目。

    `ditto -c -k` 会把 `._*` 影子条目（AppleDouble：xattr/资源叉）一起塞进 zip
    （实测 1185 条里 592 条是 `._*`）。

    **不能用 `--norsrc` 来去重**：它隐含 `--noextattr`，会把
    `Contents/MacOS/SLCNEngine`（sh launcher）的 ad-hoc 签名一起剥掉 —— codesign
    对脚本只能把签名存进 xattr（`com.apple.cs.*`），丢掉后解包即
    `codesign --verify --deep --strict` 失败（`code object is not signed at all`，
    实测 exit=1）。所以口径是：正常打包后 `zip -d` 删 `._*`，只留 launcher 那一条。
    """
    for name in ("build-flet.yml", "release.yml"):
        text = _text(WORKFLOWS / name)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("ditto -c -k")]
        assert lines, f"{name} 找不到 macOS zip 的 ditto 命令"
        for line in lines:
            assert "--norsrc" not in line, (
                f"{name} 的 zip 命令不得用 --norsrc（会剥掉 launcher 的 xattr 签名，"
                f"解包后封条校验失败）：{line}"
            )
        # 删 `._*` 时必须排除 launcher 的签名载体
        assert "zip -q -d \"$ZIP\" '*/._*' -x '*MacOS/._SLCNEngine'" in text, (
            f"{name} 缺少「删 `._*` 但保留 launcher AppleDouble」这一步"
        )
        # 删完要有自检，防止 `._*` 再次失控增长
        assert "zip 里残留 AppleDouble 影子条目" in text, f"{name} 缺 AppleDouble 残留自检"
        # 自检必须 locale 无关：`unzip -l` 按 locale 转换非 ASCII 文件名，UTF-8 下
        # 中文路径（编辑部工具箱.app/...）变乱码，`grep '/\._'` 数不到 ——
        # 同一个包实测默认 locale 数到 4、LC_ALL=C 数到 592。数错等于没自检。
        ad_lines = [ln.strip() for ln in text.splitlines() if "grep -c '/\\._'" in ln]
        assert ad_lines, f"{name} 找不到 AppleDouble 计数命令"
        for line in ad_lines:
            assert line.startswith("AD=$(LC_ALL=C "), (
                f"{name} 的 AppleDouble 计数必须带 LC_ALL=C（否则 UTF-8 locale 下"
                f"中文路径的 `._*` 数不到，自检形同虚设）：{line}"
            )
        # 失败路径要把真实数字和排查提示打出来，否则 CI 日志只有一行 exit 1
        assert "预期 <=1，只应剩 Contents/MacOS/._SLCNEngine" in text, (
            f"{name} 的自检失败文案没说明预期值"
        )
        assert 'head -20' in text, f"{name} 的自检失败时没打印残留条目样本"
        # `$AD（` 这种「变量名紧跟中文全角括号」的写法 bash 会当成 $AD（ 变量名的一部分，
        # 实测报 `AD?: unbound variable` 并以 127 退出（脚本自己崩，而不是给出诊断）。
        # 必须写成 ${AD}。
        assert "$AD（" not in text and "$AD 条" not in text, (
            f"{name} 里 $AD 紧邻中文会导致 bash 解析出非法变量名，必须写成 ${{AD}}"
        )


def test_readme_documents_codesign_verification():
    """README 必须给出封条验证命令与 ad-hoc/无 Team ID/未公证说明。"""
    readme = _text(PACKAGING / "README.md")
    assert "codesign --verify --deep --strict" in readme
    for needle in ("ad-hoc", "Team ID", "未公证", "--deep"):
        assert needle in readme, f"README 缺少签名说明：{needle}"


def test_workflow_bundles_license_files_with_cmp_assertions():
    """M07：仓库根三许可 → zip 根与 app/Contents/Resources/ 各一份，cmp 断言同源。"""
    text = _text(WORKFLOWS / "build-flet.yml")
    for name in ("LICENSE", "LICENSE-SCOPE.md", "THIRD_PARTY.md"):
        assert name in text, f"build-flet.yml 缺少随包许可文件：{name}"
    assert 'cp "$f" "pkg-mac/$f"' in text, "缺 zip 根许可复制"
    assert 'cp "$f" "$APPDIR/Contents/Resources/$f"' in text, "缺 app Resources 许可复制"
    assert 'cmp "$f" "pkg-mac/$f"' in text, "缺 zip 根许可 cmp 断言"
    assert 'cmp "$f" "$APPDIR/Contents/Resources/$f"' in text, "缺 app Resources 许可 cmp 断言"
    # 复制+断言必须早于随包客户端/版本收口/重签（否则会被后续重签漏掉或顺序漂移）
    idx_copy = text.index('cp "$f" "$APPDIR/Contents/Resources/$f"')
    assert idx_copy < text.index('ditto "$SRC" "$APPDIR/Contents/Resources/flet-client/Flet.app"')
    assert idx_copy < text.index('$PB -c "Add :LSMultipleInstancesProhibited bool true"')
    assert idx_copy < text.index('codesign --force --sign - "$APPDIR"')


def test_readme_documents_bundled_license_files():
    """README 必须写明 M07 随包许可的落点、时机与校验口径。"""
    readme = _text(PACKAGING / "README.md")
    assert "### 随包许可（M07）" in readme, "README 缺「随包许可（M07）」节"
    for name in ("LICENSE", "THIRD_PARTY.md", "LICENSE-SCOPE.md"):
        assert name in readme, f"README 缺随包许可文件说明：{name}"
    assert "Contents/Resources/" in readme
    assert "git show <BUILD_COMMIT>" in readme, "README 必须写 git show 逐字节校验口径"
    assert "cmp" in readme and "逐字节一致" in readme
