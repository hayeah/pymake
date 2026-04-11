"""Sane directory tree walker with ``.gitignore`` support and builtin ignores.

Quick start::

    from pymake.lstree import walk, Query

    # Zero-config — yields the useful files in a tree
    for entry in walk("src/"):
        print(entry.path)

    # With include/exclude filters
    q = Query(globs=["**/*.py"], exclude=["__generated__"])
    for entry in walk(".", query=q):
        ...

See the module README for the full API and filter pipeline.
"""

from __future__ import annotations

from .builtins import (
    ALWAYS_IGNORED,
    BUILTIN_IGNORE_PATTERNS,
    BuiltinIgnorer,
)
from .gitignore import GitIgnore, Rule
from .glob import GlobError, glob_match, globs_match
from .walker import Entry, Query, walk

__all__ = [
    "walk",
    "Query",
    "Entry",
    "GitIgnore",
    "Rule",
    "BuiltinIgnorer",
    "ALWAYS_IGNORED",
    "BUILTIN_IGNORE_PATTERNS",
    "glob_match",
    "globs_match",
    "GlobError",
]
