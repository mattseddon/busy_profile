"""Command line entry point."""

from __future__ import annotations

import argparse
import random
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm
from rich.table import Table

from busy_profile import __version__
from busy_profile.rewrite import (
    GitError,
    assert_rewritable,
    commit_count,
    current_branch,
    rewrite_history,
)
from busy_profile.schedule import (
    DEFAULT_COMMITS,
    DEFAULT_DAYS,
    commits_per_day,
    generate_timestamps,
)

console = Console()
err_console = Console(stderr=True)


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
        type=int,
        default=DEFAULT_DAYS,
        help=f"how many days back the initial commit goes (default: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "-c",
        "--commits",
        type=int,
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
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> Args:
    """Parse ``argv`` into a typed view of the arguments."""
    return build_parser().parse_args(argv, Args())


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    rng = random.Random(args.seed)
    try:
        timestamps = generate_timestamps(
            args.days,
            args.commits,
            now=datetime.now().astimezone(),
            rng=rng,
        )
    except ValueError as error:
        _error(str(error))
        return 2

    repo = args.repo
    _describe(timestamps, repo)

    if args.dry_run:
        return 0

    try:
        assert_rewritable(repo)
    except GitError as error:
        _error(str(error))
        return 1

    if not args.yes and not _confirm(repo):
        err_console.print("[yellow]aborted[/]", soft_wrap=True)
        return 1

    try:
        _rewrite_with_progress(repo, timestamps, rng)
    except GitError as error:
        _error(str(error))
        return 1
    except KeyboardInterrupt:
        err_console.print(
            "\n[yellow]interrupted[/]; the original history is untouched",
            soft_wrap=True,
        )
        return 130

    tick = "[green]\N{HEAVY CHECK MARK}[/]"
    console.print(
        f"{tick} rewrote [bold]{len(timestamps):,}[/] commits"
        + f" in [bold]{escape(str(repo))}[/]",
        soft_wrap=True,
    )
    return 0


def _rewrite_with_progress(
    repo: Path, timestamps: Sequence[datetime], rng: random.Random
) -> None:
    """Run the rewrite behind a progress bar."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(complete_style="green", finished_style="green"),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(elapsed_when_finished=True),
        console=console,
        disable=not console.is_terminal,
    ) as progress:
        task = progress.add_task("committing", total=len(timestamps))

        def advance(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        rewrite_history(repo, timestamps, rng=rng, on_commit=advance)


def _error(message: str) -> None:
    err_console.print(f"[bold red]error:[/] {escape(message)}", soft_wrap=True)


def _describe(timestamps: Sequence[datetime], repo: Path) -> None:
    per_day = commits_per_day(timestamps)
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="cyan", justify="right")
    summary.add_column()
    summary.add_row("repository", escape(str(repo)))
    summary.add_row("commits", f"[bold]{len(timestamps):,}[/]")
    summary.add_row("first commit", f"{timestamps[0]:%Y-%m-%d %H:%M:%S %z}")
    summary.add_row("last commit", f"{timestamps[-1]:%Y-%m-%d %H:%M:%S %z}")
    summary.add_row(
        "active days",
        f"{len(per_day):,} (busiest day has {max(per_day.values())})",
    )
    console.print(
        Panel(summary, title="[bold]plan[/]", border_style="cyan", expand=False)
    )


def _confirm(repo: Path) -> bool:
    if not sys.stdin.isatty():
        _error("refusing to rewrite history without a terminal; pass --yes")
        return False

    existing = commit_count(repo)
    branch = current_branch(repo)
    plural = "" if existing == 1 else "s"
    fate = "it" if existing == 1 else "them"
    aside = (
        "[dim italic]*unless you have an up-to-date copy on your remote "
        + "and then... \nskill issue.[/]"
    )
    warning = (
        f"Branch [cyan]{escape(branch)}[/] has [bold]{existing:,}[/] commit{plural}."
        + f" Continuing makes {fate} unreachable.\n"
        + f"This cannot be undone!*\n{aside}"
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
