"""Tests for task.py."""

import tempfile
from pathlib import Path

import pytest

from pymake import Task, TaskRegistry


class TestTask:
    def test_is_phony_with_no_outputs(self) -> None:
        task = Task(
            name="test",
            func=lambda: None,
            inputs=(),
            outputs=(),
        )
        assert task.is_phony is True

    def test_is_phony_with_outputs(self) -> None:
        task = Task(
            name="test",
            func=lambda: None,
            inputs=(),
            outputs=(Path("out.txt"),),
        )
        assert task.is_phony is False

    def test_should_run_phony_always(self) -> None:
        task = Task(
            name="test",
            func=lambda: None,
            inputs=(),
            outputs=(),
        )
        assert task.should_run() is True

    def test_should_run_missing_output(self) -> None:
        task = Task(
            name="test",
            func=lambda: None,
            inputs=(),
            outputs=(Path("/nonexistent/file.txt"),),
        )
        assert task.should_run() is True

    def test_should_run_output_exists_no_inputs(self) -> None:
        with tempfile.NamedTemporaryFile() as f:
            task = Task(
                name="test",
                func=lambda: None,
                inputs=(),
                outputs=(Path(f.name),),
            )
            assert task.should_run() is False

    def test_should_run_force(self) -> None:
        with tempfile.NamedTemporaryFile() as f:
            task = Task(
                name="test",
                func=lambda: None,
                inputs=(),
                outputs=(Path(f.name),),
            )
            assert task.should_run(force=True) is True


class TestTaskRegistry:
    def test_register_task(self) -> None:
        registry = TaskRegistry()

        def my_task() -> None:
            pass

        task = registry.register(my_task)
        assert task.name == "my_task"
        assert registry.get("my_task") is task

    def test_register_with_custom_name(self) -> None:
        registry = TaskRegistry()
        task = registry.register(lambda: None, name="custom")
        assert task.name == "custom"

    def test_register_with_inputs_outputs(self) -> None:
        registry = TaskRegistry()
        task = registry.register(
            lambda: None,
            name="build",
            inputs=["src/main.c"],
            outputs=["build/main.o"],
        )
        assert task.inputs == (Path("src/main.c"),)
        assert task.outputs == (Path("build/main.o"),)

    def test_register_duplicate_name_raises(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="test")
        with pytest.raises(ValueError, match="already registered"):
            registry.register(lambda: None, name="test")

    def test_register_duplicate_output_raises(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="task1", outputs=["out.txt"])
        with pytest.raises(ValueError, match="already produced"):
            registry.register(lambda: None, name="task2", outputs=["out.txt"])

    def test_decorator_usage(self) -> None:
        registry = TaskRegistry()

        @registry(inputs=["in.txt"], outputs=["out.txt"])
        def build() -> None:
            pass

        task = registry.get("build")
        assert task is not None
        assert task.inputs == (Path("in.txt"),)
        assert task.outputs == (Path("out.txt"),)

    def test_find_target_by_name(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="build")
        task = registry.find_target("build")
        assert task is not None
        assert task.name == "build"

    def test_find_target_by_output(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="build", outputs=["out.txt"])
        task = registry.find_target("out.txt")
        assert task is not None
        assert task.name == "build"

    def test_find_target_not_found(self) -> None:
        registry = TaskRegistry()
        assert registry.find_target("nonexistent") is None

    def test_clear(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="test")
        registry.clear()
        assert registry.get("test") is None

    def test_default_task(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="build")
        registry.register(lambda: None, name="all")

        assert registry.default_task() is None
        registry.default("all")
        assert registry.default_task() == "all"

    def test_clear_resets_default(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="all")
        registry.default("all")
        registry.clear()
        assert registry.default_task() is None
