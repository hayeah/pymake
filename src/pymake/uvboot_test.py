"""Tests for uvboot.py."""

from __future__ import annotations

from pathlib import Path

from . import uvboot


def _project(tmp_path: Path, pyproject: str | None) -> Path:
    makefile = tmp_path / "Makefile.py"
    makefile.write_text("")
    if pyproject is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject)
    return makefile


def test_project_dir_requires_tool_pymake(tmp_path: Path) -> None:
    makefile = _project(tmp_path, '[project]\nname = "x"\nversion = "0"\n')
    assert uvboot.project_dir(makefile) is None


def test_project_dir_opt_in(tmp_path: Path) -> None:
    makefile = _project(tmp_path, "[tool.pymake]\n")
    assert uvboot.project_dir(makefile) == tmp_path.resolve()


def test_project_dir_no_pyproject(tmp_path: Path) -> None:
    makefile = _project(tmp_path, None)
    assert uvboot.project_dir(makefile) is None


def test_project_dir_invalid_toml(tmp_path: Path) -> None:
    makefile = _project(tmp_path, "not [ valid toml")
    assert uvboot.project_dir(makefile) is None


def test_uv_command_shape(tmp_path: Path) -> None:
    cmd = uvboot.uv_command(tmp_path, ["build", "-B"], environ={})
    assert cmd is not None
    assert cmd[:4] == ["uv", "run", "--project", str(tmp_path)]
    # pymake itself is injected as an overlay, not required of the project
    assert "--with-editable" in cmd or "--with" in cmd
    assert cmd[-5:] == ["python", "-m", "pymake", "build", "-B"]


def test_uv_command_guard_same_project(tmp_path: Path) -> None:
    env = {uvboot.ENV_GUARD: str(tmp_path)}
    assert uvboot.uv_command(tmp_path, [], environ=env) is None


def test_uv_command_guard_other_project(tmp_path: Path) -> None:
    env = {uvboot.ENV_GUARD: str(tmp_path / "elsewhere")}
    assert uvboot.uv_command(tmp_path, [], environ=env) is not None


def test_uv_command_disabled(tmp_path: Path) -> None:
    env = {uvboot.ENV_DISABLE: "1"}
    assert uvboot.uv_command(tmp_path, [], environ=env) is None


def test_source_root_in_checkout() -> None:
    # These tests run from the source tree, so pymake is importable editable.
    root = uvboot.source_root()
    assert root is not None
    assert (root / "src" / "pymake" / "uvboot.py").is_file()
