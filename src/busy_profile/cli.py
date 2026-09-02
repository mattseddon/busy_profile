"""Command line entry point."""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from busy_profile.git import GitError
from busy_profile.gource import plan_gource_commits
from busy_profile.plan import (
    DEFAULT_COMMITS,
    DEFAULT_DAYS,
    Move,
    PlannedCommit,
    WriteFile,
    plan_commits,
)
from busy_profile.rewrite import assert_rewritable, commit_count, rewrite_history

console = Console()
err_console = Console(stderr=True)

TICK = "[green]\N{HEAVY CHECK MARK}[/]"


class Args(argparse.Namespace):
    """Typed view of the parsed arguments.

    ``argparse.Namespace`` exposes every attribute as ``Any``; annotating them
    here and handing an instance to ``parse_args`` gives the call sites real
    types. Every attribute has a parser default, so all are populated by the
    time they are read, and ``test_cli`` pins that.

    Do not give these class-body values: argparse only applies its own default
    when ``hasattr(namespace, dest)`` is false, so a value here would silently
    shadow the default declared in :func:`build_parser`.
    """

    days: int
    commits: int
    repo: Path
    seed: int | None
    dry_run: bool
    yes: bool
    for_gource: bool


def _positive_int(value: str) -> int:
    """An argparse type that rejects zero and negatives before we touch a repo."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {number}")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="busy-profile",
        description=(
            "Rewrite a repository's history as an initial commit N days ago "
            "followed by commits randomly distributed up to now. This DISCARDS "
            "the repository's existing commits."
        ),
    )
    parser.add_argument(
        "-d",
        "--days",
        type=_positive_int,
        default=DEFAULT_DAYS,
        help=f"how many days back the initial commit goes (default: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "-c",
        "--commits",
        type=_positive_int,
        default=DEFAULT_COMMITS,
        help=f"how many commits to generate (default: {DEFAULT_COMMITS})",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="repository to rewrite (default: the current directory)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="seed the random distribution for a reproducible history",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="describe the history that would be written, then exit",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="skip the confirmation prompt",
    )
    parser.add_argument(
        "--for-gource",
        action="store_true",
        help=(
            "grow a tree for gource to animate: each commit adds a file at the "
            "root or gathers three siblings into a new folder"
        ),
    )
    parser.add_argument("--version", action="version", version=version("busy-profile"))
    return parser


def parse_args(argv: Sequence[str] | None = None) -> Args:
    """Parse ``argv`` into a typed view of the arguments."""
    return build_parser().parse_args(argv, Args())


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo

    try:
        branch = assert_rewritable(repo)
    except GitError as error:
        _error(str(error))
        return 1

    # A naive local ``now`` lets each commit carry the UTC offset of its own day.
    now = datetime.now()
    rng = random.Random(args.seed)
    if args.for_gource:
        reserved = {path.name for path in repo.iterdir()}
        planned = plan_gource_commits(
            args.days, args.commits, now=now, rng=rng, reserved=reserved
        )
    else:
        planned = plan_commits(args.days, args.commits, now=now, rng=rng)
    _describe(planned, repo, for_gource=args.for_gource)

    if args.dry_run:
        return 0

    if not args.yes and not _confirm(repo, branch):
        err_console.print("[yellow]aborted[/]", soft_wrap=True)
        return 1

    try:
        _rewrite_with_spinner(repo, branch, planned)
    except GitError as error:
        _error(str(error))
        return 1
    except KeyboardInterrupt:
        err_console.print(
            "\n[yellow]interrupted[/]; see `git status` and `git log` before retrying",
            soft_wrap=True,
        )
        return 130

    console.print(
        (
            f"{TICK} rewrote [bold]{len(planned):,}[/] commits"
            f" in [bold]{escape(str(repo))}[/]"
        ),
        soft_wrap=True,
    )
    return 0


def _rewrite_with_spinner(
    repo: Path, branch: str, commits: Sequence[PlannedCommit]
) -> None:
    if not console.is_terminal:
        rewrite_history(repo, branch, commits)
        return

    in_progress: str | None = None

    def finish() -> None:
        if in_progress is not None:
            console.print(f"{TICK} [dim]{escape(in_progress)}[/]")

    with console.status("[cyan]starting[/]", spinner="dots") as status:

        def report(stage: str) -> None:
            nonlocal in_progress
            finish()
            in_progress = stage
            status.update(f"[cyan]{escape(stage)}[/]")

        rewrite_history(repo, branch, commits, on_stage=report)
        finish()


def _error(message: str) -> None:
    err_console.print(f"[bold red]error:[/] {escape(message)}", soft_wrap=True)


def _describe(
    commits: Sequence[PlannedCommit], repo: Path, *, for_gource: bool
) -> None:
    per_day = Counter(commit.timestamp.date() for commit in commits)
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="cyan", justify="right")
    summary.add_column()
    summary.add_row("repository", escape(str(repo)))
    summary.add_row("commits", f"[bold]{len(commits):,}[/]")
    if for_gource:
        changes = [change for commit in commits for change in commit.changes]
        files = sum(isinstance(change, WriteFile) for change in changes)
        folders = len(
            {c.destination.split("/")[0] for c in changes if isinstance(c, Move)}
        )
        summary.add_row("tree", f"{files:,} files, {folders:,} folders")
    summary.add_row("first commit", f"{commits[0].timestamp:%Y-%m-%d %H:%M:%S %z}")
    summary.add_row("last commit", f"{commits[-1].timestamp:%Y-%m-%d %H:%M:%S %z}")
    summary.add_row(
        "active days",
        f"{len(per_day):,} (busiest day has {max(per_day.values())})",
    )
    console.print(
        Panel(summary, title="[bold]plan[/]", border_style="cyan", expand=False)
    )


def _confirm(repo: Path, branch: str) -> bool:
    if not sys.stdin.isatty():
        _error("refusing to rewrite history without a terminal; pass --yes")
        return False

    existing = commit_count(repo)
    plural = "" if existing == 1 else "s"
    fate = "it" if existing == 1 else "them"
    warning = (
        f"Branch [cyan]{escape(branch)}[/] has [bold]{existing:,}[/] commit{plural}. "
        f"Continuing makes {fate} unreachable.\n"
        "This cannot be undone!*\n"
        "[dim italic]*unless you have an up-to-date copy on your remote "
        "and then... \nskill issue.[/]"
    )
    console.print(
        Panel(
            f"[bold]{escape(str(repo))}[/]\n\n{warning}",
            title="[bold red]\N{WARNING SIGN}  destructive rewrite[/]",
            border_style="red",
            padding=(1, 2),
        )
    )
    try:
        return Confirm.ask("Continue?", default=False, console=console)
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False


if __name__ == "__main__":
    raise SystemExit(main())
