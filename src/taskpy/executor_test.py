"""Tests for executor.py."""

import io
import tempfile
from pathlib import Path

import pytest

from taskpy import (
    CyclicDependencyError,
    ExecutionError,
    Executor,
    MissingOutputError,
    TaskRegistry,
    UnproducibleInputError,
)


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

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "a.txt"

            def task_a() -> None:
                executed.append("a")
                output_file.write_text("output")

            registry.register(task_a, name="a", outputs=[str(output_file)])
            registry.register(
                lambda: executed.append("b"), name="b", inputs=[str(output_file)]
            )

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

    def test_run_if_not_condition(self) -> None:
        registry = TaskRegistry()
        executed = []

        registry.register(
            lambda: executed.append("a"),
            name="a",
            run_if_not=lambda: True,
        )

        executor = Executor(registry, verbose=False)
        executor.run("a")
        assert executed == []  # Should skip because run_if_not returned True

    def test_run_if_not_runs_when_false(self) -> None:
        registry = TaskRegistry()
        executed = []

        registry.register(
            lambda: executed.append("a"),
            name="a",
            run_if_not=lambda: False,
        )

        executor = Executor(registry, verbose=False)
        executor.run("a")
        assert executed == ["a"]  # Should run because run_if_not returned False

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

        with tempfile.TemporaryDirectory() as tmpdir:
            a_file = Path(tmpdir) / "a.txt"
            b_file = Path(tmpdir) / "b.txt"

            def task_a() -> None:
                executed.append("a")
                a_file.write_text("a")

            def task_b() -> None:
                executed.append("b")
                b_file.write_text("b")

            registry.register(task_a, name="a", outputs=[str(a_file)])
            registry.register(task_b, name="b", outputs=[str(b_file)])
            registry.register(
                lambda: executed.append("c"),
                name="c",
                inputs=[str(a_file), str(b_file)],
            )

            executor = Executor(registry, parallel=True, verbose=False)
            executor.run("c")

            # a and b should run before c
            assert "c" in executed
            assert executed.index("a") < executed.index("c")
            assert executed.index("b") < executed.index("c")

    def test_touch_creates_file(self) -> None:
        registry = TaskRegistry()
        executed = []

        with tempfile.TemporaryDirectory() as tmpdir:
            touch_file = Path(tmpdir) / "build" / ".task-done"

            registry.register(
                lambda: executed.append("a"),
                name="a",
                touch=str(touch_file),
            )

            assert not touch_file.exists()

            executor = Executor(registry, verbose=False)
            executor.run("a")

            assert executed == ["a"]
            assert touch_file.exists()

            # Second run should skip (touch file exists, no inputs)
            executed.clear()
            executor.run("a")
            assert executed == []

    def test_touch_with_inputs(self) -> None:
        registry = TaskRegistry()
        executed = []

        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = Path(tmpdir) / "input.txt"
            touch_file = Path(tmpdir) / ".task-done"

            input_file.write_text("test")

            registry.register(
                lambda: executed.append("a"),
                name="a",
                inputs=[str(input_file)],
                touch=str(touch_file),
            )

            executor = Executor(registry, verbose=False)
            executor.run("a")
            assert executed == ["a"]
            assert touch_file.exists()

            # Second run should skip
            executed.clear()
            executor.run("a")
            assert executed == []

            # Update input file - should run again
            import time

            time.sleep(0.01)
            input_file.write_text("updated")

            executed.clear()
            executor.run("a")
            assert executed == ["a"]

    def test_unproducible_input_error(self) -> None:
        """Error when input doesn't exist and no task produces it."""
        registry = TaskRegistry()

        with tempfile.TemporaryDirectory() as tmpdir:
            missing_input = Path(tmpdir) / "nonexistent.txt"

            registry.register(
                lambda: None,
                name="a",
                inputs=[str(missing_input)],
            )

            executor = Executor(registry, verbose=False)
            with pytest.raises(UnproducibleInputError, match="nonexistent.txt"):
                executor.run("a")

    def test_missing_input_error(self) -> None:
        """Error when input doesn't exist at execution time."""
        registry = TaskRegistry()

        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = Path(tmpdir) / "input.txt"

            # Task a declares output but doesn't create it
            def task_a() -> None:
                pass  # Doesn't create input_file

            registry.register(task_a, name="a", outputs=[str(input_file)])
            registry.register(lambda: None, name="b", inputs=[str(input_file)])

            executor = Executor(registry, verbose=False)
            # Task a runs but doesn't create input_file, then task b fails
            with pytest.raises(MissingOutputError, match="input.txt"):
                executor.run("b")

    def test_missing_output_error(self) -> None:
        """Error when task doesn't create declared output."""
        registry = TaskRegistry()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.txt"

            def task_a() -> None:
                pass  # Doesn't create output_file

            registry.register(task_a, name="a", outputs=[str(output_file)])

            executor = Executor(registry, verbose=False)
            with pytest.raises(MissingOutputError, match="output.txt"):
                executor.run("a")

    def test_output_validation_excludes_touch(self) -> None:
        """Touch file is not validated as output (executor creates it)."""
        registry = TaskRegistry()
        executed = []

        with tempfile.TemporaryDirectory() as tmpdir:
            touch_file = Path(tmpdir) / ".done"

            registry.register(
                lambda: executed.append("a"),
                name="a",
                touch=str(touch_file),
            )

            executor = Executor(registry, verbose=False)
            # Should not raise - touch file is created by executor
            executor.run("a")
            assert executed == ["a"]
            assert touch_file.exists()

    def test_input_validation_with_existing_file(self) -> None:
        """No error when input file exists."""
        registry = TaskRegistry()
        executed = []

        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = Path(tmpdir) / "input.txt"
            input_file.write_text("data")

            registry.register(
                lambda: executed.append("a"),
                name="a",
                inputs=[str(input_file)],
            )

            executor = Executor(registry, verbose=False)
            executor.run("a")
            assert executed == ["a"]

    def test_producible_input_no_error(self) -> None:
        """No error when input is produced by another task."""
        registry = TaskRegistry()
        executed = []

        with tempfile.TemporaryDirectory() as tmpdir:
            intermediate = Path(tmpdir) / "intermediate.txt"

            def task_a() -> None:
                executed.append("a")
                intermediate.write_text("data")

            registry.register(task_a, name="a", outputs=[str(intermediate)])
            registry.register(
                lambda: executed.append("b"),
                name="b",
                inputs=[str(intermediate)],
            )

            executor = Executor(registry, verbose=False)
            executor.run("b")
            assert executed == ["a", "b"]
