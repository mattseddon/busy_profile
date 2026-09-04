from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from busy_profile.git import (
    TEMP_BRANCH,
    GitError,
    assert_writable,
    commit_count,
    current_branch,
    format_raw_date,
    run,
)
from tests.conftest import git

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


def test_run_reports_a_failed_command(repo: Path) -> None:
    with pytest.raises(GitError, match="`git rev-parse no-such-thing` failed"):
        run(repo, "rev-parse", "no-such-thing")


def test_assert_writable_returns_the_checked_out_branch(repo: Path) -> None:
    git(repo, "checkout", "-b", "feature/wip")
    assert assert_writable(repo) == "feature/wip"


def test_rejects_detached_head(repo: Path) -> None:
    git(repo, "checkout", "--detach")
    with pytest.raises(GitError, match="detached"):
        assert_writable(repo)


def test_rejects_existing_temp_branch(repo: Path) -> None:
    git(repo, "branch", TEMP_BRANCH)
    with pytest.raises(GitError, match="already exists"):
        assert_writable(repo)


def test_rejects_a_directory_that_is_not_a_repo(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        assert_writable(tmp_path)


def test_rejects_a_bare_repo(tmp_path: Path) -> None:
    git(tmp_path, "init", "--bare")
    with pytest.raises(GitError, match="bare"):
        assert_writable(tmp_path)


def test_current_branch_works_on_an_unborn_branch(tmp_path: Path) -> None:
    git(tmp_path, "init", "--initial-branch", "main")
    assert current_branch(tmp_path) == "main"
    assert commit_count(tmp_path) == 0


def test_format_raw_date_is_epoch_seconds_and_offset() -> None:
    assert format_raw_date(NOW) == "1788177600 +0000"


def test_format_raw_date_keeps_a_non_utc_offset() -> None:
    ten_hours_east = timezone(timedelta(hours=10))
    assert format_raw_date(NOW.astimezone(ten_hours_east)) == "1788177600 +1000"
