#!/usr/bin/env python3
"""PostToolUse hook: ruff + bandit quality gate (cross-platform).

Python port of ruff-quality-gate.sh so it works on Windows without bash.
Reads tool_input JSON from stdin, runs ruff format / fix / check on .py files,
then bandit if installed. exit(2) on remaining violations.
"""
import json
import os
import shutil
import subprocess
import sys

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
RUFF_CONFIG = os.path.join(HOME, ".claude", "ruff.toml")
VIOLATION_COLLECTOR = os.path.join(HOOKS_DIR, "violation_collector.py")
VIOLATIONS_JSONL = os.path.join(HOOKS_DIR, "..", "data", "write_time_violations.jsonl")


def _read_input():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except Exception:
        return {}


def _ruff_args():
    args = []
    if os.path.isfile(RUFF_CONFIG):
        args += ["--config", RUFF_CONFIG]
    args += ["--ignore", "E402,S110,S603"]
    return args


def _run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    except FileNotFoundError:
        return None


def _collect(kind, payload):
    if not payload:
        return
    try:
        subprocess.run(
            [sys.executable, VIOLATION_COLLECTOR, kind, VIOLATIONS_JSONL],
            input=payload, text=True, capture_output=True, timeout=10,
        )
    except Exception:
        pass


def main():
    data = _read_input()
    file_path = (data.get("tool_input") or {}).get("file_path", "")
    if not file_path.endswith(".py") or not os.path.isfile(file_path):
        sys.exit(0)

    if shutil.which("ruff") is None:
        sys.exit(0)  # ruff not installed -> skip silently

    basename = os.path.basename(file_path)
    rargs = _ruff_args()

    _run(["ruff", "format"] + rargs + [file_path])
    _run(["ruff", "check", "--fix"] + rargs + [file_path])

    res = _run(["ruff", "check"] + rargs + ["--output-format", "json", file_path])
    if res and res.stdout:
        _collect("ruff", res.stdout)
        if res.returncode != 0:
            try:
                items = json.loads(res.stdout)
                lines = []
                for it in items:
                    loc = it.get("location") or {}
                    lines.append(
                        f"{it.get('filename','')}:{loc.get('row','?')}:"
                        f"{loc.get('column','?')}: {it.get('code','?')} {it.get('message','')}"
                    )
                if lines:
                    sys.stderr.write(f"[RuffQualityGate] Violations found in {basename}:\n")
                    sys.stderr.write("\n".join(lines) + "\n")
                    sys.stderr.write("[RuffQualityGate] Fix these issues before proceeding.\n")
                    sys.exit(2)
            except Exception:
                pass

    if shutil.which("bandit") is not None:
        bres = _run(["bandit", "-r", file_path, "-f", "json", "-q"])
        if bres and bres.stdout:
            _collect("bandit", bres.stdout)
            if bres.returncode != 0:
                try:
                    bdata = json.loads(bres.stdout)
                    high_med = [r for r in bdata.get("results", [])
                                if r.get("issue_severity") in ("HIGH", "MEDIUM")]
                    if high_med:
                        sys.stderr.write(f"[BanditSecurityGate] Security issues in {basename}:\n")
                        for r in high_med[:5]:
                            sys.stderr.write(
                                f"  {r.get('issue_severity')}: {r.get('issue_text')} "
                                f"(line {r.get('line_number')})\n"
                            )
                        sys.stderr.write("[BanditSecurityGate] Fix HIGH/MEDIUM issues.\n")
                        sys.exit(2)
                except Exception:
                    pass

    sys.exit(0)


if __name__ == "__main__":
    main()
