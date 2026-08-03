"""Tests for staleness.py — fingerprints, snapshots, and record diffs."""

from pathlib import Path

from pymake.staleness import (
    Change,
    FingerprintCache,
    Snapshot,
    change_reason,
    diff_record,
    outputs_fingerprint,
    path_fingerprint,
    take_snapshot,
)
from pymake.state import TaskState
from pymake.task import TaskRegistry


class TestPathFingerprint:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert path_fingerprint(tmp_path / "nope") == "missing"

    def test_mtime_and_size(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("hello")
        st = f.stat()
        assert path_fingerprint(f) == f"{st.st_mtime_ns}:{st.st_size}"

    def test_content_size_change_changes_fingerprint(self, tmp_path: Path) -> None:
        f = tmp_path / "a.txt"
        f.write_text("one")
        before = path_fingerprint(f)
        f.write_text("longer content")
        assert path_fingerprint(f) != before


class TestOutputsFingerprint:
    def test_no_outputs_is_a_constant(self) -> None:
        registry = TaskRegistry()
        t = registry.register(lambda: None, name="phony")
        assert outputs_fingerprint(t) == ""

    def test_covers_every_declared_output(self, tmp_path: Path) -> None:
        registry = TaskRegistry()
        a, b = tmp_path / "a.bin", tmp_path / "b.bin"
        a.write_text("a")
        b.write_text("b")
        t = registry.register(lambda: None, name="build", outputs=[a, b])
        before = outputs_fingerprint(t)
        b.write_text("changed content")
        assert outputs_fingerprint(t) != before


class TestFingerprintCache:
    class _Counting:
        def __init__(self) -> None:
            self.id = "counted"
            self.calls = 0

        def fingerprint(self) -> str:
            self.calls += 1
            return f"fp{self.calls}"

    def test_memoizes_by_id_for_the_run(self) -> None:
        obj = self._Counting()
        cache = FingerprintCache()
        assert cache.fingerprint(obj) == "fp1"
        assert cache.fingerprint(obj) == "fp1"
        assert obj.calls == 1

    def test_fresh_bypasses_and_refreshes_the_memo(self) -> None:
        obj = self._Counting()
        cache = FingerprintCache()
        assert cache.fingerprint(obj) == "fp1"
        assert cache.fingerprint(obj, fresh=True) == "fp2"
        # The fresh value replaced the memo.
        assert cache.fingerprint(obj) == "fp2"
        assert obj.calls == 2


class TestTakeSnapshot:
    def test_kind_partitioned(self, tmp_path: Path) -> None:
        from pymake import value

        registry = TaskRegistry()
        out = tmp_path / "tool.exe"
        out.write_text("bin")
        registry.register(lambda: None, name="build_native", outputs=[out])

        icon = tmp_path / "icon.png"
        icon.write_text("png")
        cfg = value("build-config", "opt=1")
        t = registry.register(
            lambda: None, name="package", inputs=[cfg, icon, "build_native"]
        )
        registry.finalize()

        snap = take_snapshot(t, registry, FingerprintCache())
        assert set(snap.paths) == {icon.as_posix()}
        assert set(snap.deps) == {"build_native"}
        assert set(snap.inputs) == {"build-config"}
        assert snap.deps["build_native"] == f"{out.as_posix()}={path_fingerprint(out)}"


class TestDiffRecord:
    def test_no_changes(self) -> None:
        record = TaskState(paths={"a": "1"}, deps={"d": "2"}, inputs={"i": "3"})
        snap = Snapshot(paths={"a": "1"}, deps={"d": "2"}, inputs={"i": "3"})
        assert diff_record(record, snap) == []

    def test_changed_added_removed(self) -> None:
        record = TaskState(paths={"a": "1", "gone": "9"}, inputs={"i": "3"})
        snap = Snapshot(paths={"a": "2", "new": "5"}, inputs={"i": "3"})
        changes = diff_record(record, snap)
        assert Change("paths", "a", "changed") in changes
        assert Change("paths", "new", "added") in changes
        assert Change("paths", "gone", "removed") in changes
        assert len(changes) == 3

    def test_reason_wording(self) -> None:
        assert Change("inputs", "native-sources", "changed").describe() == (
            "input native-sources changed"
        )
        assert Change("paths", "assets/icon.png", "changed").describe() == (
            "path assets/icon.png changed"
        )
        assert Change("deps", "build_native", "changed").describe() == (
            "dep build_native outputs changed"
        )

    def test_change_reason_counts_the_rest(self) -> None:
        changes = [
            Change("inputs", "a", "changed"),
            Change("paths", "b", "changed"),
            Change("deps", "c", "changed"),
        ]
        assert change_reason(changes) == "input a changed, +2 more"
        assert change_reason(changes[:1]) == "input a changed"
