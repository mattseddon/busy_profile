"""Running git, and reporting its failures."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """A git invocation failed, or the repository is not in a usable state."""


def run(repo: Path, *args: str) -> str:
    """Run git in ``repo`` and return its stdout, stripped and decoded."""
    return run_raw(repo, *args).decode().strip()


def run_raw(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    """Run git in ``repo`` and return its stdout as bytes."""
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=stdin,
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).decode(errors="replace").strip()
        raise GitError(f"`git {' '.join(args)}` failed: {detail}")
    return process.stdout
