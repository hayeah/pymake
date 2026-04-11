"""Glob tests driven by the shared JSON vectors in testdata/lstree_glob.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pymake.lstree.glob import GlobError, glob_match, globs_match

_VECTORS = json.loads(
    (Path(__file__).parent / "testdata" / "lstree_glob.json").read_text()
)


@pytest.mark.parametrize(
    "pattern,path,expected",
    [(t["pattern"], t["path"], t["match"]) for t in _VECTORS["tests"]],
)
def test_glob_match_vectors(pattern: str, path: str, expected: bool) -> None:
    assert glob_match(pattern, path) is expected, (
        f"glob_match({pattern!r}, {path!r}) should be {expected}"
    )


def test_trailing_doublestar() -> None:
    # ** at end of pattern with a prefix matches deeper paths
    assert glob_match("src/**", "src/a/b/c.py")
    assert glob_match("src/**", "src/a")
    assert not glob_match("src/**", "other/a.py")


def test_literal_dot() -> None:
    assert glob_match(".gitignore", ".gitignore")
    assert not glob_match(".gitignore", "gitignore")


def test_unclosed_character_class_raises() -> None:
    with pytest.raises(GlobError):
        glob_match("[abc", "a")


class TestGlobsMatch:
    def test_single_positive(self) -> None:
        assert globs_match(["**/*.py"], "src/foo.py")
        assert not globs_match(["**/*.py"], "src/foo.ts")

    def test_single_negation(self) -> None:
        # only a negation — everything not matched passes
        assert globs_match(["!**/test_*"], "src/foo.py")
        assert not globs_match(["!**/test_*"], "src/test_foo.py")

    def test_positive_and_negation(self) -> None:
        globs = ["**/*.py", "!**/test_*"]
        assert globs_match(globs, "src/foo.py")
        assert not globs_match(globs, "src/test_foo.py")
        assert not globs_match(globs, "src/foo.ts")

    def test_multiple_positives_match_any(self) -> None:
        globs = ["**/*.py", "**/*.ts"]
        assert globs_match(globs, "src/foo.py")
        assert globs_match(globs, "src/foo.ts")
        assert not globs_match(globs, "src/foo.md")

    def test_empty_globs_passes_all(self) -> None:
        assert globs_match([], "anything/at/all.txt")
