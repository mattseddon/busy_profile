"""Destructive rewriting of a repository's commit history."""

from __future__ import annotations

import os
import random
import subprocess
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from busy_profile.schedule import format_git_date
from busy_profile.text import RANDOM_TEXT_FILE, random_sentence

TEMP_BRANCH = "busy-profile-rewrite"

ProgressCallback = Callable[[int, int], None]


class GitError(RuntimeError):
    """A git invocation failed, or the repository is not in a usable state."""


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=None if env is None else {**os.environ, **env},
        check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip()
        raise GitError(f"`git {' '.join(args)}` failed: {detail}")
    return process.stdout.strip()


def current_branch(repo: Path) -> str:
    """Return the name of the checked out branch.

    Works on an unborn branch (a repository with no commits yet) but fails on a
    detached HEAD, which we cannot safely rewrite.
    """
    try:
        return _git(repo, "symbolic-ref", "--short", "HEAD")
    except GitError as error:
        raise GitError(
            "HEAD is detached; check out a branch before rewriting history"
        ) from error


def commit_count(repo: Path) -> int:
    """How many commits are reachable from HEAD, or 0 on an unborn branch."""
    try:
        return int(_git(repo, "rev-list", "--count", "HEAD"))
    except GitError:
        return 0


def assert_rewritable(repo: Path) -> None:
    """Raise :class:`GitError` unless ``repo`` is a repo we can rewrite."""
    if not repo.is_dir():
        raise GitError(f"{repo} is not a directory")
    if _git(repo, "rev-parse", "--is-bare-repository") == "true":
        raise GitError(f"{repo} is a bare repository")
    current_branch(repo)
    if _git(repo, "branch", "--list", TEMP_BRANCH):
        raise GitError(
            f"branch {TEMP_BRANCH!r} already exists; delete it and try again"
        )


def rewrite_history(
    repo: Path,
    timestamps: Sequence[datetime],
    *,
    rng: random.Random | None = None,
    on_commit: ProgressCallback | None = None,
) -> None:
    """Replace the history of ``repo`` with one commit per entry in ``timestamps``.

    Every commit overwrites ``random_text`` with a freshly generated sentence and
    quotes that sentence in its commit message. The first commit additionally
    snapshots the rest of the current working tree (everything ``git add -A``
    would stage). The previous history is discarded.
    """
    if not timestamps:
        raise ValueError("timestamps must not be empty")
    if any(timestamp.tzinfo is None for timestamp in timestamps):
        raise ValueError("timestamps must be timezone-aware")

    assert_rewritable(repo)
    rng = random.Random() if rng is None else rng
    branch = current_branch(repo)
    total = len(timestamps)

    _git(repo, "checkout", "--orphan", TEMP_BRANCH)
    try:
        for index, timestamp in enumerate(timestamps):
            sentence = _write_random_text(repo, rng)
            if index == 0:
                _git(repo, "add", "-A")
            # --force so a target repo that gitignores the file still works.
            _git(repo, "add", "--force", "--", RANDOM_TEXT_FILE)
            _commit(repo, f"Update {RANDOM_TEXT_FILE} to be {sentence}", timestamp)
            if on_commit is not None:
                on_commit(index + 1, total)
        _git(repo, "branch", "-M", branch)
    except (Exception, KeyboardInterrupt):
        # Anything at all leaves the repo on a half-written orphan branch, so
        # always put it back before propagating.
        _restore(repo, branch)
        raise


def _write_random_text(repo: Path, rng: random.Random) -> str:
    """Overwrite ``random_text`` with a single random sentence and return it."""
    sentence = random_sentence(rng)
    (repo / RANDOM_TEXT_FILE).write_text(f"{sentence}\n")
    return sentence


def _restore(repo: Path, branch: str) -> None:
    """Move HEAD back to ``branch``, drop the temporary branch, and tidy up."""
    if _git(repo, "branch", "--list", branch):
        _git(repo, "checkout", "--force", branch)
    else:
        # The original branch was unborn, so there is nothing to check out;
        # just point HEAD back at it and clear what we staged.
        _git(repo, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
        _git(repo, "read-tree", "--empty")
    if _git(repo, "branch", "--list", TEMP_BRANCH):
        _git(repo, "branch", "-D", TEMP_BRANCH)

    # Do not leave our scratch file behind if the restored branch has no
    # version of it to fall back on.
    path = repo / RANDOM_TEXT_FILE
    if path.exists() and not _git(repo, "ls-files", "--", RANDOM_TEXT_FILE):
        path.unlink()


def _commit(repo: Path, message: str, timestamp: datetime) -> None:
    stamp = format_git_date(timestamp)
    _git(
        repo,
        "commit",
        "--allow-empty",
        "--no-verify",
        "--message",
        message,
        env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
    )
