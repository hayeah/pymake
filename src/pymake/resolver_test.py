"""Tests for resolver.py."""

import pytest

from pymake import CyclicDependencyError, DependencyResolver, TaskRegistry


class TestDependencyResolver:
    def test_resolve_no_dependencies(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="build")
        resolver = DependencyResolver(registry)

        task = registry.get("build")
        assert task is not None
        order = resolver.resolve(task)
        assert len(order) == 1
        assert order[0].name == "build"

    def test_resolve_linear_chain(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="a", outputs=["a.txt"])
        registry.register(lambda: None, name="b", inputs=["a.txt"], outputs=["b.txt"])
        registry.register(lambda: None, name="c", inputs=["b.txt"], outputs=["c.txt"])
        resolver = DependencyResolver(registry)

        task = registry.get("c")
        assert task is not None
        order = resolver.resolve(task)
        names = [t.name for t in order]
        assert names == ["a", "b", "c"]

    def test_resolve_diamond_dependencies(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="a", outputs=["a.txt"])
        registry.register(lambda: None, name="b", inputs=["a.txt"], outputs=["b.txt"])
        registry.register(lambda: None, name="c", inputs=["a.txt"], outputs=["c.txt"])
        registry.register(
            lambda: None, name="d", inputs=["b.txt", "c.txt"], outputs=["d.txt"]
        )
        resolver = DependencyResolver(registry)

        task = registry.get("d")
        assert task is not None
        order = resolver.resolve(task)
        names = [t.name for t in order]

        # a must come before b and c; b and c must come before d
        assert names.index("a") < names.index("b")
        assert names.index("a") < names.index("c")
        assert names.index("b") < names.index("d")
        assert names.index("c") < names.index("d")

    def test_detect_cycle(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="a", inputs=["c.txt"], outputs=["a.txt"])
        registry.register(lambda: None, name="b", inputs=["a.txt"], outputs=["b.txt"])
        registry.register(lambda: None, name="c", inputs=["b.txt"], outputs=["c.txt"])
        resolver = DependencyResolver(registry)

        task = registry.get("a")
        assert task is not None
        with pytest.raises(CyclicDependencyError) as exc_info:
            resolver.resolve(task)
        assert "a" in exc_info.value.cycle

    def test_to_dot_simple(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="build", outputs=["out.txt"])
        resolver = DependencyResolver(registry)

        task = registry.get("build")
        assert task is not None
        dot = resolver.to_dot(task)
        assert "digraph" in dot
        assert "build" in dot
        assert "out.txt" in dot

    def test_task_dependencies_with_callable_inputs(self) -> None:
        registry = TaskRegistry()

        def a() -> None:
            pass

        def b() -> None:
            pass

        def all_tasks() -> None:
            pass

        registry.register(a)
        registry.register(b)
        registry.register(all_tasks, name="all", inputs=[a, b])

        resolver = DependencyResolver(registry)
        task = registry.get("all")
        assert task is not None

        deps = resolver.dependencies(task)
        dep_names = [d.name for d in deps]
        assert "a" in dep_names
        assert "b" in dep_names

        order = resolver.resolve(task)
        names = [t.name for t in order]
        assert names.index("a") < names.index("all")
        assert names.index("b") < names.index("all")

    def test_dependents_simple(self) -> None:
        """Test finding immediate dependents of a task."""
        registry = TaskRegistry()
        registry.register(lambda: None, name="a", outputs=["a.txt"])
        registry.register(lambda: None, name="b", inputs=["a.txt"])
        registry.register(lambda: None, name="c", inputs=["a.txt"])

        resolver = DependencyResolver(registry)
        task_a = registry.get("a")
        assert task_a is not None

        dependents = resolver.dependents(task_a)
        dep_names = {d.name for d in dependents}
        assert dep_names == {"b", "c"}

    def test_dependents_with_no_dependents(self) -> None:
        """Test dependents on a leaf task (no dependents)."""
        registry = TaskRegistry()
        registry.register(lambda: None, name="a", outputs=["a.txt"])
        registry.register(lambda: None, name="b", inputs=["a.txt"])

        resolver = DependencyResolver(registry)
        task_b = registry.get("b")
        assert task_b is not None

        dependents = resolver.dependents(task_b)
        assert dependents == []

    def test_transitive_dependents(self) -> None:
        """Test finding all tasks that transitively depend on a task."""
        registry = TaskRegistry()
        registry.register(lambda: None, name="a", outputs=["a.txt"])
        registry.register(lambda: None, name="b", inputs=["a.txt"], outputs=["b.txt"])
        registry.register(lambda: None, name="c", inputs=["b.txt"], outputs=["c.txt"])
        registry.register(lambda: None, name="d", inputs=["c.txt"])

        resolver = DependencyResolver(registry)
        task_a = registry.get("a")
        assert task_a is not None

        trans_deps = resolver.transitive_dependents(task_a)
        # Should include a itself, b, c, and d
        assert trans_deps == {"a", "b", "c", "d"}

    def test_transitive_dependents_diamond(self) -> None:
        """Test transitive dependents with diamond shape."""
        registry = TaskRegistry()
        registry.register(lambda: None, name="a", outputs=["a.txt"])
        registry.register(lambda: None, name="b", inputs=["a.txt"], outputs=["b.txt"])
        registry.register(lambda: None, name="c", inputs=["a.txt"], outputs=["c.txt"])
        registry.register(
            lambda: None, name="d", inputs=["b.txt", "c.txt"], outputs=["d.txt"]
        )

        resolver = DependencyResolver(registry)
        task_a = registry.get("a")
        assert task_a is not None

        trans_deps = resolver.transitive_dependents(task_a)
        # a affects b, c, and d
        assert trans_deps == {"a", "b", "c", "d"}

        # b only affects d
        task_b = registry.get("b")
        assert task_b is not None
        trans_deps_b = resolver.transitive_dependents(task_b)
        assert trans_deps_b == {"b", "d"}
