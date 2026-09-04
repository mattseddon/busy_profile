from __future__ import annotations

import random
from collections import Counter, defaultdict
from collections.abc import Collection
from datetime import UTC, datetime

from busy_profile.gource import (
    GROUP_SIZE,
    INITIAL_MESSAGE,
    REPEATS_TO_DOOM,
    plan_appended_commits,
    plan_gource_commits,
)
from busy_profile.plan import Delete, Move, PlannedCommit, WriteFile, plan_commits
from busy_profile.text import NOUNS
from tests.conftest import replay

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)
SINCE = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

EXISTING = {
    "README.md": "# hi\n",
    "notes.txt": "n\n",
    "todo.txt": "t\n",
    "deep/a/x.py": "x\n",
    "deep/b/y.py": "y\n",
    "deep/c/z.py": "z\n",
    "flat/p.py": "p\n",
    "flat/q.py": "q\n",
    "flat/r.py": "r\n",
    "level/s.py": "s\n",
    "level/t.py": "t\n",
    "level/u.py": "u\n",
    ".gitignore": "*.log\n",
    ".github/workflows/ci.yml": "on: push\n",
}


def append(
    commits: int, *, seed: int = 0, existing: dict[str, str] = EXISTING
) -> list[PlannedCommit]:
    return plan_appended_commits(
        commits, since=SINCE, now=NOW, rng=random.Random(seed), existing=existing
    )


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


def test_every_folder_holds_exactly_three_things() -> None:
    """Deletions take whole top-level folders, so no folder is ever left short."""
    tree = replay(plan(800))

    children: defaultdict[str, set[str]] = defaultdict(set)
    for path in tree:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            children["/".join(parts[:depth])].add(parts[depth])

    assert children
    assert all(len(names) == GROUP_SIZE for names in children.values())


def most_repeated(path: str) -> int:
    """How many folders in ``path`` share the most common folder name."""
    folders = path.split("/")[:-1]
    return max(Counter(folders).values(), default=0)


# Three folders of one name in a single path needs a six-level folder, which
# takes 1,093 commits to build. Seed 2 produces three such deletions in 2,500.
LONG_RUN = 2500
LONG_RUN_SEED = 2


def test_a_deleting_commit_removes_the_top_folder_just_made_and_nothing_else() -> None:
    """In a fresh run a third use of a word can only appear when a new top-level
    folder is named after two nested descendants, so each delete follows a
    grouping."""
    planned = plan(LONG_RUN, seed=LONG_RUN_SEED)
    deleting = [
        i
        for i, c in enumerate(planned)
        if any(isinstance(ch, Delete) for ch in c.changes)
    ]

    assert len(deleting) == 3
    for i in deleting:
        (change,) = planned[i].changes
        assert isinstance(change, Delete)
        assert "/" not in change.path
        assert change.path == folder_made_by(planned[i - 1])
        assert planned[i].message == f"Delete {change.path}"

        before = replay(planned[:i])
        beneath = [p for p in before if p.startswith(change.path + "/")]
        assert max(most_repeated(p) for p in beneath) == REPEATS_TO_DOOM
        assert not any(
            p.startswith(change.path + "/") for p in replay(planned[: i + 1])
        )


def test_a_word_used_twice_in_a_path_is_not_enough() -> None:
    tree = replay(plan(LONG_RUN, seed=LONG_RUN_SEED))
    assert any(most_repeated(path) == 2 for path in tree)


def test_no_surviving_path_uses_one_word_three_times() -> None:
    for path in replay(plan(LONG_RUN, seed=LONG_RUN_SEED)):
        assert most_repeated(path) < REPEATS_TO_DOOM, path


def test_reserved_names_are_never_used_at_the_root() -> None:
    planned = plan(60, reserved=NOUNS)

    for commit in planned[1:]:
        for change in commit.changes:
            path = change.destination if isinstance(change, Move) else change.path
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


def test_appended_commits_fall_between_the_last_commit_and_now() -> None:
    stamps = [commit.timestamp for commit in append(40)]

    assert len(stamps) == 40
    assert stamps == sorted(stamps)
    assert stamps[0] > SINCE
    assert stamps[-1] <= NOW


def test_appending_has_no_initial_commit() -> None:
    assert all(commit.changes for commit in append(5))


def test_appending_groups_the_existing_root_files_first() -> None:
    first = append(1)[0]

    assert [move.source for move in moves(first)] == [
        "README.md",
        "notes.txt",
        "todo.txt",
    ]
    assert first.message.startswith("Move 3 files into ")


def test_existing_folders_are_ranked_by_how_deep_their_files_go() -> None:
    """``deep`` holds folders of files, so it waits; the two flat folders group
    with the folder made from the root files as soon as that exists."""
    planned = append(2)

    grouped = folder_made_by(planned[0])
    assert [move.source for move in moves(planned[1])] == ["flat", "level", grouped]
    assert planned[1].message.startswith("Move 3 folders into ")


def test_a_word_used_three_times_beneath_a_top_folder_deletes_the_whole_folder() -> (
    None
):
    """``harbour/kettle/harbour/lantern/harbour`` uses one word three times, so
    all of ``harbour`` goes, including the sibling branches that do not."""
    existing = {
        **EXISTING,
        "harbour/kettle/harbour/lantern/harbour/x.py": "x\n",
        "harbour/kettle/harbour/y.py": "y\n",
        "harbour/ferry/z.py": "z\n",
    }

    first, second = append(2, existing=existing)

    assert first.changes == (Delete("harbour"),)
    assert first.message == "Delete harbour"
    tree = replay([first], existing)
    assert not any(path.startswith("harbour/") for path in tree)
    assert "deep/a/x.py" in tree
    assert [m.source for m in moves(second)] == ["README.md", "notes.txt", "todo.txt"]


def test_a_word_used_only_twice_beneath_a_top_folder_spares_it() -> None:
    existing = {**EXISTING, "harbour/kettle/harbour/x.py": "x\n"}

    planned = append(10, existing=existing)

    assert not any(isinstance(ch, Delete) for c in planned for ch in c.changes)
    assert "harbour/kettle/harbour/x.py" in replay(planned, existing)


def test_the_top_folder_holding_the_deepest_triple_goes_first() -> None:
    """``a/b/a/b/a`` completes its triple at level 5 and ``c/c/c`` at level 3,
    so ``a`` is deleted before ``c``; then normal growth resumes."""
    existing = {
        **EXISTING,
        "a/b/a/b/a/x.py": "x\n",
        "a/b/e/y.py": "y\n",
        "c/c/c/z.py": "z\n",
    }

    first, second, third = append(3, existing=existing)

    assert first.changes == (Delete("a"),)
    assert second.changes == (Delete("c"),)
    moves(third)
    tree = replay([first, second], existing)
    assert not any(path.startswith(("a/", "c/")) for path in tree)


def test_deleting_the_only_top_folder_frees_the_root() -> None:
    """Once ``x`` is gone the root is empty, so the next commit adds a file."""
    existing = {"x/y/x/x/f.py": "f\n"}

    first, second = append(2, existing=existing)

    assert first.changes == (Delete("x"),)
    added = written(second)
    assert replay([first, second], existing) == {added.path: added.content}


def test_appending_leaves_folders_with_more_than_three_files_alone() -> None:
    busy = {f"src/{name}.py": "" for name in ("a", "b", "c", "d")}
    existing = {**EXISTING, **busy}

    planned = append(60, existing=existing)

    tree = replay(planned, existing)
    assert all(path in tree for path in busy)
    for commit in planned:
        for change in commit.changes:
            assert not isinstance(change, Move) or change.source != "src"
            top = (
                change.path
                if isinstance(change, WriteFile | Delete)
                else change.destination
            ).split("/")[0]
            assert top != "src"


def test_a_folder_with_exactly_three_files_still_takes_part() -> None:
    """``flat`` in EXISTING has three files, and is grouped like one of ours."""
    planned = append(2)
    assert "flat" in [move.source for move in moves(planned[1])]


def test_appending_never_touches_hidden_entries() -> None:
    planned = append(200)

    for commit in planned:
        for change in commit.changes:
            touched = change.source if isinstance(change, Move) else change.path
            assert not touched.startswith(".")


def test_appending_never_reuses_a_name_already_at_the_root() -> None:
    tree = replay(append(200), EXISTING)
    assert set(EXISTING) & set(tree) == {".gitignore", ".github/workflows/ci.yml"}


def test_appending_carries_on_from_a_fresh_tree() -> None:
    """A fresh 41-commit tree is one complete three-level folder; appending
    starts by adding files beside it, and every step stays legal."""
    tree = replay(plan(41))

    appended = append(120, existing=tree)

    written(appended[0])
    after = replay(appended, tree)
    assert len(after) > len(tree)
    for path in after:
        assert most_repeated(path) < REPEATS_TO_DOOM, path
