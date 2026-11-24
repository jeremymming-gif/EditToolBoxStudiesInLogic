"""Instrument the subprocess boundary with optional real XeLaTeX delegation."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path


def main():
    config = json.loads(Path(__file__).with_suffix(".json").read_bytes())
    cwd = str(Path.cwd().resolve())
    source = Path(sys.argv[-1])
    with open(config["runs"], "a", encoding="utf-8") as trace:
        trace.write(json.dumps({
            "cwd": cwd, "argv": sys.argv[1:],
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }) + "\n")
    mode = config["mode"]
    is_output = cwd == config["output"]
    if mode == "hold" or (mode == "hold-output" and is_output):
        Path(config["ready"]).write_text(str(os.getpid()), encoding="ascii")
        deadline = time.monotonic() + 20
        while not Path(config["release"]).exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        return 1
    if mode == "no-artifacts" or (mode == "no-output-artifacts" and is_output):
        return 0
    if mode == "fail" or (mode == "fail-output" and is_output):
        artifact_dir = next((Path(argument.split("=", 1)[1]) for argument in sys.argv[1:]
                             if argument.startswith("-output-directory=")), Path.cwd())
        log = artifact_dir / source.with_suffix(".log").name
        log.write_text("! Synthetic compilation failed.\n", encoding="utf-8")
        return 1
    real_tool = config["real_tool"]
    os.execv(real_tool, [real_tool, *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
