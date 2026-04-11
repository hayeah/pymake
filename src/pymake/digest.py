"""Directory-level change detection via mtime+size fingerprinting.

A :class:`TreeDigest` fingerprints a set of files and directories using the
rsync trick (mtime+size per file, not content hashing) and persists the
fingerprint to a small state file. Tasks use the instance method
:meth:`TreeDigest.changed` as a ``run_if`` predicate; the executor calls
:meth:`TreeDigest.commit` after the task succeeds to update the stored state.

Directory walking is delegated to :mod:`hayeah.core.lstree`, which gives us
``.gitignore`` + builtin-ignore filtering (``node_modules``, ``__pycache__``,
``.venv`` …) for free. That import is lazy so the rest of pymake can still
load when ``hayeah-core`` is not installed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

__all__ = ["TreeDigest", "tree_digest"]


def _lstree_walk_query() -> tuple[Any, Any]:
    """Lazy import of :mod:`hayeah.core.lstree`.

    Returns ``(walk, Query)``. Raises :class:`ImportError` with a helpful
    install hint if the library isn't available.
    """
    try:
        from hayeah.core.lstree import Query, walk  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "pymake.tree_digest requires the 'hayeah-core' package for "
            "directory walking (provides hayeah.core.lstree). Install it "
            "with `uv pip install hayeah-core` or from the dotfiles lib at "
            "github.com/hayeah/dotfiles/libs/hayeah-py."
        ) from exc
    return walk, Query


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
        exclude: list[str] | None = None,
        globs: list[str] | None = None,
        state: str | Path | None = None,
        name: str | None = None,
    ) -> None:
        if not paths:
            raise ValueError("tree_digest requires at least one path")

        self.paths: tuple[Path, ...] = tuple(Path(p) for p in paths)
        self.exclude: list[str] = list(exclude or [])
        self.globs: list[str] = list(globs or [])
        self.name: str | None = name
        self.state_path: Path | None = (
            Path(state) if state is not None else self._default_state_path()
        )
        self._current_digest: str | None = None

    def _default_state_path(self) -> Path:
        """Derive a stable default state file under ``.pymake/``."""
        slug = self.name or self._slug_from_paths()
        return Path(".pymake") / f"{slug}.digest"

    def _slug_from_paths(self) -> str:
        """Deterministic short slug derived from the supplied paths."""
        joined = "\n".join(p.as_posix() for p in self.paths).encode()
        return hashlib.blake2b(joined, digest_size=6).hexdigest()

    def _walk(self) -> list[tuple[str, int, int]]:
        """Collect ``(key, mtime_ns, size)`` tuples for all watched files.

        The key is ``f"{supplied_path}/{entry_relpath}"`` for directory roots
        and ``supplied_path`` for file roots — stable across runs so long as
        the caller supplies the same path strings, and distinguishes
        identically-named entries across different roots.

        The state file (if it falls under a watched root) is filtered out so
        :meth:`commit` doesn't create a feedback loop on the next
        :meth:`changed` call.
        """
        walk, Query = _lstree_walk_query()  # noqa: N806 — ``Query`` is the class name
        q = Query(
            globs=self.globs or None,
            exclude=self.exclude or None,
            stat=True,
        )

        # Resolve the state file's absolute path once so we can skip it
        # during the walk. ``resolve(strict=False)`` is a no-op if the file
        # doesn't exist yet.
        state_abs: Path | None = (
            self.state_path.resolve() if self.state_path is not None else None
        )

        entries: list[tuple[str, int, int]] = []
        for root in self.paths:
            root_label = root.as_posix()
            if root.is_file():
                if state_abs is not None and root.resolve() == state_abs:
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
                if state_abs is not None and (root_abs / entry.path) == state_abs:
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
        """Return ``True`` if the digest differs from the stored state.

        Designed to be passed directly as a ``run_if`` predicate::

            digest = tree_digest("src/")
            @task(run_if=digest.changed, touch=".pymake/done")
            def build(): ...

        Computes the current digest on first call and caches it on the
        instance so :meth:`commit` can write it without re-walking.
        """
        current = self._ensure_current()
        if self.state_path is None or not self.state_path.exists():
            return True
        try:
            stored = self.state_path.read_text().strip()
        except OSError:
            return True
        return stored != current

    def commit(self) -> None:
        """Write the current digest to the state file.

        Called by the executor after a successful task run. If
        :meth:`changed` hasn't been called yet, the digest is computed now so
        ``commit`` still records an accurate snapshot.
        """
        if self.state_path is None:
            return
        current = self._ensure_current()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(current + "\n")

    def reset(self) -> None:
        """Drop the cached current digest (useful in tests)."""
        self._current_digest = None


def tree_digest(
    *paths: str | Path,
    exclude: list[str] | None = None,
    globs: list[str] | None = None,
    state: str | Path | None = None,
    name: str | None = None,
) -> TreeDigest:
    """Create a :class:`TreeDigest` for the given paths.

    Args:
        *paths: Files and directories to watch. Directories are walked
            recursively via ``hayeah.core.lstree`` with gitignore + builtin
            junk excluded by default.
        exclude: Additional exclude patterns layered on top of lstree's
            defaults. Passed through to :class:`hayeah.core.lstree.Query`.
        globs: Optional include filter (e.g. ``["**/*.ts", "**/*.tsx"]``).
        state: Path to the state file. Defaults to
            ``.pymake/<slug>.digest`` where ``slug`` is derived from the
            paths (or ``name`` if supplied).
        name: Optional explicit slug used for the default state path.

    Returns:
        A configured :class:`TreeDigest`.
    """
    return TreeDigest(
        *paths,
        exclude=exclude,
        globs=globs,
        state=state,
        name=name,
    )
