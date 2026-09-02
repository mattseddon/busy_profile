from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import get_type_hints

import pytest

from busy_profile import __version__
from busy_profile.cli import Args, main, parse_args
from busy_profile.plan import DEFAULT_COMMITS, DEFAULT_DAYS
from tests.conftest import git


def test_version_flag_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_defaults_are_365_days_and_2500_commits() -> None:
    args = parse_args([])
    assert args.days == DEFAULT_DAYS
    assert args.commits == DEFAULT_COMMITS


def test_flags_override_defaults() -> None:
    args = parse_args(["--days", "30", "--commits", "100"])
    assert args.days == 30
    assert args.commits == 100


def test_every_attribute_args_declares_is_actually_populated() -> None:
    args = parse_args([])
    for name in get_type_hints(Args):
        assert hasattr(args, name), name


def test_args_attributes_have_their_declared_types() -> None:
    args = parse_args([])
    assert isinstance(args.days, int)
    assert isinstance(args.commits, int)
    assert isinstance(args.repo, Path)
    assert isinstance(args.dry_run, bool)
    assert isinstance(args.yes, bool)
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
    del interactive_stdin
    monkeypatch.setattr("builtins.input", _answering("n"))
    original = git(repo, "rev-parse", "HEAD")

    assert (
        main(["--days", "30", "--commits", "6", "--repo", str(repo), "--seed", "0"])
        == 1
    )

    assert git(repo, "rev-parse", "HEAD") == original


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
