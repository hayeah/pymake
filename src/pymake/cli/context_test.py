"""Tests for CommandContext."""

import argparse

import pytest

from ..task import TaskRegistry
from .context import CommandContext


class TestCommandContext:
    """Test CommandContext shared utilities."""

    def test_find_target_found(self) -> None:
        """Test find_target returns task when found."""
        registry = TaskRegistry()
        registry.register(lambda: None, name="test_task")

        args = argparse.Namespace()
        ctx = CommandContext(registry, args)

        task = ctx.find_target("test_task")
        assert task.name == "test_task"

    def test_find_target_not_found_raises(self) -> None:
        """Test find_target raises ValueError when not found."""
        registry = TaskRegistry()
        args = argparse.Namespace()
        ctx = CommandContext(registry, args)

        with pytest.raises(ValueError, match="Unknown target: nonexistent"):
            ctx.find_target("nonexistent")

    def test_resolver_cached(self) -> None:
        """Test resolver is lazily created and cached."""
        registry = TaskRegistry()
        args = argparse.Namespace()
        ctx = CommandContext(registry, args)

        resolver1 = ctx.resolver
        resolver2 = ctx.resolver
        assert resolver1 is resolver2

    def test_parallel_with_jobs(self) -> None:
        """Test parallel is True when jobs is set."""
        registry = TaskRegistry()
        args = argparse.Namespace(parallel=False, jobs=4)
        ctx = CommandContext(registry, args)

        assert ctx.parallel is True

    def test_parallel_with_flag(self) -> None:
        """Test parallel is True when parallel flag is set."""
        registry = TaskRegistry()
        args = argparse.Namespace(parallel=True, jobs=None)
        ctx = CommandContext(registry, args)

        assert ctx.parallel is True

    def test_parallel_false(self) -> None:
        """Test parallel is False when neither jobs nor flag is set."""
        registry = TaskRegistry()
        args = argparse.Namespace(parallel=False, jobs=None)
        ctx = CommandContext(registry, args)

        assert ctx.parallel is False

    def test_verbose_true(self) -> None:
        """Test verbose is True when quiet is False."""
        registry = TaskRegistry()
        args = argparse.Namespace(quiet=False)
        ctx = CommandContext(registry, args)

        assert ctx.verbose is True

    def test_verbose_false(self) -> None:
        """Test verbose is False when quiet is True."""
        registry = TaskRegistry()
        args = argparse.Namespace(quiet=True)
        ctx = CommandContext(registry, args)

        assert ctx.verbose is False

    def test_check_before_run_no_issues(self) -> None:
        """Test check_before_run does not exit when no issues."""
        registry = TaskRegistry()

        def noop() -> None:
            pass

        task = registry.register(noop, name="test_task")

        args = argparse.Namespace()
        ctx = CommandContext(registry, args)

        # Should not raise or exit
        ctx.check_before_run(task)

    def test_vars_resolver_cached(self) -> None:
        """Test vars_resolver is lazily created and cached."""
        registry = TaskRegistry()
        args = argparse.Namespace(vars_file=None, vars=["build.port=2"])
        ctx = CommandContext(registry, args)

        resolver1 = ctx.vars_resolver
        resolver2 = ctx.vars_resolver

        assert resolver1 is resolver2
        assert resolver1.vars_overrides == ["build.port=2"]
