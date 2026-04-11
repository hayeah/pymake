"""Directory-level change detection via mtime+size fingerprinting.

A :class:`TreeDigest` fingerprints a set of files and directories using the
rsync trick (mtime+size per file, not content hashing) and persists the
fingerprint to a small digest file. Tasks use the instance method
:meth:`TreeDigest.changed` as a ``run_if`` predicate; the executor calls
:meth:`TreeDigest.commit` after the task succeeds to update the stored digest.

Directory walking is delegated to :mod:`pymake.lstree`, which gives us
``.gitignore`` + builtin-ignore filtering (``node_modules``, ``__pycache__``,
``.venv`` …) for free.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pymake.lstree import Query, walk

__all__ = ["TreeDigest", "tree_digest"]


def _make_hasher() -> tuple[Any, str]:
    """Return ``(hasher, algo_tag)`` for fingerprinting.

    Prefers ``xxhash.xxh64`` (fast, 8 bytes) and falls back to
    ``hashlib.blake2b(digest_size=8)`` so the feature works without any
    extra dependency.
    """
    try:
        import xxhash  # type: ignore[import-not-found]

        return xxhash.xxh64(), "xxhash64"
    except ImportError:
        return hashlib.blake2b(digest_size=8), "blake2b64"


class TreeDigest:
    """Fingerprint a set of files/directories and persist the result.

    See :func:`tree_digest` for the typical entry point.
    """

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
        self._current_digest: str | None = None

    def _walk(self) -> list[tuple[str, int, int]]:
        """Collect ``(key, mtime_ns, size)`` tuples for all watched files.

        The key is ``f"{supplied_path}/{entry_relpath}"`` for directory roots
        and ``supplied_path`` for file roots — stable across runs so long as
        the caller supplies the same path strings, and distinguishes
        identically-named entries across different roots.

        The digest file (if it falls under a watched root) is filtered out so
        :meth:`commit` doesn't create a feedback loop on the next
        :meth:`changed` call.
        """
        q = Query(
            globs=self.globs or None,
            exclude=self.exclude or None,
            stat=True,
        )

        # Resolve the digest file's absolute path once so we can skip it
        # during the walk. ``resolve(strict=False)`` is a no-op if the file
        # doesn't exist yet.
        digest_abs: Path = self.digest_path.resolve()

        entries: list[tuple[str, int, int]] = []
        for root in self.paths:
            root_label = root.as_posix()
            if root.is_file():
                if root.resolve() == digest_abs:
                    continue
                st = root.stat()
                entries.append((root_label, st.st_mtime_ns, st.st_size))
                continue
            if not root.is_dir():
                # missing path — record as a sentinel so the digest still
                # changes when the path appears later
                entries.append((root_label + "::missing", 0, 0))
                continue

            root_abs = root.resolve()
            for entry in walk(root, query=q):
                if entry.is_dir:
                    continue
                if (root_abs / entry.path) == digest_abs:
                    continue
                key = f"{root_label}/{entry.path.as_posix()}"
                entries.append((key, entry.mtime_ns, entry.size))

        entries.sort(key=lambda e: e[0])
        return entries

    def _compute(self) -> str:
        """Compute the fingerprint string (``"<algo>:<hex>"``)."""
        hasher, algo = _make_hasher()
        for key, mtime, size in self._walk():
            hasher.update(f"{key}\t{mtime}\t{size}\n".encode())
        return f"{algo}:{hasher.hexdigest()}"

    def _ensure_current(self) -> str:
        if self._current_digest is None:
            self._current_digest = self._compute()
        return self._current_digest

    def changed(self) -> bool:
        """Return ``True`` if the digest differs from the stored digest file.

        Designed to be passed directly as a ``run_if`` predicate::

            digest = tree_digest("src/", digest=".build/src.digest")
            @task(run_if=digest.changed)
            def build(): ...

        Computes the current digest on first call and caches it on the
        instance so :meth:`commit` can write it without re-walking.
        """
        current = self._ensure_current()
        if not self.digest_path.exists():
            return True
        try:
            stored = self.digest_path.read_text().strip()
        except OSError:
            return True
        return stored != current

    def commit(self) -> None:
        """Write the current digest to the digest file.

        Called by the executor after a successful task run. If
        :meth:`changed` hasn't been called yet, the digest is computed now so
        ``commit`` still records an accurate snapshot.
        """
        current = self._ensure_current()
        self.digest_path.parent.mkdir(parents=True, exist_ok=True)
        self.digest_path.write_text(current + "\n")

    def reset(self) -> None:
        """Drop the cached current digest (useful in tests)."""
        self._current_digest = None


def tree_digest(
    *paths: str | Path,
    digest: str | Path,
    exclude: list[str] | None = None,
    globs: list[str] | None = None,
) -> TreeDigest:
    """Create a :class:`TreeDigest` for the given paths.

    Args:
        *paths: Files and directories to watch. Directories are walked
            recursively via :mod:`pymake.lstree` with gitignore + builtin
            junk excluded by default.
        digest: Path to the digest file. Required, and always caller-supplied
            — there is no default. Pick a location that's already gitignored,
            e.g. next to your build output (``".build/web.digest"``).
        exclude: Additional exclude patterns layered on top of lstree's
            defaults. Passed through to :class:`pymake.lstree.Query`.
        globs: Optional include filter (e.g. ``["**/*.ts", "**/*.tsx"]``).

    Returns:
        A configured :class:`TreeDigest`.
    """
    return TreeDigest(
        *paths,
        digest=digest,
        exclude=exclude,
        globs=globs,
    )
