"""Glob pattern matching with ``**`` (doublestar) support.

Semantics match go-lstree's ``glob.go`` and gitignore:

- ``*`` matches any sequence of characters except ``/``
- ``**`` matches zero or more path segments (can cross ``/``)
- ``?`` matches one character except ``/``
- ``[abc]`` / ``[a-z]`` / ``[!abc]`` — character class with optional negation
- Patterns without ``/`` or ``**`` only match at the top level

Use ``**/*.ext`` to match recursively.
"""

from __future__ import annotations

import re
from functools import lru_cache

__all__ = ["glob_match", "globs_match", "compile_globs", "GlobError"]


class GlobError(ValueError):
    """Raised when a glob pattern is malformed (e.g. unclosed ``[``)."""


def glob_match(pattern: str, name: str) -> bool:
    """Return True if ``name`` matches ``pattern``.

    Raises :class:`GlobError` on malformed patterns.
    """
    return _match(pattern, name)


def _match(pattern: str, name: str) -> bool:
    while pattern:
        c = pattern[0]
        if c == "*":
            if len(pattern) > 1 and pattern[1] == "*":
                # ** — doublestar, consume the two stars
                pattern = pattern[2:]
                # optional trailing slash
                if pattern.startswith("/"):
                    pattern = pattern[1:]
                # empty rest — match anything
                if not pattern:
                    return True
                # try matching rest at every position (including 0 and len(name))
                for i in range(len(name) + 1):
                    if _match(pattern, name[i:]):
                        return True
                return False

            # single * — match any non-slash sequence
            pattern = pattern[1:]
            if not pattern:
                # trailing * matches rest iff no slashes remain
                return "/" not in name
            for i in range(len(name) + 1):
                if i > 0 and name[i - 1] == "/":
                    break
                if _match(pattern, name[i:]):
                    return True
            return False

        if c == "?":
            if not name or name[0] == "/":
                return False
            pattern = pattern[1:]
            name = name[1:]
            continue

        if c == "[":
            if not name or name[0] == "/":
                return False
            pattern = pattern[1:]
            negate = False
            if pattern and pattern[0] in ("!", "^"):
                negate = True
                pattern = pattern[1:]
            matched = False
            nrange = 0
            while True:
                if not pattern:
                    raise GlobError("unclosed character class '['")
                if pattern[0] == "]" and nrange > 0:
                    pattern = pattern[1:]
                    break
                lo = pattern[0]
                pattern = pattern[1:]
                hi = lo
                if len(pattern) >= 2 and pattern[0] == "-":
                    hi = pattern[1]
                    pattern = pattern[2:]
                if lo <= name[0] <= hi:
                    matched = True
                nrange += 1
            if matched == negate:
                return False
            name = name[1:]
            continue

        # literal character
        if not name or pattern[0] != name[0]:
            return False
        pattern = pattern[1:]
        name = name[1:]

    return not name


def globs_match(globs: list[str], path: str) -> bool:
    """Match ``path`` against a list of glob patterns with ``!`` negation.

    Rules (match go-lstree's ``GlobsMatch``):

    - A leading ``!`` marks a negated pattern
    - If any negated pattern matches, the path is rejected
    - If at least one positive pattern is present, the path must match at
      least one of them
    - If only negated patterns are given, any path not matched by them passes

    This is the spec-level reference implementation. The walker uses
    :func:`compile_globs` for the hot path — a compiled regex is roughly
    10-50x faster for recursive ``**`` patterns.
    """
    has_positive = False
    matched_positive = False

    for g in globs:
        negated = g.startswith("!")
        pat = g[1:] if negated else g
        ok = glob_match(pat, path)
        if negated:
            if ok:
                return False
        else:
            has_positive = True
            if ok:
                matched_positive = True

    if not has_positive:
        return True
    return matched_positive


# ----------------------------------------------------------------------
# Compiled (regex) fast path for the walker hot loop.
#
# The recursive implementation above is fine for correctness and test
# vectors, but ``**/*.ext`` on a large tree (60k files) spends most of
# its time in Python-level backtracking. Compiling each glob to a regex
# once and reusing it amortises that cost.
# ----------------------------------------------------------------------


def compile_globs(globs: list[str]) -> _CompiledGlobs:
    """Compile a glob list into a reusable matcher.

    Call once per walker invocation (or cache by tuple(globs)) and then
    invoke :meth:`_CompiledGlobs.match` for each path.
    """
    positives: list[re.Pattern[str]] = []
    negatives: list[re.Pattern[str]] = []
    for g in globs:
        if g.startswith("!"):
            negatives.append(_compile_one(g[1:]))
        else:
            positives.append(_compile_one(g))
    return _CompiledGlobs(positives=tuple(positives), negatives=tuple(negatives))


class _CompiledGlobs:
    __slots__ = ("positives", "negatives")

    def __init__(
        self,
        positives: tuple[re.Pattern[str], ...],
        negatives: tuple[re.Pattern[str], ...],
    ) -> None:
        self.positives = positives
        self.negatives = negatives

    def match(self, path: str) -> bool:
        for neg in self.negatives:
            if neg.fullmatch(path) is not None:
                return False
        if not self.positives:
            return True
        for pos in self.positives:
            if pos.fullmatch(path) is not None:
                return True
        return False


@lru_cache(maxsize=256)
def _compile_one(pattern: str) -> re.Pattern[str]:
    """Translate a single glob pattern to a regex matching the whole path.

    Semantics match :func:`glob_match` for ``*``, ``**``, ``?``, ``[...]``.
    Cached by pattern string since typical workloads reuse a small number
    of globs across many files.
    """
    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # ** — can cross /
                if i + 2 < n and pattern[i + 2] == "/":
                    # **/ — zero or more path segments
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
            end = pattern.find("]", i + 1)
            if end == -1:
                raise GlobError("unclosed character class '['")
            body = pattern[i + 1 : end]
            if body.startswith("!") or body.startswith("^"):
                body = "^" + body[1:]
            parts.append("[" + body + "]")
            i = end + 1
            continue

        parts.append(re.escape(c))
        i += 1

    return re.compile("".join(parts))
