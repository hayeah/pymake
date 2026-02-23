"""Tests for list command."""

from __future__ import annotations

import argparse

import pytest

from ..task import TaskRegistry
from .context import CommandContext
from .list_cmd import ListCommand


def test_list_command_prints_task_vars(capsys: pytest.CaptureFixture[str]) -> None:
    registry = TaskRegistry()

    def build(optimize: bool = False, target: str = "x86_64") -> None:
        """Compile."""

    def deploy(env: str | None = None, port: int = 8080) -> None:
        """Deploy."""

    registry.register(build)
    registry.register(deploy)

    args = argparse.Namespace(all_tasks=False)
    ctx = CommandContext(registry, args)
    ListCommand(ctx).execute()

    out = capsys.readouterr().out
    assert 'vars: optimize (bool=false), target (str="x86_64")' in out
    assert "vars: env (str?), port (int=8080)" in out
