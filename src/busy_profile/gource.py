"""Planning a history that grows a tree of files and folders for gource.

`gource <https://gource.io>`_ animates a repository as a tree of files, so one
file rewritten thousands of times makes for a dull film. In this mode every
commit after the first either adds one file at the root or gathers three
siblings into a new folder, and the tree grows like a base-3 counter:

* three loose files at the root become a folder of files (height 1);
* three folders of the same height become one folder a level taller.

Folders of different heights are never mixed, so the tree stays balanced. A
folder is never added to once made, which means the root is the only place
where ungrouped items ever accumulate. Each commit therefore looks at the root
for something to group, starting with the smallest units and working up
through the heights, and adds a file only when nothing can be grouped.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field
from datetime import datetime

from busy_profile.plan import Move, PlannedCommit, WriteFile, plan_timestamps
from busy_profile.text import file_names, folder_names, random_sentence

GROUP_SIZE = 3
INITIAL_MESSAGE = "Initial commit"


def _no_folders() -> defaultdict[int, list[str]]:
    return defaultdict(list)


@dataclass
class _Root:
    """What sits directly under the repository root as planning proceeds.

    Folders are tracked by height: a folder of files has height 1, a folder of
    those has height 2, and so on. ``taken`` holds every name in use at the
    root, including whatever was there before the rewrite, so a generated name
    never overwrites a file or merges into a folder that already exists.
    """

    taken: set[str]
    files: list[str] = field(default_factory=list)
    folders: defaultdict[int, list[str]] = field(default_factory=_no_folders)


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


def _next_commit(stamp: datetime, root: _Root, rng: random.Random) -> PlannedCommit:
    """Group the smallest complete set of siblings, or add a file if there is none."""
    if len(root.files) >= GROUP_SIZE:
        return _group(stamp, root, rng, root.files, height=0)
    for height in sorted(root.folders):
        if len(root.folders[height]) >= GROUP_SIZE:
            return _group(stamp, root, rng, root.folders[height], height=height)
    return _add_file(stamp, root, rng)


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
    root.folders[height + 1].append(folder)

    kind = "files" if height == 0 else "folders"
    moves = tuple(Move(member, f"{folder}/{member}") for member in members)
    return PlannedCommit(stamp, f"Move {len(members)} {kind} into {folder}", moves)


def _fresh(taken: set[str], candidates: Iterator[str]) -> str:
    """Take the first candidate not already in use at the root, and claim it."""
    name = next(name for name in candidates if name not in taken)
    taken.add(name)
    return name
