#!/usr/bin/env python3
"""smoke.py — v1.3.3 Group M `cc-autopipe-smoke` helper.

Validates a pipeline script (or any executable) with a quick smoke run
BEFORE Claude calls `cc-autopipe-detach` for a long-running operation.
Catches the class of failure where a Claude-written launcher crashes in
the first 30 seconds (PermissionError, missing volume mount, bad env)
— without smoke validation, the engine then polls `check_cmd` for
hours against a dead pipeline.

Three terminal outcomes:

    SMOKE_OK   exit 0 — script either completed rc=0 within timeout,
               or stayed alive past --min-alive-sec then was killed
    SMOKE_FAIL exit 1 — script exited with rc!=0 within the timeout
    misuse     exit 2 — bad arguments or unreadable script

Usage:

    cc-autopipe-smoke <script_path> \\
        [--timeout-sec N] \\
        [--min-alive-sec N] \\
        [--workdir DIR]

Defaults: timeout-sec=60, min-alive-sec=30, workdir=<script's parent>.

Process group is kill -9'd reliably via `os.killpg` after
`start_new_session=True`, so any child processes (Docker exec'd workers,
spawned trainers) are cleaned up regardless of the outcome. Same idiom
used elsewhere in the engine for detach cleanup.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_TIMEOUT_SEC = 60
DEFAULT_MIN_ALIVE_SEC = 30
STDERR_TAIL_LINES = 30
KILL_DEADLINE_SEC = 5


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Best-effort SIGTERM → SIGKILL on the whole session group."""
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError, OSError):
        # Already gone, or no permission. Fall back to single-pid kill.
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    deadline = time.monotonic() + KILL_DEADLINE_SEC
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _tail_lines(path: Path, n: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def smoke(
    script_path: Path,
    *,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    min_alive_sec: int = DEFAULT_MIN_ALIVE_SEC,
    workdir: Path | None = None,
    stdout_writer=None,
    stderr_writer=None,
) -> int:
    """Run the smoke check. Returns the exit code the wrapper should use.

    `stdout_writer` / `stderr_writer` default to None → resolved to
    sys.stdout / sys.stderr at call time so pytest capsys (which
    swaps these at test-setup) captures correctly.
    """
    if stdout_writer is None:
        stdout_writer = sys.stdout
    if stderr_writer is None:
        stderr_writer = sys.stderr
    if not script_path.exists():
        stderr_writer.write(f"cc-autopipe-smoke: script not found: {script_path}\n")
        return 2
    if not os.access(script_path, os.X_OK):
        stderr_writer.write(
            f"cc-autopipe-smoke: script not executable: {script_path}\n"
            f"Hint: chmod +x {script_path}\n"
        )
        return 2
    if timeout_sec < min_alive_sec:
        stderr_writer.write(
            f"cc-autopipe-smoke: misconfig — --timeout-sec ({timeout_sec}) "
            f"< --min-alive-sec ({min_alive_sec}); cannot evaluate\n"
        )
        return 2

    workdir = workdir or script_path.parent

    tmp_stdout = tempfile.NamedTemporaryFile(
        mode="w+", prefix="cc-smoke-stdout-", delete=False
    )
    tmp_stderr = tempfile.NamedTemporaryFile(
        mode="w+", prefix="cc-smoke-stderr-", delete=False
    )
    tmp_stdout_path = Path(tmp_stdout.name)
    tmp_stderr_path = Path(tmp_stderr.name)
    tmp_stdout.close()
    tmp_stderr.close()

    proc: subprocess.Popen | None = None
    try:
        with tmp_stdout_path.open("w") as out, tmp_stderr_path.open("w") as err:
            proc = subprocess.Popen(
                [str(script_path)],
                cwd=str(workdir),
                stdout=out,
                stderr=err,
                start_new_session=True,
            )

        start = time.monotonic()
        deadline = start + timeout_sec
        rc: int | None = None
        while time.monotonic() < deadline:
            try:
                rc = proc.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue

        elapsed = int(time.monotonic() - start)

        if rc is None:
            # Still running at the timeout boundary. Two cases:
            #   - elapsed >= min_alive_sec → SMOKE_OK (script is healthy
            #     enough that it didn't crash early). Kill the tree.
            #   - elapsed <  min_alive_sec → can't happen because the
            #     argparse-time check requires timeout_sec >= min_alive.
            _kill_process_tree(proc)
            if elapsed >= min_alive_sec:
                stdout_writer.write(
                    f"SMOKE_OK: script alive past min-alive threshold "
                    f"({elapsed}s), killed\n"
                )
                return 0
            stderr_writer.write(
                f"cc-autopipe-smoke: script killed before min-alive "
                f"({elapsed}s < {min_alive_sec}s) — internal error\n"
            )
            return 2

        if rc == 0:
            stdout_writer.write(
                f"SMOKE_OK: script completed successfully (elapsed {elapsed}s)\n"
            )
            return 0

        stdout_writer.write(
            f"SMOKE_FAIL: script exited with rc={rc} (elapsed {elapsed}s)\n"
        )
        tail = _tail_lines(tmp_stderr_path, STDERR_TAIL_LINES)
        if tail:
            stderr_writer.write("--- last %d stderr lines ---\n" % STDERR_TAIL_LINES)
            stderr_writer.write(tail)
            if not tail.endswith("\n"):
                stderr_writer.write("\n")
        return 1

    finally:
        if proc is not None:
            _kill_process_tree(proc)
        for path in (tmp_stdout_path, tmp_stderr_path):
            try:
                path.unlink()
            except OSError:
                pass


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="cc-autopipe-smoke")
    parser.add_argument("script", help="path to the pipeline script to smoke")
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=DEFAULT_TIMEOUT_SEC,
        help=f"max wall time before killing the script (default {DEFAULT_TIMEOUT_SEC})",
    )
    parser.add_argument(
        "--min-alive-sec",
        type=int,
        default=DEFAULT_MIN_ALIVE_SEC,
        help=(
            "if the script is still running at this elapsed time, treat "
            f"as SMOKE_OK (default {DEFAULT_MIN_ALIVE_SEC})"
        ),
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="working directory (default: script's parent dir)",
    )
    args = parser.parse_args(argv)

    script_path = Path(args.script).resolve()
    workdir = Path(args.workdir).resolve() if args.workdir else None

    # Reset SIGINT/SIGTERM handlers so a Ctrl+C from the operator
    # immediately propagates to the subprocess group via the kill in
    # the finally block.
    def _sig_passthrough(_signum, _frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _sig_passthrough)

    try:
        return smoke(
            script_path,
            timeout_sec=args.timeout_sec,
            min_alive_sec=args.min_alive_sec,
            workdir=workdir,
        )
    except KeyboardInterrupt:
        sys.stderr.write("cc-autopipe-smoke: interrupted\n")
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
