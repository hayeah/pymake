"""Tests for state.py — per-task fingerprint state files."""

import json
from pathlib import Path

from pymake.state import STATE_VERSION, StateStore, TaskState


class TestStateStore:
    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / "state")
        assert store.load("build") is None

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / "state")
        state = TaskState(
            paths={"assets/icon.png": "123:45"},
            deps={"build_native": "abc"},
            inputs={"native-sources": "deadbeef"},
            flips={"inputs": {"native-sources": 2}},
        )
        store.save("package", state)

        loaded = store.load("package")
        assert loaded is not None
        assert loaded.paths == {"assets/icon.png": "123:45"}
        assert loaded.deps == {"build_native": "abc"}
        assert loaded.inputs == {"native-sources": "deadbeef"}
        assert loaded.flip_count("inputs", "native-sources") == 2
        assert loaded.flip_count("inputs", "other") == 0

    def test_records_are_per_task(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / "state")
        store.save("a", TaskState(inputs={"shared": "one"}))
        store.save("b", TaskState(inputs={"shared": "two"}))

        a = store.load("a")
        b = store.load("b")
        assert a is not None and a.inputs["shared"] == "one"
        assert b is not None and b.inputs["shared"] == "two"

    def test_write_is_atomic_no_temp_leftovers(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / "state")
        for _ in range(3):
            store.save("build", TaskState(paths={"a": "1"}))
        files = sorted(p.name for p in (tmp_path / "state").iterdir())
        assert files == [store.path_for("build").name]

    def test_corrupt_file_reads_as_no_record(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / "state")
        store.save("build", TaskState())
        store.path_for("build").write_text("{not json")
        assert store.load("build") is None

    def test_version_mismatch_reads_as_no_record(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / "state")
        store.save("build", TaskState(paths={"a": "1"}))
        path = store.path_for("build")
        data = json.loads(path.read_text())
        data["version"] = STATE_VERSION + 1
        path.write_text(json.dumps(data))
        assert store.load("build") is None

    def test_task_names_with_hostile_chars(self, tmp_path: Path) -> None:
        store = StateStore(tmp_path / "state")
        names = ["Windows.build_app", "cc:foo.c", "a/b", "a b"]
        for i, name in enumerate(names):
            store.save(name, TaskState(paths={"n": str(i)}))
        for i, name in enumerate(names):
            loaded = store.load(name)
            assert loaded is not None
            assert loaded.paths == {"n": str(i)}
        # All files landed inside the state root (no path traversal).
        assert len(list((tmp_path / "state").iterdir())) == len(names)
