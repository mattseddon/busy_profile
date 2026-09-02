from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Collection
from datetime import UTC, datetime

from busy_profile.gource import GROUP_SIZE, INITIAL_MESSAGE, plan_gource_commits
from busy_profile.plan import Move, PlannedCommit, WriteFile, plan_commits
from busy_profile.text import NOUNS
from tests.conftest import replay

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


def plan(
    commits: int, *, seed: int = 0, reserved: Collection[str] = ()
) -> list[PlannedCommit]:
    return plan_gource_commits(
        30, commits, now=NOW, rng=random.Random(seed), reserved=reserved
    )


def written(commit: PlannedCommit) -> WriteFile:
    (change,) = commit.changes
    assert isinstance(change, WriteFile), commit
    return change


def moves(commit: PlannedCommit) -> list[Move]:
    assert commit.changes, commit
    assert all(isinstance(change, Move) for change in commit.changes), commit
    return [change for change in commit.changes if isinstance(change, Move)]


def folder_made_by(commit: PlannedCommit) -> str:
    folders = {move.destination.split("/")[0] for move in moves(commit)}
    assert len(folders) == 1, commit
    return folders.pop()


def test_initial_commit_changes_nothing() -> None:
    first = plan(5)[0]
    assert first.message == INITIAL_MESSAGE
    assert first.changes == ()


def test_the_next_three_commits_each_add_one_file_at_the_root() -> None:
    for commit in plan(5)[1:4]:
        change = written(commit)
        assert "/" not in change.path
        assert commit.message == f"Add {change.path}"


def test_each_new_file_holds_exactly_one_sentence() -> None:
    for commit in plan(5)[1:4]:
        content = written(commit).content
        assert content.endswith(".\n")
        assert content[0].isupper()
        assert len(content.splitlines()) == 1


def test_the_fifth_commit_moves_the_three_files_into_a_new_folder() -> None:
    planned = plan(5)
    names = [written(commit).path for commit in planned[1:4]]

    folder = folder_made_by(planned[4])
    assert [move.source for move in moves(planned[4])] == names
    assert [move.destination for move in moves(planned[4])] == [
        f"{folder}/{name}" for name in names
    ]
    assert planned[4].message == f"Move 3 files into {folder}"


def test_three_folders_of_files_are_grouped_into_a_taller_folder() -> None:
    planned = plan(14)
    folders = [folder_made_by(planned[i]) for i in (4, 8, 12)]

    assert [move.source for move in moves(planned[13])] == folders
    assert planned[13].message == f"Move 3 folders into {folder_made_by(planned[13])}"


def test_folders_of_different_heights_do_not_group() -> None:
    """By commit 23 the root holds a folder of folders and two folders of files.

    Those are three folders, but not three of a kind, so a file is added
    instead. The two folders of files wait for a third before they group.
    """
    planned = plan(27)

    written(planned[22])
    assert [move.source for move in moves(planned[26])] == [
        folder_made_by(planned[i]) for i in (17, 21, 25)
    ]


def test_grouping_is_never_random() -> None:
    """Which commits add and which group depends only on the commit count."""
    shapes = {
        tuple(
            isinstance(change, Move)
            for c in plan(60, seed=seed)
            for change in c.changes
        )
        for seed in range(5)
    }
    assert len(shapes) == 1


def test_every_folder_holds_exactly_three_things() -> None:
    tree = replay(plan(500))

    children: defaultdict[str, set[str]] = defaultdict(set)
    for path in tree:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            children["/".join(parts[:depth])].add(parts[depth])

    assert children
    assert all(len(names) == GROUP_SIZE for names in children.values())


def test_the_tree_grows_deep() -> None:
    """500 commits is enough for a folder five levels tall."""
    assert max(path.count("/") for path in replay(plan(500))) == 5


def test_reserved_names_are_never_used_at_the_root() -> None:
    planned = plan(60, reserved=NOUNS)

    for commit in planned[1:]:
        for change in commit.changes:
            path = change.path if isinstance(change, WriteFile) else change.destination
            assert path.split("/")[0] not in NOUNS


def test_folder_names_fall_back_to_adjective_noun_when_nouns_are_taken() -> None:
    planned = plan(5, reserved=NOUNS)
    assert folder_made_by(planned[4]) not in NOUNS


def test_same_seed_gives_the_same_plan() -> None:
    assert plan(100, seed=42) == plan(100, seed=42)


def test_different_seeds_give_different_names() -> None:
    assert plan(100, seed=1) != plan(100, seed=2)


def test_dates_match_the_plain_plan_for_the_same_seed() -> None:
    """``--for-gource`` changes what the commits do, not when they happen."""
    plain = plan_commits(30, 50, now=NOW, rng=random.Random(0))
    assert [c.timestamp for c in plan(50)] == [c.timestamp for c in plain]
