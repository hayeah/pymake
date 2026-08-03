"""Directory-level change detection via mtime+size fingerprinting.

A :class:`TreeDigest` fingerprints a set of files and directories using the
rsync trick (mtime+size per file) and persists the final digest string to a
caller-specified digest file. Tasks use :meth:`TreeDigest.changed` as a
``run_if`` predicate; the executor calls :meth:`TreeDigest.commit` after the
task succeeds to update the stored digest.

Directory walking is delegated to :mod:`pymake.lstree`, which gives us
``.gitignore`` + builtin-ignore filtering (``node_modules``, ``__pycache__``,
``.venv`` …) for free.
"""

from __future__ import annotations

import functools
import hashlib
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pymake.lstree import Query, walk

__all__ = ["TreeDigest", "tree_digest", "severed_commit_wrapper"]


def _make_hasher() -> tuple[Any, str]:
    """Return ``(hasher, algo_tag)`` for fingerprinting."""
    try:
        import xxhash  # type: ignore[import-not-found]

        return xxhash.xxh64(), "xxhash64"
    except ImportError:
        return hashlib.blake2b(digest_size=8), "blake2b64"


@dataclass(frozen=True)
class _TreeEntry:
    logical_path: str
    mtime_ns: int
    size: int


@dataclass
class _PendingDigest:
    entries: list[_TreeEntry]
    aggregate_digest: str | None


def _hash_lines(lines: list[str]) -> str:
    hasher, algo = _make_hasher()
    for line in lines:
        hasher.update(line.encode())
    return f"{algo}:{hasher.hexdigest()}"


class TreeDigest:
    """Fingerprint a set of files/directories and persist the result."""

    def __init__(
        self,
        *paths: str | Path,
        digest: str | Path,
        exclude: list[str] | None = None,
        globs: list[str] | None = None,
    ) -> None:
        if not paths:
            raise ValueError("tree_digest requires at least one path")

        self.paths: tuple[Path, ...] = tuple(Path(p) for p in paths)
        self.exclude: list[str] = list(exclude or [])
        self.globs: list[str] = list(globs or [])
        self.digest_path: Path = Path(digest)
        self._pending: _PendingDigest | None = None

    def _walk(self) -> list[_TreeEntry]:
        """Collect logical paths plus file metadata for all watched files."""
        q = Query(
            globs=self.globs or None,
            exclude=[".pymake", *self.exclude] if self.exclude else [".pymake"],
            stat=True,
        )

        digest_abs = self.digest_path.resolve(strict=False)
        entries: list[_TreeEntry] = []
        for root in self.paths:
            root_label = root.as_posix()
            if root.is_file():
                resolved = root.resolve()
                if resolved == digest_abs:
                    continue
                st = root.stat()
                entries.append(
                    _TreeEntry(
                        logical_path=root_label,
                        mtime_ns=st.st_mtime_ns,
                        size=st.st_size,
                    )
                )
                continue
            if not root.is_dir():
                entries.append(
                    _TreeEntry(
                        logical_path=root_label + "::missing",
                        mtime_ns=0,
                        size=0,
                    )
                )
                continue

            root_abs = root.resolve()
            for entry in walk(root, query=q):
                if entry.is_dir:
                    continue
                entry_abs = root_abs / entry.path
                if entry_abs == digest_abs:
                    continue
                entries.append(
                    _TreeEntry(
                        logical_path=f"{root_label}/{entry.path.as_posix()}",
                        mtime_ns=entry.mtime_ns,
                        size=entry.size,
                    )
                )

        return entries

    def _aggregate_digest(self, entries: list[_TreeEntry]) -> str:
        return _hash_lines(
            [
                f"{entry.logical_path}\t{entry.mtime_ns}\t{entry.size}\n"
                for entry in entries
            ]
        )

    def _prepare_pending(self) -> _PendingDigest:
        if self._pending is None:
            self._pending = _PendingDigest(
                entries=self._walk(),
                aggregate_digest=None,
            )
        return self._pending

    def _compute(self) -> str:
        """Compute the fingerprint string (``"<algo>:<hex>"``)."""
        pending = self._prepare_pending()
        if pending.aggregate_digest is None:
            pending.aggregate_digest = self._aggregate_digest(pending.entries)
        return pending.aggregate_digest

    def changed(self) -> bool:
        """Return ``True`` if the digest differs from the stored digest file."""
        current = self._compute()
        try:
            stored = self.digest_path.read_text().strip()
        except OSError:
            return True
        return stored != current

    def commit(self) -> None:
        """Write the current digest to the digest file."""
        current = self._compute()
        self.digest_path.parent.mkdir(parents=True, exist_ok=True)
        self.digest_path.write_text(current + "\n")

    def reset(self) -> None:
        """Drop the cached current digest (useful in tests)."""
        self._pending = None


def tree_digest(
    *paths: str | Path,
    digest: str | Path,
    exclude: list[str] | None = None,
    globs: list[str] | None = None,
) -> TreeDigest:
    """Create a :class:`TreeDigest` for the given paths.

    .. deprecated::
        Use an Input instead: ``git(id, repo, paths=[...])`` for trees in
        git (ignore rules and content identity for free), or a custom
        Input for genuinely un-gitted trees. See
        ``docs/input-contract-design.md``.
    """
    warnings.warn(
        "tree_digest is deprecated: use inputs=[git(id, repo, paths=[...])] "
        "(or a custom Input) — the executor records fingerprints itself, "
        "with no digest file or commit protocol",
        FutureWarning,
        stacklevel=2,
    )
    return TreeDigest(
        *paths,
        digest=digest,
        exclude=exclude,
        globs=globs,
    )


def _one_hop_reachable(fn: Callable[..., Any]) -> Iterator[Any]:
    """Objects reachable from *fn* in one inspection hop.

    Covers the common wrapper shapes: closure cells, referenced module
    globals (the canonical Makefile shape — a module-level digest used
    inside a ``def``), ``functools.partial`` (func/args/keywords), argument
    defaults, and — for bound methods — the instance and its attributes.
    Bound methods found along the way also yield their ``__self__``.
    Deeper indirection (a digest returned by a call, doubly nested
    wrappers) is NOT detected; this is a best-effort migration tripwire,
    not an analysis pass.
    """

    def expand(obj: Any) -> Iterator[Any]:
        yield obj
        owner = getattr(obj, "__self__", None)
        if owner is not None:
            yield owner

    if isinstance(fn, functools.partial):
        yield from expand(fn.func)
        for arg in fn.args:
            yield from expand(arg)
        for arg in fn.keywords.values():
            yield from expand(arg)
        return

    closure = getattr(fn, "__closure__", None)
    if closure:
        for cell in closure:
            try:
                yield from expand(cell.cell_contents)
            except ValueError:  # empty cell
                continue

    code = getattr(fn, "__code__", None)
    fn_globals = getattr(fn, "__globals__", None)
    if code is not None and fn_globals is not None:
        for global_name in code.co_names:
            if global_name in fn_globals:
                yield from expand(fn_globals[global_name])

    for default in getattr(fn, "__defaults__", None) or ():
        yield from expand(default)
    for default in (getattr(fn, "__kwdefaults__", None) or {}).values():
        yield from expand(default)

    owner = getattr(fn, "__self__", None)
    if owner is not None:
        yield owner
        try:
            attrs = vars(owner)
        except TypeError:
            attrs = {}
        for attr_value in attrs.values():
            yield from expand(attr_value)


def severed_commit_wrapper(run_if: Callable[..., Any] | None) -> TreeDigest | None:
    """Detect the severed-wrapper signature on a legacy ``run_if``.

    The executor discovers a digest's commit half by convention — ``commit``
    on the callable or its ``__self__``. A plain function wrapping
    ``digest.changed()`` has neither: the digest file never settles and the
    task rebuilds forever, silently. Return the reachable
    :class:`TreeDigest` when *run_if* holds one but exposes no ``commit``;
    detection limits are those of :func:`_one_hop_reachable`.
    """
    if run_if is None:
        return None
    if callable(getattr(run_if, "commit", None)):
        return None
    owner = getattr(run_if, "__self__", None)
    if owner is not None and callable(getattr(owner, "commit", None)):
        return None
    for candidate in _one_hop_reachable(run_if):
        if isinstance(candidate, TreeDigest):
            return candidate
    return None
