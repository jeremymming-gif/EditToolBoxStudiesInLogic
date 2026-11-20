# Windows Gate Timeout Acceptance Handoff

## Copyable lead prompt

You are taking over the Windows acceptance for the Engine test-runner timeout fix in the public `EditToolBoxStudiesInLogic` repository. Work only on this acceptance unless a concrete defect blocks it.

### Fixed candidate

- Repository: `EditToolBoxStudiesInLogic`
- Expected commit: `21ccc4dc9c128fe7a233755c91fe5090473413b5`
- Relevant implementation: `Engine/tools/run_engine_tests.sh`
- Hermetic regression: `Engine/tests/test_b309_gate_speedup.py`
- Timeout contract: `SLCN_TEST_TIMEOUT_SECONDS`, default `720`, accepted range `1` through `4000000` seconds.

The fix supervises each suite with Python. On Windows it creates a new process group, uses `taskkill /PID <pid> /T /F` on timeout, returns `124`, marks the suite `TIMEOUT`, and proceeds to later suites. The existing parallel policy then serially rechecks a parallel red suite, including a timed-out suite.

### Boundaries

- Do not access, search, copy, or test against `Studies_in_Logic_Database` or any real manuscript material. Do not ask another process or agent to do so.
- Use only the regression's generated synthetic suites. Set explicit isolated `SLCN_MANUSCRIPTS` and `SLCN_NOTES` paths so automatic discovery cannot fall back to sibling repositories.
- Do not call paid APIs or run the long `full`/release gate, `selfcheck --full`, strict AUTO, packaging, or release workflows for this acceptance.
- Preserve existing local changes. Do not use `reset --hard`, `clean`, checkout/restore of user files, or broad staging.
- Do not push, tag, or release. If the regression proves a defect, keep the repair narrowly scoped, add/adjust a synthetic regression, and report the exact diff and evidence for lead review before any commit.
- Do not treat macOS/POSIX evidence as Windows proof. This acceptance exists specifically to exercise the Windows Git Bash branch.

### Steps

1. In PowerShell, enter the public repository and inspect local state:

   ```powershell
   git status --short --branch
   ```

   Preserve any pre-existing edits. If the checkout is dirty in a way that makes updating or testing unsafe, stop and report it; do not discard or stash user work.

2. If the checkout is clean, update by fast-forward only, then verify the candidate source:

   ```powershell
   git pull --ff-only origin main
   git branch --show-current
   git merge-base --is-ancestor 21ccc4dc9c128fe7a233755c91fe5090473413b5 HEAD
   git diff --exit-code 21ccc4dc9c128fe7a233755c91fe5090473413b5 HEAD -- Engine/tools/run_engine_tests.sh Engine/tests/test_b309_gate_speedup.py
   git show -s --format="%H %s" HEAD
   ```

   Continue only on `main`, when the ancestry check succeeds and the diff command prints no changes. The handoff-document commit may make `HEAD` a descendant of the fixed code candidate. If either source file differs from that candidate, stop and ask the originating lead to identify the replacement candidate; do not silently test a different implementation.

3. Confirm Git Bash and Python are available to child processes:

   ```powershell
   Get-Command bash
   Get-Command python
   bash --version
   python --version
   ```

   `test_b309_gate_speedup.py` invokes `bash` and, on Windows, `cygpath` and `tasklist`; the installed Git for Windows normally supplies the first two utilities. If a required utility is unavailable, report an environment blocker rather than changing the test to skip Windows coverage.

4. Create isolated locator paths and run only the focused hermetic regression from the repository root:

   ```powershell
   $isolation = Join-Path $env:TEMP "slcn-gate-timeout-acceptance"
   $manuscripts = Join-Path $isolation "empty-manuscripts"
   $notes = Join-Path $isolation "empty-notes"
   New-Item -ItemType Directory -Force $manuscripts | Out-Null
   New-Item -ItemType Directory -Force $notes | Out-Null
   $env:SLCN_MANUSCRIPTS = $manuscripts
   $env:SLCN_NOTES = $notes
   python Engine/tests/test_b309_gate_speedup.py
   $testExitCode = $LASTEXITCODE
   "TEST_EXIT_CODE=$testExitCode"
   ```

   The explicit manuscript path must remain empty; the notes path intentionally has no `cases` directory. Do not point either variable at a real repository. The regression creates and removes its own temporary synthetic suite tree and timeout child processes.

### Acceptance criteria

- The focused regression exits `0` and reports `失败 0 项（全部通过）` (the current candidate reports 41 passing assertions).
- The Windows-specific process-tree assertion passes: after timeout, both the synthetic suite process and its child are gone, and the child's pulse file stops changing.
- The serial timeout is recorded as exit code `124` and manifest status `timeout`; the next synthetic suite runs and is represented in the manifest.
- The allowlist timeout is still counted and does not prevent the next suite from running.
- The parallel timeout is reported as red, gets the existing serial recheck, is counted once in the final suite result, and both the parallel attempt and serial recheck leave no suite/child process alive.
- Preserve the full test output and record the exact commit, Windows version, Git Bash version, Python version, command/cwd, process-tree assertion result, and exit code. Do not claim that this validates a Windows GUI, package, full gate, or release.

### If something fails

Capture the complete focused-test output and the failing assertion name. State whether the failure is an environment prerequisite, a regression-test failure, a timeout/cleanup failure, or an unrelated assertion. Do not rerun the full gate to diagnose this narrow result. If a source defect is confirmed, make only the minimal repair and synthetic regression needed, then report changed paths and fresh Windows evidence to the originating lead for review.

## Originating lead evidence

The candidate was pushed to public `main` so a Windows checkout can pull it. On macOS, the focused regression passed 41 assertions; Bash syntax validation and `git diff --check` also passed. The quick Engine tier passed 12 suites in 9.4 seconds, but that is not Windows timeout evidence and is not a full-gate result.

### Windows/Git Bash result (2026-11-20)

The Windows operator reported a passing run on Windows 11 Pro build 26200, Git Bash 5.2.37, and Python 3.14.10. The checkout was `main` at `2a2aeafed7230752fff296508e19c9621002d52e`, a descendant of the fixed candidate; both acceptance source files matched `21ccc4dc9c128fe7a233755c91fe5090473413b5`. From the repository root, `python Engine/tests/test_b309_gate_speedup.py` reported 41 assertions passed, 0 failed, exit code 0. The report covers serial, allowlist, parallel timeout/recheck, manifest and continuation behavior, and Windows process-tree cleanup. Explicit empty manuscript/notes paths were used; section (e) skipped as expected. No real manuscript/notes data, full gate, strict AUTO, package, or release was accessed or run. The Windows checkout was clean and unchanged. Detailed logs remain on the Windows host; they were not independently read by the originating Mac lead.

This is evidence for the Windows Git Bash timeout path only. It does not establish Windows GUI/package, full gate, strict AUTO, or release acceptance. POSIX child detachment remains outside coverage. In parallel mode, the existing policy serially rechecks a timed-out suite, so a hung suite may consume two timeout intervals.

The originating checkout had nine unrelated pre-existing modified files at handoff. They were not included in the candidate push and must remain untouched.
