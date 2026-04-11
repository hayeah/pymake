"""Directory walker with a 3-stage filter pipeline.

Stage 1 — base exclude (``.gitignore`` or :class:`BuiltinIgnorer`)
Stage 2 — include globs (``Query.globs``)
Stage 3 — additional exclude (``Query.exclude``)

Directory pruning happens in-place on ``os.walk``'s ``dirnames`` list so
ignored subtrees (``node_modules/``, ``.git/`` …) are never entered.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .builtins import BuiltinIgnorer
from .gitignore import GitIgnore
from .glob import _CompiledGlobs, compile_globs, glob_match

__all__ = ["Entry", "Query", "walk"]


# ----------------------------------------------------------------------
# Public types
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """A single entry yielded by :func:`walk`.

    ``path`` is relative to the walk root. ``size`` and ``mtime_ns`` are
    zero unless ``Query.stat`` was set.
    """

    path: Path
    size: int = 0
    mtime_ns: int = 0
    is_dir: bool = False


@dataclass
class Query:
    """Walker configuration.

    Attributes:
        globs: Stage-2 include patterns. If set, only files matching at
            least one positive glob pass (with ``!`` prefix for negation).
        exclude: Stage-3 additional exclude. Bare names match any path
            component; glob chars trigger a ``glob_match`` against the
            full relative path.
        max_depth: 0 = unlimited (recursive). ``1`` yields a flat listing
            of files and immediate subdirs.
        stat: When True, populate ``Entry.size`` and ``Entry.mtime_ns``.
        no_ignore: Disable all ignore processing (gitignore, builtins,
            VCS dirs). Full kill switch — matches go-lstree semantics.
    """

    globs: list[str] | None = None
    exclude: list[str] | None = None
    max_depth: int = 0
    stat: bool = False
    no_ignore: bool = False


# ----------------------------------------------------------------------
# Ignorer protocol
# ----------------------------------------------------------------------


class _Ignorer(Protocol):
    def is_ignored(self, rel_path: str, is_dir: bool) -> bool: ...


# ----------------------------------------------------------------------
# Walker
# ----------------------------------------------------------------------


def walk(
    *paths: str | os.PathLike[str],
    query: Query | None = None,
) -> Iterator[Entry]:
    """Walk one or more paths, yielding :class:`Entry` for each match.

    Each path is walked independently. If a path points at a file, it is
    yielded directly (no filtering applied — you asked for it by name).
    Directories are walked recursively with the 3-stage pipeline.

    Results are yielded in sorted order within each directory.
    """
    q = query or Query()

    for raw in paths:
        root = Path(raw)

        if root.is_file():
            yield _file_entry(root, stat=q.stat, relative=root)
            continue

        if not root.is_dir():
            # silently skip missing paths — matches the "best-effort" feel
            # of tools like fd / rg
            continue

        yield from _walk_dir(root, q)


# ----------------------------------------------------------------------
# Per-root walker
# ----------------------------------------------------------------------


def _walk_dir(root: Path, q: Query) -> Iterator[Entry]:
    ignorer: _Ignorer | None
    if q.no_ignore:
        ignorer = None
    else:
        gi = GitIgnore.parse(root)
        ignorer = gi if gi.rules else BuiltinIgnorer()

    # Pre-compile include globs for the hot path
    include: _CompiledGlobs | None = None
    if q.globs:
        include = compile_globs(q.globs)

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = _rel_dir(Path(dirpath), root)
        depth = _depth_of(rel_dir)

        # Flat-listing mode: when we have reached max_depth, yield dir
        # entries at this level (but no files beyond), then stop
        # descending.
        if q.max_depth and depth >= q.max_depth:
            dirnames[:] = []

        # Prune ignored directories in-place
        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            rel = _join(rel_dir, d)
            if _dir_excluded(d, rel, ignorer, q):
                continue
            kept_dirs.append(d)
            # In flat mode, yield directory entries inline
            if q.max_depth and depth + 1 <= q.max_depth:
                # only yield at the max-depth boundary
                if depth + 1 == q.max_depth:
                    yield Entry(path=Path(rel), is_dir=True)
        dirnames[:] = kept_dirs

        # Files at this level
        if q.max_depth and depth >= q.max_depth:
            # already past the max depth (shouldn't normally hit) — skip
            continue

        for fname in sorted(filenames):
            rel = _join(rel_dir, fname)

            # Stage 1 — base exclude
            if ignorer is not None and ignorer.is_ignored(rel, False):
                continue

            # Stage 2 — include globs
            if include is not None and not include.match(rel):
                continue

            # Stage 3 — additional exclude
            if q.exclude and _exclude_match(q.exclude, rel):
                continue

            abs_path = Path(dirpath) / fname
            yield _file_entry(abs_path, stat=q.stat, relative=Path(rel))


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _file_entry(path: Path, *, stat: bool, relative: Path) -> Entry:
    if stat:
        st = path.stat()
        return Entry(
            path=relative,
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            is_dir=False,
        )
    return Entry(path=relative, size=0, mtime_ns=0, is_dir=False)


def _rel_dir(dirpath: Path, root: Path) -> str:
    """Return the relative directory path (forward-slash), '' for root."""
    try:
        rel = dirpath.relative_to(root)
    except ValueError:
        # dirpath is root itself via os.walk on some platforms
        return ""
    if rel == Path("."):
        return ""
    return rel.as_posix()


def _join(rel_dir: str, name: str) -> str:
    return name if not rel_dir else f"{rel_dir}/{name}"


def _depth_of(rel_dir: str) -> int:
    if not rel_dir:
        return 0
    return rel_dir.count("/") + 1


def _dir_excluded(
    name: str,
    rel: str,
    ignorer: _Ignorer | None,
    q: Query,
) -> bool:
    # VCS dirs: if we have an ignorer, let it decide. If no_ignore, skip
    # this check entirely (kill switch).
    if ignorer is not None and ignorer.is_ignored(rel, True):
        return True
    if q.exclude and _exclude_match(q.exclude, rel):
        return True
    return False


def _exclude_match(patterns: list[str], rel_path: str) -> bool:
    """Stage-3 exclude.

    Bare names (no glob chars, no slash) match against any path component.
    Anything else uses ``glob_match`` against the full relative path. A
    trailing ``/`` is stripped (gitignore-style directory marker).
    """
    parts = rel_path.split("/")
    for raw in patterns:
        pat = raw.rstrip("/")
        if not pat:
            continue
        if _is_glob(pat):
            if glob_match(pat, rel_path):
                return True
            continue
        # bare name — component match
        if pat in parts:
            return True
    return False


def _is_glob(pat: str) -> bool:
    return any(ch in pat for ch in "*?[") or "/" in pat
