"""Tests for doctor.py."""

from __future__ import annotations

from pathlib import Path

from .doctor import Doctor
from .task import TaskRegistry


def test_reports_unresolved_dotted_string_input() -> None:
    registry = TaskRegistry()
    registry.register(lambda: None, name="build", inputs=["Common.build_assets"])
    registry.finalize()

    issues = Doctor(registry).check_all()

    assert len(issues) == 1
    assert issues[0].task == "build"
    assert "no task and no file named 'Common.build_assets'" in issues[0].message
    assert "is its group registered?" in issues[0].message


def test_resolved_string_reference_is_not_an_issue() -> None:
    registry = TaskRegistry()
    registry.register(lambda: None, name="Common.build_assets")
    registry.register(lambda: None, name="build", inputs=["Common.build_assets"])
    registry.finalize()

    assert Doctor(registry).check_all() == []


def test_missing_file_keeps_the_file_wording(tmp_path: Path) -> None:
    registry = TaskRegistry()
    registry.register(lambda: None, name="build", inputs=[tmp_path / "missing.c"])
    registry.finalize()

    issues = Doctor(registry).check_all()

    assert len(issues) == 1
    assert "does not exist and no task produces it" in issues[0].message


def test_sweep_aggregates_flip_counters(tmp_path: Path) -> None:
    from .state import StateStore, TaskState

    registry = TaskRegistry()
    registry.register(lambda: None, name="build")

    store = StateStore(tmp_path / "state")
    store.save(
        "build",
        TaskState(
            inputs={"build-config": "fp"},
            flips={"inputs": {"build-config": 4}},
        ),
    )

    issues = Doctor(registry, state_dir=tmp_path / "state").check_all(sweep=True)
    warnings = [i for i in issues if i.severity == "warning"]
    assert len(warnings) == 1
    assert "input build-config has changed on every one of the last 4 runs" in (
        warnings[0].message
    )

    # Below the threshold: silence.
    store.save(
        "build",
        TaskState(inputs={"build-config": "fp"}, flips={"inputs": {"build-config": 2}}),
    )
    issues = Doctor(registry, state_dir=tmp_path / "state").check_all(sweep=True)
    assert [i for i in issues if i.severity == "warning"] == []


def test_sweep_reports_severed_run_if_wrappers(tmp_path: Path) -> None:
    import warnings as warnings_mod

    from .digest import TreeDigest

    src = tmp_path / "src"
    src.mkdir()
    digest = TreeDigest(src, digest=tmp_path / ".digest")

    def should_build() -> bool:
        return digest.changed() or False

    registry = TaskRegistry()
    with warnings_mod.catch_warnings():
        warnings_mod.simplefilter("ignore")  # registration warns; doctor is the sweep
        registry.register(lambda: None, name="build", run_if=should_build)

    issues = Doctor(registry, state_dir=tmp_path / "state").check_all(sweep=True)
    severed = [i for i in issues if "exposes no .commit" in i.message]
    assert len(severed) == 1
    assert severed[0].severity == "warning"
    assert severed[0].task == "build"


def test_default_check_all_skips_the_sweep(tmp_path: Path) -> None:
    """check_before_run's path must not repeat inline warnings every run."""
    from .state import StateStore, TaskState

    registry = TaskRegistry()
    registry.register(lambda: None, name="build")
    store = StateStore(tmp_path / "state")
    store.save("build", TaskState(flips={"inputs": {"cfg": 9}}))

    issues = Doctor(registry, state_dir=tmp_path / "state").check_all()
    assert issues == []
