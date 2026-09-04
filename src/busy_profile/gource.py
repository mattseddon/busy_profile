"""Planning a history that grows a tree of files and folders for gource.

`gource <https://gource.io>`_ animates a repository as a tree of files, so one
file rewritten thousands of times makes for a dull film. In this mode every
commit after the first does one of three things:

* adds one file at the root;
* gathers three siblings into a new folder;
* deletes a folder that has grown five levels deep.

The tree grows like a base-3 counter. Three loose files at the root become a
folder of files (height 1), and three folders of the same height become one
folder a level taller. Folders of different heights are never mixed, so the
tree stays balanced. A folder is never added to once made, which means the root
is the only place where ungrouped items ever accumulate. Each commit therefore
looks at the root for something to group, starting with the smallest units and
working up through the heights, and adds a file only when nothing can be
grouped.

Growth is not unbounded. The commit after one that makes a folder
``MAX_HEIGHT`` levels deep deletes that folder outright, and does nothing else,
so gource shows the tree collapse and start again.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

from busy_profile.plan import (
    Delete,
    Move,
    PlannedCommit,
    WriteFile,
    plan_timestamps,
    timestamps_after,
)
from busy_profile.text import file_names, folder_names, random_sentence

GROUP_SIZE = 3
INITIAL_MESSAGE = "Initial commit"

# A folder this many levels deep, holding files at ``x/a/b/c/d/file``, is
# deleted by the next commit.
MAX_HEIGHT = 5

# An existing folder with more than this many files directly inside it is not
# one of ours, and is left alone when appending.
MAX_FOLDER_FILES = 3


def _no_folders() -> defaultdict[int, list[str]]:
    return defaultdict(list)


@dataclass
class _Root:
    """What sits directly under the repository root as planning proceeds.

    Folders are tracked by height: a folder of files has height 1, a folder of
    those has height 2, and so on. ``taken`` holds every name in use at the
    root, including whatever was there before the rewrite, so a generated name
    never overwrites a file or merges into a folder that already exists.
    ``doomed`` lists folders that have reached ``MAX_HEIGHT`` and are waiting
    to be deleted.
    """

    taken: set[str]
    files: list[str] = field(default_factory=list)
    folders: defaultdict[int, list[str]] = field(default_factory=_no_folders)
    doomed: list[str] = field(default_factory=list)


def plan_gource_commits(
    days: int,
    commits: int,
    *,
    now: datetime,
    rng: random.Random,
    reserved: Collection[str] = (),
) -> list[PlannedCommit]:
    """Plan ``commits`` commits that grow a tree, dated like :func:`plan_commits`.

    The first commit changes nothing itself; it carries only the working-tree
    snapshot that :func:`~busy_profile.rewrite.rewrite_history` stages.
    ``reserved`` names whatever already sits at the root of the repository, so
    those files and folders are left alone and never take part in grouping.
    """
    stamps = plan_timestamps(days, commits, now=now, rng=rng)
    root = _Root(taken=set(reserved))
    planned = [PlannedCommit(stamps[0], INITIAL_MESSAGE, ())]
    planned.extend(_next_commit(stamp, root, rng) for stamp in stamps[1:])
    return planned


def plan_appended_commits(
    commits: int,
    *,
    since: datetime,
    now: datetime,
    rng: random.Random,
    existing: Iterable[str],
) -> list[PlannedCommit]:
    """Plan ``commits`` commits that carry on growing a tree that already exists.

    ``existing`` is every tracked path in the repository, relative to its root.
    Unlike a fresh rewrite, what is at the root takes part: loose files are
    grouped, and existing folders are ranked by how deep their files go, so a
    folder holding ``a/b.txt`` has height 2. A folder already ``MAX_HEIGHT``
    deep is deleted first.

    Two kinds of entry are left alone. Hidden ones such as ``.gitignore`` or
    ``.github``, because moving them would change what they do; and folders
    with more than ``MAX_FOLDER_FILES`` files directly inside, which no run of
    this tool would have made and so are presumed to be real work.

    The commits are dated uniformly between ``since``, the time of the most
    recent existing commit, and ``now``, and are written on top of that commit
    by :func:`~busy_profile.append.append_commits`.
    """
    stamps = timestamps_after(since, commits, now=now, rng=rng)
    root = _root_from_paths(existing)
    return [_next_commit(stamp, root, rng) for stamp in stamps]


def _root_from_paths(paths: Iterable[str]) -> _Root:
    files: list[str] = []
    heights: dict[str, int] = {}
    direct_files: Counter[str] = Counter()
    taken: set[str] = set()
    for path in sorted(set(paths)):
        top, _, rest = path.partition("/")
        taken.add(top)
        if top.startswith("."):
            continue
        if not rest:
            files.append(top)
            continue
        heights[top] = max(heights.get(top, 0), 1 + rest.count("/"))
        if "/" not in rest:
            direct_files[top] += 1

    root = _Root(taken=taken, files=files)
    for folder, height in heights.items():
        if direct_files[folder] > MAX_FOLDER_FILES:
            continue
        if height >= MAX_HEIGHT:
            root.doomed.append(folder)
        else:
            root.folders[height].append(folder)
    return root


def _next_commit(stamp: datetime, root: _Root, rng: random.Random) -> PlannedCommit:
    """Delete a doomed folder, else group the smallest complete set of siblings,
    else add a file."""
    if root.doomed:
        return _delete(stamp, root)
    if len(root.files) >= GROUP_SIZE:
        return _group(stamp, root, rng, root.files, height=0)
    for height in sorted(root.folders):
        if len(root.folders[height]) >= GROUP_SIZE:
            return _group(stamp, root, rng, root.folders[height], height=height)
    return _add_file(stamp, root, rng)


def _delete(stamp: datetime, root: _Root) -> PlannedCommit:
    folder = root.doomed.pop(0)
    root.taken.discard(folder)
    return PlannedCommit(stamp, f"Delete {folder}", (Delete(folder),))


def _add_file(stamp: datetime, root: _Root, rng: random.Random) -> PlannedCommit:
    name = _fresh(root.taken, file_names(rng))
    root.files.append(name)
    sentence = random_sentence(rng)
    return PlannedCommit(stamp, f"Add {name}", (WriteFile(name, f"{sentence}\n"),))


def _group(
    stamp: datetime,
    root: _Root,
    rng: random.Random,
    siblings: list[str],
    *,
    height: int,
) -> PlannedCommit:
    """Move the oldest ``GROUP_SIZE`` of ``siblings`` into a new folder."""
    members = siblings[:GROUP_SIZE]
    # Members are still in ``taken`` here, so the folder cannot share a name
    # with one of them and end up renamed into itself.
    folder = _fresh(root.taken, folder_names(rng))
    del siblings[:GROUP_SIZE]
    root.taken.difference_update(members)
    if height + 1 >= MAX_HEIGHT:
        root.doomed.append(folder)
    else:
        root.folders[height + 1].append(folder)

    kind = "files" if height == 0 else "folders"
    moves = tuple(Move(member, f"{folder}/{member}") for member in members)
    return PlannedCommit(stamp, f"Move {len(members)} {kind} into {folder}", moves)


def _fresh(taken: set[str], candidates: Iterator[str]) -> str:
    """Take the first candidate not already in use at the root, and claim it."""
    name = next(name for name in candidates if name not in taken)
    taken.add(name)
    return name
