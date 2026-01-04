"""Tests for executor.py."""

import io
import tempfile
from pathlib import Path

import pytest

from pymake import CyclicDependencyError, ExecutionError, Executor, TaskRegistry


class TestExecutor:
    def test_run_single_task(self) -> None:
        registry = TaskRegistry()
        executed = []
        registry.register(lambda: executed.append("a"), name="a")

        executor = Executor(registry, verbose=False)
        executor.run("a")
        assert executed == ["a"]

    def test_run_with_dependencies(self) -> None:
        registry = TaskRegistry()
        executed = []
        registry.register(lambda: executed.append("a"), name="a", outputs=["a.txt"])
        registry.register(lambda: executed.append("b"), name="b", inputs=["a.txt"])

        executor = Executor(registry, verbose=False)
        executor.run("b")
        assert executed == ["a", "b"]

    def test_skip_up_to_date(self) -> None:
        registry = TaskRegistry()
        executed = []

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test")
            output_path = Path(f.name)

        try:
            registry.register(
                lambda: executed.append("a"),
                name="a",
                outputs=[str(output_path)],
            )

            executor = Executor(registry, verbose=False)
            executor.run("a")
            assert executed == []  # Should skip because output exists
        finally:
            output_path.unlink()

    def test_force_rerun(self) -> None:
        registry = TaskRegistry()
        executed = []

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test")
            output_path = Path(f.name)

        try:
            registry.register(
                lambda: executed.append("a"),
                name="a",
                outputs=[str(output_path)],
            )

            executor = Executor(registry, force=True, verbose=False)
            executor.run("a")
            assert executed == ["a"]  # Should run because force=True
        finally:
            output_path.unlink()

    def test_run_if_condition(self) -> None:
        registry = TaskRegistry()
        executed = []

        registry.register(
            lambda: executed.append("a"),
            name="a",
            run_if=lambda: False,
        )

        executor = Executor(registry, verbose=False)
        executor.run("a")
        assert executed == []  # Should skip because run_if returned False

    def test_unknown_target_raises(self) -> None:
        registry = TaskRegistry()
        executor = Executor(registry, verbose=False)

        with pytest.raises(ValueError, match="Unknown target"):
            executor.run("nonexistent")

    def test_cycle_detection(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="a", inputs=["c.txt"], outputs=["a.txt"])
        registry.register(lambda: None, name="b", inputs=["a.txt"], outputs=["b.txt"])
        registry.register(lambda: None, name="c", inputs=["b.txt"], outputs=["c.txt"])

        executor = Executor(registry, verbose=False)
        with pytest.raises(CyclicDependencyError):
            executor.run("a")

    def test_task_error_handling(self) -> None:
        registry = TaskRegistry()

        def failing_task() -> None:
            raise RuntimeError("Task failed!")

        registry.register(failing_task, name="fail")

        executor = Executor(registry, verbose=False)
        with pytest.raises(ExecutionError, match="Task failed"):
            executor.run("fail")

    def test_verbose_output(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="a")

        output = io.StringIO()
        executor = Executor(registry, verbose=True, output=output)
        executor.run("a")

        assert "[run] a" in output.getvalue()

    def test_parallel_execution(self) -> None:
        registry = TaskRegistry()
        executed = []
        registry.register(lambda: executed.append("a"), name="a", outputs=["a.txt"])
        registry.register(lambda: executed.append("b"), name="b", outputs=["b.txt"])
        registry.register(
            lambda: executed.append("c"),
            name="c",
            inputs=["a.txt", "b.txt"],
        )

        executor = Executor(registry, parallel=True, verbose=False)
        executor.run("c")

        # a and b should run before c
        assert "c" in executed
        assert executed.index("a") < executed.index("c")
        assert executed.index("b") < executed.index("c")
