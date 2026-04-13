"""Tests for ``pymake.digest``."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from pymake import tree_digest
from pymake.digest import TreeDigest

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _write(p: Path, content: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _bump_mtime(p: Path, delta_seconds: float = 2.0) -> None:
    """Advance a file's mtime deterministically.

    mtime resolution varies by filesystem (HFS+ / APFS / ext4) so we push
    far enough to survive any reasonable bucketing.
    """
    st = p.stat()
    new_time = st.st_mtime + delta_seconds
    os.utime(p, (new_time, new_time))


# ----------------------------------------------------------------------
# Basic digest behaviour
# ----------------------------------------------------------------------


class TestTreeDigestBasics:
    def test_deterministic_across_calls(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt", "alpha")
        _write(tmp_path / "sub" / "b.txt", "bravo")

        d1 = TreeDigest(tmp_path, digest=tmp_path / ".state")
        d2 = TreeDigest(tmp_path, digest=tmp_path / ".state")
        assert d1._compute() == d2._compute()

    def test_mtime_change_flips_digest(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "a.txt", "alpha")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        before = d._compute()

        _bump_mtime(f)
        d.reset()
        after = d._compute()

        assert before != after

    def test_size_change_flips_digest(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "a.txt", "alpha")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        before = d._compute()

        # Rewrite with different content at the same mtime
        st = f.stat()
        f.write_text("alphabravo")  # size changes
        os.utime(f, (st.st_atime, st.st_mtime))  # pin mtime
        d.reset()
        after = d._compute()

        assert before != after

    def test_file_added_flips_digest(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        before = d._compute()

        _write(tmp_path / "b.txt", "new")
        d.reset()
        assert d._compute() != before

    def test_file_removed_flips_digest(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt")
        b = _write(tmp_path / "b.txt", "doomed")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        before = d._compute()

        b.unlink()
        d.reset()
        assert d._compute() != before

    def test_empty_paths_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            TreeDigest(digest=tmp_path / ".state")

    def test_missing_path_contributes_sentinel(self, tmp_path: Path) -> None:
        """A missing path should not crash; the digest reflects its absence."""
        missing = tmp_path / "nope"
        d = TreeDigest(missing, digest=tmp_path / ".state")
        # Should compute without raising.
        before = d._compute()

        # Once the path appears, digest must change.
        _write(missing / "x.txt")
        d.reset()
        assert d._compute() != before


# ----------------------------------------------------------------------
# Include / exclude plumbing
# ----------------------------------------------------------------------


class TestFilters:
    def test_exclude_is_honored(self, tmp_path: Path) -> None:
        _write(tmp_path / "keep.txt", "keep")
        _write(tmp_path / "drop" / "x.txt", "drop")

        included = TreeDigest(tmp_path, digest=tmp_path / ".state1")
        excluded = TreeDigest(
            tmp_path,
            exclude=["drop"],
            digest=tmp_path / ".state2",
        )
        assert included._compute() != excluded._compute()

        # Changing an excluded file should not change the filtered digest.
        before = excluded._compute()
        _bump_mtime(tmp_path / "drop" / "x.txt")
        excluded.reset()
        assert excluded._compute() == before

    def test_globs_narrow_to_matching_files(self, tmp_path: Path) -> None:
        _write(tmp_path / "keep.py", "py")
        _write(tmp_path / "ignore.md", "md")

        d = TreeDigest(
            tmp_path,
            globs=["**/*.py"],
            digest=tmp_path / ".state",
        )
        before = d._compute()

        # Touching the non-matching file must not affect the digest.
        _bump_mtime(tmp_path / "ignore.md")
        d.reset()
        assert d._compute() == before

        # Touching the matching file must affect the digest.
        _bump_mtime(tmp_path / "keep.py")
        d.reset()
        assert d._compute() != before

    def test_gitignore_aware_by_default(self, tmp_path: Path) -> None:
        """lstree should respect .gitignore (no manual exclude needed)."""
        _write(tmp_path / "src" / "a.py", "real")
        _write(tmp_path / "src" / "build" / "artifact.txt", "noise")
        (tmp_path / "src" / ".gitignore").write_text("build/\n")

        d = TreeDigest(tmp_path / "src", digest=tmp_path / ".state")
        before = d._compute()

        # Bumping an ignored file should not change the digest.
        _bump_mtime(tmp_path / "src" / "build" / "artifact.txt")
        d.reset()
        assert d._compute() == before


# ----------------------------------------------------------------------
# State file round-trip
# ----------------------------------------------------------------------


class TestStateFile:
    def test_changed_true_first_time(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        assert d.changed() is True

    def test_commit_then_changed_false(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        assert d.changed() is True
        d.commit()

        # Fresh instance reads from disk.
        d2 = TreeDigest(tmp_path, digest=tmp_path / ".state")
        assert d2.changed() is False

    def test_changed_flips_after_mutation(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "a.txt")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        d.changed()
        d.commit()

        _bump_mtime(f)
        d2 = TreeDigest(tmp_path, digest=tmp_path / ".state")
        assert d2.changed() is True

    def test_commit_without_prior_changed_call(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        # commit() before changed() should still record a valid snapshot
        d.commit()

        d2 = TreeDigest(tmp_path, digest=tmp_path / ".state")
        assert d2.changed() is False

    def test_digest_kwarg_is_required(self, tmp_path: Path) -> None:
        """``digest=`` has no default — omitting it is a TypeError."""
        _write(tmp_path / "a.txt")
        with pytest.raises(TypeError):
            TreeDigest(tmp_path)  # type: ignore[call-arg]

    def test_digest_path_is_stored(self, tmp_path: Path) -> None:
        """The caller-supplied digest path is stored verbatim."""
        _write(tmp_path / "a.txt")
        explicit = tmp_path / "sub" / "my.digest"
        d = TreeDigest(tmp_path, digest=explicit)
        assert d.digest_path == explicit

    def test_changed_true_after_file_added_post_commit(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        d.commit()

        _write(tmp_path / "b.txt", "new")

        d2 = TreeDigest(tmp_path, digest=tmp_path / ".state")
        assert d2.changed() is True

    def test_changed_true_after_file_removed_post_commit(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt")
        doomed = _write(tmp_path / "b.txt", "gone")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        d.commit()

        doomed.unlink()

        d2 = TreeDigest(tmp_path, digest=tmp_path / ".state")
        assert d2.changed() is True

    def test_unchanged_tree_recomputes_aggregate_hash(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        d.commit()

        d2 = TreeDigest(tmp_path, digest=tmp_path / ".state")
        calls = 0
        original = TreeDigest._aggregate_digest

        def record_call(self: TreeDigest, entries: list[object]) -> str:
            nonlocal calls
            calls += 1
            return original(self, entries)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(TreeDigest, "_aggregate_digest", record_call)
        try:
            assert d2.changed() is False
            assert calls == 1
        finally:
            monkeypatch.undo()


# ----------------------------------------------------------------------
# Multi-root, mixed inputs, and the factory
# ----------------------------------------------------------------------


class TestMultiRoot:
    def test_mixed_file_and_dir_roots(self, tmp_path: Path) -> None:
        f = _write(tmp_path / "file.txt", "solo")
        _write(tmp_path / "dir" / "a.py")

        d = TreeDigest(f, tmp_path / "dir", digest=tmp_path / ".state")
        before = d._compute()

        _bump_mtime(f)
        d.reset()
        assert d._compute() != before

    def test_multiple_dir_roots_cross_collision_guarded(self, tmp_path: Path) -> None:
        """Two dirs containing identically-named files must not collide."""
        _write(tmp_path / "x" / "same.py", "one")
        _write(tmp_path / "y" / "same.py", "two")

        combined = TreeDigest(
            tmp_path / "x",
            tmp_path / "y",
            digest=tmp_path / ".state",
        )
        only_x = TreeDigest(tmp_path / "x", digest=tmp_path / ".other")
        assert combined._compute() != only_x._compute()

    def test_factory_returns_instance(self, tmp_path: Path) -> None:
        d = tree_digest(tmp_path, digest=tmp_path / ".state")
        assert isinstance(d, TreeDigest)


# ----------------------------------------------------------------------
# Hash backend fallback
# ----------------------------------------------------------------------


class TestHashFallback:
    def test_blake2b_fallback_when_xxhash_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Force the xxhash import to fail and confirm digest still works."""
        # Evict any real xxhash import from the cache.
        monkeypatch.setitem(sys.modules, "xxhash", None)

        _write(tmp_path / "a.txt")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        digest = d._compute()
        assert digest.startswith("blake2b64:")


# ----------------------------------------------------------------------
# Digest file behaviour
# ----------------------------------------------------------------------


class TestDigestStorage:
    def test_commit_writes_digest_file_only(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt", "alpha")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        d.commit()

        digest_text = (tmp_path / ".state").read_text().strip()
        assert digest_text == d._compute()

    def test_changed_detects_digest_file_tampering(self, tmp_path: Path) -> None:
        _write(tmp_path / "a.txt", "alpha")
        d = TreeDigest(tmp_path, digest=tmp_path / ".state")
        d.commit()

        (tmp_path / ".state").write_text("xxhash64:not-the-real-digest\n")

        d2 = TreeDigest(tmp_path, digest=tmp_path / ".state")
        assert d2.changed() is True
