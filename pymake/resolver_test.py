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
