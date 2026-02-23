"""Tests for cli/__init__.py."""

from __future__ import annotations

from . import CLI


def test_is_target_mode_skips_vars_value() -> None:
    cli = CLI(["--vars", "build.optimize=true", "build"])
    assert cli._is_target_mode() is True


def test_is_target_mode_skips_vars_file_value() -> None:
    cli = CLI(["--vars-file", "prod.toml", "list"])
    assert cli._is_target_mode() is False
