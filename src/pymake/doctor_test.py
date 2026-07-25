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
