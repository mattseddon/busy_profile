from __future__ import annotations

import io
import re
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import get_type_hints

import pytest
from rich.console import Console

from busy_profile import cli
from busy_profile.cli import Args, main, parse_args
from busy_profile.plan import DEFAULT_COMMITS, DEFAULT_DAYS
from busy_profile.text import NOUNS
from tests.conftest import git


def test_version_flag_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == version("busy-profile")


def test_defaults_are_365_days_and_2500_commits(
    repo: Path, wide_terminal: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """``days`` parses as ``None`` so an explicit flag can be told apart; the
    plan still starts DEFAULT_DAYS ago."""
    del wide_terminal
    args = parse_args([])
    assert args.days is None
    assert args.commits == DEFAULT_COMMITS

    assert main(["--repo", str(repo), "--dry-run"]) == 0
    expected = (datetime.now() - timedelta(days=DEFAULT_DAYS)).strftime("%Y-%m-%d")
    assert expected in capsys.readouterr().out


def test_flags_override_defaults() -> None:
    args = parse_args(["--days", "30", "--commits", "100"])
    assert args.days == 30
    assert args.commits == 100


def test_every_attribute_args_declares_is_actually_populated() -> None:
    args = parse_args([])
    for name in get_type_hints(Args):
        assert hasattr(args, name), name


def test_args_attributes_have_their_declared_types() -> None:
    args = parse_args(["--days", "3"])
    assert isinstance(args.days, int)
    assert isinstance(args.commits, int)
    assert isinstance(args.repo, Path)
    assert isinstance(args.dry_run, bool)
    assert isinstance(args.yes, bool)
    assert isinstance(args.for_gource, bool)
    assert isinstance(args.append_commits, bool)
    assert args.seed is None


def test_dry_run_reports_the_plan_without_touching_the_repo(
    repo: Path, wide_terminal: None, capsys: pytest.CaptureFixture[str]
) -> None:
    del wide_terminal
    original = git(repo, "rev-parse", "HEAD")

    assert (
        main(
            [
                "--days",
                "30",
                "--commits",
                "40",
                "--repo",
                str(repo),
                "--seed",
                "0",
                "--dry-run",
            ]
        )
        == 0
    )

    out = capsys.readouterr().out
    assert "plan" in out
    assert str(repo) in out
    assert "commits" in out
    assert "40" in out
    assert git(repo, "rev-parse", "HEAD") == original
    assert git(repo, "status", "--porcelain") == "?? untracked.txt"


def test_dry_run_fails_the_same_way_a_real_run_would(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--repo", str(tmp_path), "--dry-run"]) == 1

    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "plan" not in captured.out


def test_dry_run_renders_no_ansi_codes_when_piped(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "--days",
                "30",
                "--commits",
                "40",
                "--repo",
                str(repo),
                "--seed",
                "0",
                "--dry-run",
            ]
        )
        == 0
    )

    assert "\x1b[" not in capsys.readouterr().out


def test_rewrite_emits_no_progress_animation_when_piped(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "--days",
                "30",
                "--commits",
                "8",
                "--repo",
                str(repo),
                "--seed",
                "0",
                "--yes",
            ]
        )
        == 0
    )

    out = capsys.readouterr().out
    assert "\x1b[" not in out
    assert "committing" not in out


def test_rewrite_leaves_a_line_per_finished_stage_in_a_terminal(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spinner overwrites itself; finished stages must stay on screen."""
    screen = io.StringIO()
    monkeypatch.setattr(
        cli, "console", Console(file=screen, force_terminal=True, width=200)
    )

    assert main(["--days", "30", "--commits", "8", "--repo", str(repo), "--yes"]) == 0

    out = screen.getvalue()
    stages = [
        "reading git identity",
        "staging the working tree",
        "building 8 commits",
        "importing 8 commits",
        "pointing main at the new history",
    ]
    for stage in stages:
        assert stage in out
    assert out.count("\N{HEAVY CHECK MARK}") == len(stages) + 1


def test_rewrites_the_repo_when_confirmed(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "--days",
                "30",
                "--commits",
                "12",
                "--repo",
                str(repo),
                "--seed",
                "0",
                "--yes",
            ]
        )
        == 0
    )

    assert git(repo, "rev-list", "--count", "HEAD") == "12"
    assert "rewrote 12 commits" in capsys.readouterr().out


def test_for_gource_grows_a_tree_instead_of_random_text(repo: Path) -> None:
    assert (
        main(
            [
                "--commits",
                "14",
                "--repo",
                str(repo),
                "--seed",
                "0",
                "--yes",
                "--for-gource",
            ]
        )
        == 0
    )

    tracked = git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    assert "random_text" not in tracked
    assert "README.md" in tracked
    assert "untracked.txt" in tracked
    assert any(path.count("/") == 2 for path in tracked)
    assert git(repo, "status", "--porcelain") == ""


def test_for_gource_dry_run_describes_the_tree(
    repo: Path, wide_terminal: None, capsys: pytest.CaptureFixture[str]
) -> None:
    del wide_terminal
    assert (
        main(["--commits", "14", "--repo", str(repo), "--dry-run", "--for-gource"]) == 0
    )

    assert "9 new files, 4 new folders, 0 deleted" in capsys.readouterr().out
    assert git(repo, "status", "--porcelain") == "?? untracked.txt"


def test_for_gource_dry_run_counts_every_folder_made(
    repo: Path, wide_terminal: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Folder names are reused after a move, so folders are counted by commit,
    not by distinct name: 2,500 commits make far more folders than there are
    nouns to name them. Seed 2 also happens to delete some of them; the exact
    number differs from the planner's own tests because the names already at
    the repo root are reserved here, which shifts the draws."""
    del wide_terminal
    argv = ["--commits", "2500", "--seed", "2", "--repo", str(repo)]
    assert main([*argv, "--dry-run", "--for-gource"]) == 0

    match = re.search(
        r"([\d,]+) new files, ([\d,]+) new folders, ([\d,]+) deleted",
        capsys.readouterr().out,
    )
    assert match is not None
    files, folders, deleted = (int(group.replace(",", "")) for group in match.groups())
    assert folders > len(NOUNS)
    assert deleted >= 1
    assert files + folders + deleted == 2499


def grow(repo: Path) -> list[str]:
    """Give ``repo`` a 14-commit gource history; return its shas, oldest first."""
    argv = [
        "--commits",
        "14",
        "--repo",
        str(repo),
        "--seed",
        "0",
        "--yes",
        "--for-gource",
    ]
    assert main(argv) == 0
    return git(repo, "rev-list", "--reverse", "HEAD").splitlines()


def append_argv(repo: Path, *extra: str) -> list[str]:
    return [
        "--commits",
        "10",
        "--repo",
        str(repo),
        "--for-gource",
        "--append-commits",
        *extra,
    ]


@pytest.mark.parametrize(
    ("argv", "complaint"),
    [
        (["--append-commits"], "--for-gource"),
        (["--append-commits", "--for-gource", "--days", "30"], "--days"),
    ],
)
def test_append_commits_rejects_bad_flag_combinations(
    argv: list[str], complaint: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(argv)

    assert excinfo.value.code == 2
    assert complaint in capsys.readouterr().err


def test_append_commits_adds_to_the_existing_history(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = grow(repo)

    assert main(append_argv(repo, "--yes", "--seed", "1")) == 0

    shas = git(repo, "rev-list", "--reverse", "HEAD").splitlines()
    assert shas[:14] == original
    assert len(shas) == 24
    assert "appended 10 commits" in capsys.readouterr().out
    assert git(repo, "status", "--porcelain") == ""


def test_append_commits_dry_run_says_it_appends(
    repo: Path, wide_terminal: None, capsys: pytest.CaptureFixture[str]
) -> None:
    del wide_terminal
    original = grow(repo)
    capsys.readouterr()

    assert main(append_argv(repo, "--dry-run")) == 0

    out = capsys.readouterr().out
    assert "append to main" in out
    assert "destructive" not in out
    assert git(repo, "rev-list", "--reverse", "HEAD").splitlines() == original


def test_append_commits_refuses_a_dirty_tree(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grow(repo)
    (repo / "README.md").write_text("changed\n")

    assert main(append_argv(repo, "--yes")) == 1
    assert "uncommitted" in capsys.readouterr().err


def test_refuses_to_rewrite_without_a_terminal_or_yes(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = git(repo, "rev-parse", "HEAD")

    assert (
        main(["--days", "30", "--commits", "12", "--repo", str(repo), "--seed", "0"])
        == 1
    )

    assert "pass --yes" in capsys.readouterr().err
    assert git(repo, "rev-parse", "HEAD") == original
    assert git(repo, "status", "--porcelain") == "?? untracked.txt"


class _Tty:
    """Stands in for an interactive stdin."""

    def isatty(self) -> bool:
        return True


def _answering(text: str) -> Callable[..., str]:
    """A stand-in for ``input`` that always types ``text``."""

    def respond(*_: object) -> str:
        return text

    return respond


@pytest.fixture
def interactive_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", _Tty())


def test_answering_yes_at_the_prompt_rewrites(
    repo: Path, interactive_stdin: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del interactive_stdin
    monkeypatch.setattr("builtins.input", _answering("y"))

    assert (
        main(["--days", "30", "--commits", "6", "--repo", str(repo), "--seed", "0"])
        == 0
    )

    assert git(repo, "rev-list", "--count", "HEAD") == "6"


def test_answering_no_at_the_prompt_leaves_the_repo_alone(
    repo: Path, interactive_stdin: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither the history nor the index: nothing is staged until the user agrees."""
    del interactive_stdin
    monkeypatch.setattr("builtins.input", _answering("n"))
    original = git(repo, "rev-parse", "HEAD")

    assert (
        main(["--days", "30", "--commits", "6", "--repo", str(repo), "--seed", "0"])
        == 1
    )

    assert git(repo, "rev-parse", "HEAD") == original
    assert git(repo, "status", "--porcelain") == "?? untracked.txt"


def test_append_prompt_is_not_a_destructive_warning(
    repo: Path,
    interactive_stdin: None,
    wide_terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del interactive_stdin, wide_terminal
    original = grow(repo)
    capsys.readouterr()
    monkeypatch.setattr("builtins.input", _answering("n"))

    assert main(append_argv(repo)) == 1

    out = " ".join(capsys.readouterr().out.replace("│", " ").split())
    assert "append commits" in out
    assert "Branch main has 14 commits, 10 more commits will be added." in out
    assert "destructive" not in out
    assert "cannot be undone" not in out
    assert git(repo, "rev-list", "--reverse", "HEAD").splitlines() == original


def test_prompt_warns_what_will_be_lost(
    repo: Path,
    interactive_stdin: None,
    wide_terminal: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del interactive_stdin, wide_terminal
    monkeypatch.setattr("builtins.input", _answering("n"))

    assert (
        main(["--days", "30", "--commits", "6", "--repo", str(repo), "--seed", "0"])
        == 1
    )

    out = " ".join(capsys.readouterr().out.replace("│", " ").split())
    assert "destructive rewrite" in out
    assert "Branch main has 1 commit." in out
    assert "This cannot be undone" in out
    assert "This cannot be undone!*" in out
    assert "*unless you have an up-to-date copy on your remote" in out
    assert "skill issue." in out


@pytest.mark.parametrize("interruption", [EOFError, KeyboardInterrupt])
def test_interrupting_the_prompt_aborts_cleanly(
    repo: Path,
    interactive_stdin: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    interruption: type[BaseException],
) -> None:
    del interactive_stdin

    def interrupt(*_: object) -> str:
        raise interruption

    monkeypatch.setattr("builtins.input", interrupt)
    original = git(repo, "rev-parse", "HEAD")

    assert (
        main(["--days", "30", "--commits", "6", "--repo", str(repo), "--seed", "0"])
        == 1
    )

    assert "aborted" in capsys.readouterr().err
    assert git(repo, "rev-parse", "HEAD") == original


def test_reports_an_error_for_a_non_repo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(["--days", "30", "--commits", "12", "--repo", str(tmp_path), "--yes"]) == 1
    )
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--days", "0", "--commits", "10"],
        ["--days", "10", "--commits", "0"],
        ["--days", "-1", "--commits", "10"],
        ["--days", "10", "--commits", "-5"],
    ],
)
def test_rejects_non_positive_arguments(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        code: int | str | None = main(argv)
    except SystemExit as error:
        code = error.code

    assert code == 2
    assert capsys.readouterr().err != ""
