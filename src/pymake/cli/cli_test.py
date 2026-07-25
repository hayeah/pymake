"""Tests for cli/__init__.py."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from . import CLI


@pytest.fixture(autouse=True)
def restore_cwd() -> Iterator[None]:
    """The CLI chdirs for -C; put the process back where it was."""
    cwd = Path.cwd()
    try:
        yield
    finally:
        os.chdir(cwd)


def test_is_target_mode_skips_vars_value() -> None:
    cli = CLI(["--vars", "build.optimize=true", "build"])
    assert cli._is_target_mode() is True


def test_is_target_mode_skips_vars_file_value() -> None:
    cli = CLI(["--vars-file", "prod.toml", "list"])
    assert cli._is_target_mode() is False


MAKEFILE = '''
from pathlib import Path

from pymake import task

OUT = Path("out")


class Common:
    def build_assets(self):
        """Shared assets."""
        OUT.mkdir(exist_ok=True)
        (OUT / "assets.txt").write_text("assets")


class Platform:
    def __init__(self, label):
        self.label = label

    def build_app(self, type: str = "release"):
        """Build the app."""
        OUT.mkdir(exist_ok=True)
        (OUT / f"{self.label}.txt").write_text(type)


common = Common()
task(common.build_assets, outputs=[OUT / "assets.txt"])

windows = Platform("windows")
task(windows.build_app,
     inputs=["Common.build_assets"],
     outputs=[OUT / "windows.txt"],
     name="Windows.build_app")

macos = Platform("macos")
task.group(namespace="macos", sep="_").task(
    macos.build_app,
    inputs=[common.build_assets],
    outputs=[OUT / "macos.txt"],
)

task.default(common.build_assets)
'''


def _write_makefile(tmp_path: Path) -> None:
    (tmp_path / "Makefile.py").write_text(MAKEFILE)


def test_group_tasks_run_from_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_makefile(tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        CLI(["-C", str(tmp_path), "Windows.build_app", "macos_build_app"]).run()

    assert exit_info.value.code == 0
    assert (tmp_path / "out" / "assets.txt").read_text() == "assets"
    assert (tmp_path / "out" / "windows.txt").read_text() == "release"
    assert (tmp_path / "out" / "macos.txt").read_text() == "release"


def test_naked_var_fans_out_to_named_targets(tmp_path: Path) -> None:
    _write_makefile(tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        CLI(
            [
                "-C",
                str(tmp_path),
                "--vars",
                "type=dev",
                "Windows.build_app",
                "macos_build_app",
            ]
        ).run()

    assert exit_info.value.code == 0
    assert (tmp_path / "out" / "windows.txt").read_text() == "dev"
    assert (tmp_path / "out" / "macos.txt").read_text() == "dev"


def test_qualified_var_targets_one_task(tmp_path: Path) -> None:
    _write_makefile(tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        CLI(
            [
                "-C",
                str(tmp_path),
                "--vars",
                "Windows.build_app.type=dev",
                "Windows.build_app",
                "macos_build_app",
            ]
        ).run()

    assert exit_info.value.code == 0
    assert (tmp_path / "out" / "windows.txt").read_text() == "dev"
    assert (tmp_path / "out" / "macos.txt").read_text() == "release"


def test_naked_var_no_target_declares_it_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_makefile(tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        CLI(["-C", str(tmp_path), "--vars", "stamp=1", "Windows.build_app"]).run()

    assert exit_info.value.code == 1
    assert "no target declares var 'stamp'" in capsys.readouterr().err


def test_list_shows_namespaced_tasks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_makefile(tmp_path)

    with pytest.raises(SystemExit):
        CLI(["-C", str(tmp_path), "list"]).run()

    out = capsys.readouterr().out
    assert "Common.build_assets (default) - Shared assets." in out
    assert "Windows.build_app - Build the app." in out
    assert "macos_build_app - Build the app." in out
