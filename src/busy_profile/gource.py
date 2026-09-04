"""Planning a history that grows a tree of files and folders for gource.

`gource <https://gource.io>`_ animates a repository as a tree of files, so one
file rewritten thousands of times makes for a dull film. In this mode every
commit after the first does one of three things:

* adds one file at the root;
* gathers three siblings into a new folder;
* deletes a top-level folder in which some path repeats a word.

The tree grows like a base-3 counter. Three loose files at the root become a
folder of files (height 1), and three folders of the same height become one
folder a level taller. Folders of different heights are never mixed, so the
tree stays balanced. A folder is never added to once made, which means the root
is the only place where ungrouped items ever accumulate. Each commit therefore
looks at the root for something to group, starting with the smallest units and
working up through the heights, and adds a file only when nothing can be
grouped.

Growth is pruned by coincidence rather than by a limit. Folder names are drawn
from a small list, so sooner or later a new folder is given a name that already
names two folders beneath it, and a path like
``harbour/kettle/harbour/lantern/harbour`` appears. A word appearing
``REPEATS_TO_DOOM`` times in one path dooms the whole top-level folder it sits
in, ``harbour`` here: the next commit deletes that folder and everything beneath
it, and does nothing else. When such paths sit under several top-level folders
at once, the folder holding the deepest one goes first. File names are
hyphenated compounds and never count.
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

# A path in which one word names this many folders dooms its top-level folder.
REPEATS_TO_DOOM = 3

# An existing folder with more than this many files directly inside it is not
# one of ours, and is left alone when appending.
MAX_FOLDER_FILES = 3


@dataclass
class _Folder:
    """A folder in the planned tree: its name, whether it holds files directly,
    and its subfolders in the order they were placed there.

    Files themselves are not tracked, because nothing ever needs to move or
    delete a single file; a folder either holds some or it does not.
    """

    name: str
    has_files: bool
    children: list[_Folder] = field(default_factory=list)

    @property
    def height(self) -> int:
        """1 for a folder of files, one more than its tallest child otherwise."""
        return 1 + max((child.height for child in self.children), default=0)


@dataclass
class _Root:
    """What sits directly under the repository root as planning proceeds.

    ``taken`` holds every name in use at the root, including whatever was there
    before the rewrite, so a generated name never overwrites a file or merges
    into a folder that already exists.
    """

    taken: set[str]
    files: list[str] = field(default_factory=list)
    folders: list[_Folder] = field(default_factory=list)


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
    folder holding ``a/b.txt`` has height 2. Top-level folders in which a path
    already uses one word ``REPEATS_TO_DOOM`` times are deleted first, deepest
    such path first.

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
    top_level: dict[str, _Folder] = {}
    direct_files: Counter[str] = Counter()
    taken: set[str] = set()
    for path in sorted(set(paths)):
        parts = path.split("/")
        top = parts[0]
        taken.add(top)
        if top.startswith("."):
            continue
        if len(parts) == 1:
            files.append(top)
            continue
        if len(parts) == 2:
            direct_files[top] += 1
        folder = top_level.setdefault(top, _Folder(top, has_files=False))
        for name in parts[1:-1]:
            folder = _child(folder, name)
        folder.has_files = True

    folders = [
        f for f in top_level.values() if direct_files[f.name] <= MAX_FOLDER_FILES
    ]
    return _Root(taken=taken, files=files, folders=folders)


def _child(folder: _Folder, name: str) -> _Folder:
    for child in folder.children:
        if child.name == name:
            return child
    child = _Folder(name, has_files=False)
    folder.children.append(child)
    return child


def _next_commit(stamp: datetime, root: _Root, rng: random.Random) -> PlannedCommit:
    """Delete the top-level folder holding the deepest doomed path, else group
    the smallest complete set of siblings, else add a file."""
    doomed = _doomed(root)
    if doomed:
        return _delete(stamp, root, doomed[0][0])
    if len(root.files) >= GROUP_SIZE:
        return _group_files(stamp, root, rng)
    by_height: defaultdict[int, list[_Folder]] = defaultdict(list)
    for folder in root.folders:
        by_height[folder.height].append(folder)
    for height in sorted(by_height):
        if len(by_height[height]) >= GROUP_SIZE:
            return _group_folders(stamp, root, rng, by_height[height][:GROUP_SIZE])
    return _add_file(stamp, root, rng)


def _doomed(root: _Root) -> list[tuple[str, ...]]:
    """Paths of every folder that is the ``REPEATS_TO_DOOM``-th of its name on
    the way down from the root, deepest first.

    The first element of each path is the top-level folder that it dooms. A
    doomed path's own subtree is still searched so that, when such paths sit
    under different top-level folders, the deepest decides which goes first.
    """
    found: list[tuple[str, ...]] = []

    def walk(folder: _Folder, ancestors: tuple[str, ...]) -> None:
        path = (*ancestors, folder.name)
        if path.count(folder.name) >= REPEATS_TO_DOOM:
            found.append(path)
        for child in folder.children:
            walk(child, path)

    for folder in root.folders:
        walk(folder, ())
    return sorted(found, key=lambda path: (-len(path), path))


def _delete(stamp: datetime, root: _Root, name: str) -> PlannedCommit:
    """Delete the top-level folder ``name`` and everything beneath it."""
    root.folders.remove(next(f for f in root.folders if f.name == name))
    root.taken.discard(name)
    return PlannedCommit(stamp, f"Delete {name}", (Delete(name),))


def _add_file(stamp: datetime, root: _Root, rng: random.Random) -> PlannedCommit:
    name = _fresh(root.taken, file_names(rng))
    root.files.append(name)
    sentence = random_sentence(rng)
    return PlannedCommit(stamp, f"Add {name}", (WriteFile(name, f"{sentence}\n"),))


def _group_files(stamp: datetime, root: _Root, rng: random.Random) -> PlannedCommit:
    """Move the oldest ``GROUP_SIZE`` loose files into a new folder."""
    members = root.files[:GROUP_SIZE]
    folder = _new_folder(root, rng, members)
    del root.files[:GROUP_SIZE]
    root.folders.append(_Folder(folder, has_files=True))
    return _move_into(stamp, folder, members, kind="files")


def _group_folders(
    stamp: datetime, root: _Root, rng: random.Random, members: list[_Folder]
) -> PlannedCommit:
    """Move ``members``, three root folders of one height, into a new folder."""
    names = [member.name for member in members]
    folder = _new_folder(root, rng, names)
    for member in members:
        root.folders.remove(member)
    root.folders.append(_Folder(folder, has_files=False, children=list(members)))
    return _move_into(stamp, folder, names, kind="folders")


def _new_folder(root: _Root, rng: random.Random, members: Collection[str]) -> str:
    """Claim a folder name, then release the names of what is moving into it.

    The members are still in ``taken`` when the name is drawn, so the folder
    cannot share a name with one of them and end up renamed into itself.
    """
    folder = _fresh(root.taken, folder_names(rng))
    root.taken.difference_update(members)
    return folder


def _move_into(
    stamp: datetime, folder: str, members: Collection[str], *, kind: str
) -> PlannedCommit:
    moves = tuple(Move(member, f"{folder}/{member}") for member in members)
    return PlannedCommit(stamp, f"Move {len(members)} {kind} into {folder}", moves)


def _fresh(taken: set[str], candidates: Iterator[str]) -> str:
    """Take the first candidate not already in use at the root, and claim it."""
    name = next(name for name in candidates if name not in taken)
    taken.add(name)
    return name
