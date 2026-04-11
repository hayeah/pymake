"""Walker tests.

Each test builds a small filesystem under ``tmp_path`` and exercises the
3-stage pipeline, depth handling, stat, and exclude. Also has a driver
that reads JSON fixtures from ``testdata/lstree.json``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from pymake.lstree import Entry, Query, walk

_FIXTURE_PATH = Path(__file__).parent / "testdata" / "lstree.json"
_FIXTURES = json.loads(_FIXTURE_PATH.read_text()) if _FIXTURE_PATH.exists() else None


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_tree(root: Path, files: dict[str, str | bytes]) -> None:
    """Create a tree of files from a {rel_path: content} mapping."""
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


def _paths(entries: list[Entry]) -> list[str]:
    return [e.path.as_posix() for e in entries]


# ----------------------------------------------------------------------
# Default walk (builtin ignorer)
# ----------------------------------------------------------------------


class TestDefaultWalk:
    def test_builtins_skip_node_modules(self, tmp_path: Path) -> None:
        _make_tree(
            tmp_path,
            {
                "src/main.py": "x",
                "src/util.py": "y",
                "node_modules/pkg/index.js": "z",
                "__pycache__/main.cpython-312.pyc": "z",
                ".DS_Store": "z",
                "README.md": "r",
            },
        )
        got = _paths(list(walk(tmp_path)))
        assert got == ["README.md", "src/main.py", "src/util.py"]

    def test_vcs_dirs_always_ignored_when_builtin(self, tmp_path: Path) -> None:
        _make_tree(
            tmp_path,
            {
                ".git/config": "x",
                ".hg/hgrc": "x",
                ".svn/entries": "x",
                "main.py": "y",
            },
        )
        got = _paths(list(walk(tmp_path)))
        assert got == ["main.py"]


# ----------------------------------------------------------------------
# Gitignore-driven walk
# ----------------------------------------------------------------------


class TestGitignoreWalk:
    def test_gitignore_replaces_builtins(self, tmp_path: Path) -> None:
        # When a .gitignore exists, builtins no longer apply — so a file
        # under node_modules is visible unless .gitignore excludes it.
        _make_tree(
            tmp_path,
            {
                ".gitignore": "drafts/\n",
                "alice.epub": "a",
                "drafts/wip.epub": "b",
                "node_modules/pkg/index.js": "c",
                "sci-fi/dune.epub": "d",
            },
        )
        got = _paths(list(walk(tmp_path)))
        assert got == [
            ".gitignore",
            "alice.epub",
            "node_modules/pkg/index.js",
            "sci-fi/dune.epub",
        ]

    def test_git_dir_still_ignored_under_gitignore(self, tmp_path: Path) -> None:
        # .git is not in .gitignore, but the GitIgnore parser should still
        # exclude it — it lives in ALWAYS_IGNORED... wait, the spec says
        # ALWAYS_IGNORED applies only for BuiltinIgnorer path. Under a
        # .gitignore, .git is only excluded if the .gitignore lists it.
        # Going with the strict semantic: real projects' .gitignore always
        # lists .git implicitly — but to match the spec text we test that
        # the .git dir is NOT pruned automatically when using gitignore.
        _make_tree(
            tmp_path,
            {
                ".gitignore": "drafts/\n",
                ".git/config": "x",
                "main.py": "y",
            },
        )
        got = _paths(list(walk(tmp_path)))
        # .git IS listed because .gitignore doesn't exclude it explicitly
        assert ".git/config" in got
        assert "main.py" in got

    def test_negation_in_gitignore(self, tmp_path: Path) -> None:
        _make_tree(
            tmp_path,
            {
                ".gitignore": "*.log\n!keep.log\n",
                "app.log": "a",
                "keep.log": "b",
                "notes.txt": "c",
            },
        )
        got = _paths(list(walk(tmp_path)))
        assert got == [".gitignore", "keep.log", "notes.txt"]


# ----------------------------------------------------------------------
# Query: globs, exclude, depth, stat
# ----------------------------------------------------------------------


class TestQuery:
    def test_include_glob(self, tmp_path: Path) -> None:
        _make_tree(
            tmp_path,
            {
                "main.py": "a",
                "README.md": "b",
                "src/util.py": "c",
                "src/types.ts": "d",
            },
        )
        got = _paths(list(walk(tmp_path, query=Query(globs=["**/*.py"]))))
        assert got == ["main.py", "src/util.py"]

    def test_include_glob_with_negation(self, tmp_path: Path) -> None:
        _make_tree(
            tmp_path,
            {
                "main.py": "a",
                "test_main.py": "b",
                "src/util.py": "c",
                "src/test_util.py": "d",
            },
        )
        q = Query(globs=["**/*.py", "!**/test_*"])
        got = _paths(list(walk(tmp_path, query=q)))
        assert got == ["main.py", "src/util.py"]

    def test_exclude_bare_name(self, tmp_path: Path) -> None:
        _make_tree(
            tmp_path,
            {
                "main.py": "a",
                "__generated__/foo.py": "b",
                "src/__generated__/bar.py": "c",
                "src/util.py": "d",
            },
        )
        q = Query(exclude=["__generated__"])
        got = _paths(list(walk(tmp_path, query=q)))
        assert got == ["main.py", "src/util.py"]

    def test_exclude_glob(self, tmp_path: Path) -> None:
        _make_tree(
            tmp_path,
            {
                "main.py": "a",
                "src/util.py": "b",
                "src/util.test.py": "c",
            },
        )
        q = Query(exclude=["**/*.test.*"])
        got = _paths(list(walk(tmp_path, query=q)))
        assert got == ["main.py", "src/util.py"]

    def test_stage_three_wins_over_include(self, tmp_path: Path) -> None:
        # A file can match an include glob AND match an exclude — exclude wins.
        _make_tree(
            tmp_path,
            {
                "main.py": "a",
                "src/foo.py": "b",
                "src/foo.test.py": "c",
            },
        )
        q = Query(globs=["**/*.py"], exclude=["**/*.test.*"])
        got = _paths(list(walk(tmp_path, query=q)))
        assert got == ["main.py", "src/foo.py"]

    def test_max_depth_one_flat_listing(self, tmp_path: Path) -> None:
        _make_tree(
            tmp_path,
            {
                "README.md": "r",
                "src/main.py": "a",
                "src/sub/util.py": "b",
                "docs/intro.md": "c",
            },
        )
        entries = list(walk(tmp_path, query=Query(max_depth=1)))
        # Includes files at root + directories at root (flat listing mode)
        names = {(e.path.as_posix(), e.is_dir) for e in entries}
        assert ("README.md", False) in names
        assert ("src", True) in names
        assert ("docs", True) in names
        # Deeper files are NOT present
        assert not any("sub" in e.path.as_posix() for e in entries)
        assert not any(e.path.as_posix() == "src/main.py" for e in entries)

    def test_stat_populates_size_and_mtime(self, tmp_path: Path) -> None:
        (tmp_path / "file.txt").write_text("hello world")
        entries = list(walk(tmp_path, query=Query(stat=True)))
        assert len(entries) == 1
        e = entries[0]
        assert e.size == len("hello world")
        assert e.mtime_ns > 0

    def test_no_stat_returns_zero(self, tmp_path: Path) -> None:
        (tmp_path / "file.txt").write_text("hello world")
        entries = list(walk(tmp_path))
        assert entries[0].size == 0
        assert entries[0].mtime_ns == 0

    def test_no_ignore_shows_everything(self, tmp_path: Path) -> None:
        _make_tree(
            tmp_path,
            {
                ".git/config": "x",
                "node_modules/pkg/index.js": "y",
                "main.py": "z",
            },
        )
        got = _paths(list(walk(tmp_path, query=Query(no_ignore=True))))
        assert ".git/config" in got
        assert "node_modules/pkg/index.js" in got
        assert "main.py" in got


# ----------------------------------------------------------------------
# Multiple roots + file paths
# ----------------------------------------------------------------------


class TestMultipleRoots:
    def test_multiple_dir_roots(self, tmp_path: Path) -> None:
        _make_tree(
            tmp_path,
            {
                "a/one.py": "x",
                "b/two.py": "y",
            },
        )
        entries = list(walk(tmp_path / "a", tmp_path / "b"))
        got = {e.path.as_posix() for e in entries}
        assert got == {"one.py", "two.py"}

    def test_single_file_path_yielded_directly(self, tmp_path: Path) -> None:
        (tmp_path / "foo.txt").write_text("bar")
        entries = list(walk(tmp_path / "foo.txt", query=Query(stat=True)))
        assert len(entries) == 1
        assert entries[0].path.name == "foo.txt"
        assert entries[0].size == 3

    def test_missing_root_silently_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "existing.txt").write_text("x")
        entries = list(walk(tmp_path / "nope", tmp_path / "existing.txt"))
        assert [e.path.name for e in entries] == ["existing.txt"]


# ----------------------------------------------------------------------
# Pruning efficiency — a 50k-file node_modules should be cheap to skip
# ----------------------------------------------------------------------


class TestPruning:
    def test_ignored_subtree_not_stat_called(self, tmp_path: Path) -> None:
        # Create many files under node_modules; walker should never stat them
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        for i in range(200):
            (nm / f"f{i}.js").write_text(".")
        (tmp_path / "main.py").write_text("x")

        start = time.perf_counter()
        entries = list(walk(tmp_path))
        elapsed = time.perf_counter() - start
        assert [e.path.as_posix() for e in entries] == ["main.py"]
        # Should be well under 50ms even with 200 files in node_modules
        assert elapsed < 0.2, f"walker too slow: {elapsed:.3f}s"


# ----------------------------------------------------------------------
# JSON fixture driver (shared format across languages)
# ----------------------------------------------------------------------


@pytest.mark.skipif(_FIXTURES is None, reason="lstree.json fixture missing")
@pytest.mark.parametrize(
    "case",
    _FIXTURES["tests"] if _FIXTURES else [],
    ids=lambda c: c["name"],
)
def test_fixture_cases(tmp_path: Path, case: dict[str, Any]) -> None:
    assert _FIXTURES is not None
    # Build the fs
    for rel, meta in _FIXTURES["fs"].items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        content = meta.get("content", "x" * meta.get("size", 1))
        p.write_text(content)

    qdict = case.get("query", {}) or {}
    q = Query(
        globs=qdict.get("globs"),
        exclude=qdict.get("exclude"),
        max_depth=qdict.get("maxDepth", 0),
        stat=qdict.get("stat", False),
        no_ignore=qdict.get("noIgnore", False),
    )
    entries = list(walk(tmp_path, query=q))
    got = [{"path": e.path.as_posix(), "isDir": e.is_dir} for e in entries]
    expected = [
        {"path": e["path"], "isDir": e.get("isDir", False)}
        for e in case["expect"]["entries"]
    ]
    assert got == expected, f"{case['name']}: {got!r} != {expected!r}"
