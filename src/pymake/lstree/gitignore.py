"""Minimal ``.gitignore`` parser.

Supports the parts of the spec that actually matter for filtering a walk:

- Blank lines and ``#`` comments are skipped
- Trailing unescaped whitespace is stripped
- ``!`` prefix negates a rule (re-include previously excluded)
- Trailing ``/`` means the rule only matches directories
- A rule containing an interior ``/`` is anchored to the ``.gitignore``
  location; a rule with no ``/`` (after trailing-slash stripping) matches
  at any depth by basename
- ``*`` / ``**`` / ``?`` / ``[...]`` — standard gitignore glob syntax

Rules are evaluated in order and **the last match wins** (so a later ``!foo``
can re-include something an earlier pattern excluded).

This is *not* a full gitignore implementation — no nested ``.gitignore``
files, no ``\\`` escape handling beyond stripping, no case-insensitive mode.
The root-only scope is deliberate for v1; the spec calls out nested support
as a future extension.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["GitIgnore", "Rule"]


@dataclass
class Rule:
    """A single compiled ``.gitignore`` rule."""

    pattern: str  # original (post-parse) pattern, for debugging
    regex: re.Pattern[str]
    negated: bool
    dir_only: bool


@dataclass
class GitIgnore:
    """Parse and match a single ``.gitignore`` file.

    Construct via :meth:`parse` (reads ``<root>/.gitignore``) or
    :meth:`from_text` (parses a string, for tests).
    """

    root: Path
    rules: list[Rule] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def parse(cls, root: str | Path) -> GitIgnore:
        """Read ``<root>/.gitignore`` and return a matcher.

        Returns an empty matcher (no rules) if the file is absent or empty.
        """
        root_path = Path(root)
        path = root_path / ".gitignore"
        if not path.is_file():
            return cls(root=root_path)
        return cls.from_text(path.read_text(), root=root_path)

    @classmethod
    def from_text(cls, text: str, root: str | Path = ".") -> GitIgnore:
        gi = cls(root=Path(root))
        for raw in text.splitlines():
            rule = _parse_line(raw)
            if rule is not None:
                gi.rules.append(rule)
        return gi

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def is_ignored(self, rel_path: str, is_dir: bool) -> bool:
        """Return True if ``rel_path`` (relative to root, forward slashes)
        is ignored by this ``.gitignore``.

        Last matching rule wins. If no rule matches, returns False.
        """
        ignored = False
        for rule in self.rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.regex.search(rel_path):
                ignored = not rule.negated
        return ignored


# ----------------------------------------------------------------------
# Line parsing
# ----------------------------------------------------------------------


def _parse_line(raw: str) -> Rule | None:
    """Parse one ``.gitignore`` line. Returns None for blank/comment lines."""
    # strip trailing whitespace (git handles \  escape; v1 does not)
    line = raw.rstrip()
    if not line:
        return None
    if line.startswith("#"):
        return None

    negated = False
    if line.startswith("!"):
        negated = True
        line = line[1:]
        if not line:
            return None

    dir_only = False
    if line.endswith("/"):
        dir_only = True
        line = line[:-1]
        if not line:
            return None

    # Anchoring: if the pattern contains `/` after trailing-slash strip, it
    # is anchored to the .gitignore root.
    anchored = "/" in line
    if line.startswith("/"):
        line = line[1:]

    regex = _glob_to_regex(line, anchored=anchored)
    return Rule(pattern=line, regex=regex, negated=negated, dir_only=dir_only)


def _glob_to_regex(pattern: str, *, anchored: bool) -> re.Pattern[str]:
    """Convert a gitignore glob to a compiled regex.

    Anchored patterns match from the start of the path. Unanchored patterns
    match at any depth (treated as basename matches).
    """
    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # ** — any number of path segments
                # forms:
                #   **/    → zero or more leading segments
                #   /**    → zero or more trailing segments
                #   **     → match anything (including /)
                if i + 2 < n and pattern[i + 2] == "/":
                    parts.append("(?:.*/)?")
                    i += 3
                else:
                    parts.append(".*")
                    i += 2
            else:
                parts.append("[^/]*")
                i += 1
            continue

        if c == "?":
            parts.append("[^/]")
            i += 1
            continue

        if c == "[":
            # character class — preserve as-is up to the matching ]
            end = pattern.find("]", i + 1)
            if end == -1:
                # unterminated — treat literally
                parts.append(re.escape(c))
                i += 1
                continue
            cls_body = pattern[i + 1 : end]
            # git uses ! for negation; python regex uses ^
            if cls_body.startswith("!"):
                cls_body = "^" + cls_body[1:]
            parts.append("[" + cls_body + "]")
            i = end + 1
            continue

        parts.append(re.escape(c))
        i += 1

    body = "".join(parts)
    if anchored:
        # anchored: must match from the start; allow either exact match or
        # a directory prefix (so `drafts` matches `drafts/wip.epub`).
        return re.compile(f"^{body}(?:/|$)")
    # unanchored: match as a basename at any depth
    return re.compile(f"(?:^|/){body}(?:/|$)")
