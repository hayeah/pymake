"""Builtin ignore lists — the "sane defaults" baseline.

``ALWAYS_IGNORED`` is VCS directories. It is only applied when there is no
``.gitignore`` at the walk root (i.e. the builtin path); if the project has
a ``.gitignore``, it already decides what to ignore.

``BUILTIN_IGNORE_PATTERNS`` covers common language ecosystem junk
(``node_modules``, ``__pycache__``, ``.venv`` …). Used only when no
``.gitignore`` is present — otherwise the project's own rules take over,
same as go-lstree.
"""

from __future__ import annotations

from .glob import glob_match

__all__ = ["ALWAYS_IGNORED", "BUILTIN_IGNORE_PATTERNS", "BuiltinIgnorer"]


# Always skip these VCS directories when using the builtin ignorer.
ALWAYS_IGNORED: frozenset[str] = frozenset({".git", ".hg", ".svn"})


# Common junk patterns across language ecosystems. Matched against the
# basename of a file or directory. Entries containing ``*`` are matched
# with :func:`glob_match`; plain entries use equality.
BUILTIN_IGNORE_PATTERNS: list[str] = [
    # JavaScript / TypeScript
    "node_modules",
    ".next",
    ".nuxt",
    # Python
    "__pycache__",
    ".venv",
    "venv",
    "*.egg-info",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    # Rust
    "target",
    # Swift / Xcode
    ".build",
    "DerivedData",
    # Java / Kotlin
    ".gradle",
    # Ruby
    ".bundle",
    # OS junk
    ".DS_Store",
    "Thumbs.db",
    # IDE
    ".idea",
    ".vscode",
    # Generic build outputs
    "dist",
    "build",
]


class BuiltinIgnorer:
    """Ignorer used when no ``.gitignore`` is present at the walk root.

    Matches a file/dir entry by basename against :data:`ALWAYS_IGNORED`
    and :data:`BUILTIN_IGNORE_PATTERNS`. Unlike ``GitIgnore``, this does
    not understand negation or nested scopes — just a flat allowlist.
    """

    __slots__ = ("_glob_patterns", "_exact_patterns")

    def __init__(self) -> None:
        self._exact_patterns: set[str] = set()
        self._glob_patterns: list[str] = []
        for pat in BUILTIN_IGNORE_PATTERNS:
            if "*" in pat or "?" in pat or "[" in pat:
                self._glob_patterns.append(pat)
            else:
                self._exact_patterns.add(pat)

    def is_ignored(self, rel_path: str, is_dir: bool) -> bool:
        """Match against the basename of ``rel_path``.

        Ignores VCS directories (:data:`ALWAYS_IGNORED`) and anything
        matching :data:`BUILTIN_IGNORE_PATTERNS`.
        """
        # basename is the last segment of the rel path
        base = rel_path.rsplit("/", 1)[-1]
        if is_dir and base in ALWAYS_IGNORED:
            return True
        if base in self._exact_patterns:
            return True
        for pat in self._glob_patterns:
            if glob_match(pat, base):
                return True
        return False
