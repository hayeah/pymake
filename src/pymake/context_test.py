"""Tests for pymake.context — disposable local task registries."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import pymake
from pymake import TaskContext, context


def _write(path: Path, body: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _touch_older(path: Path, seconds: float = 2.0) -> None:
    """Backdate *path*'s mtime so freshness checks see newer inputs."""
    st = path.stat()
    os.utime(path, (st.st_atime - seconds, st.st_mtime - seconds))


class TestFactory:
    def test_context_factory_returns_task_context(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)
        assert isinstance(ctx, TaskContext)
        assert ctx.cwd == tmp_path.resolve()

    def test_context_defaults_to_process_cwd(self) -> None:
        ctx = context()
        assert ctx.cwd == Path.cwd()

    def test_context_accepts_string_cwd(self, tmp_path: Path) -> None:
        ctx = context(cwd=str(tmp_path))
        assert ctx.cwd == tmp_path.resolve()


class TestPathResolution:
    def test_relative_paths_resolve_against_cwd(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)

        @ctx.task(inputs=["in.txt"], outputs=["out.txt"])
        def build() -> None:
            pass

        t = ctx.registry.get("build")
        assert t is not None
        assert t.inputs == (tmp_path / "in.txt",)
        assert t.outputs == (tmp_path / "out.txt",)

    def test_absolute_paths_pass_through(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "elsewhere.txt"
        ctx = context(cwd=tmp_path)

        @ctx.task(inputs=[outside], outputs=[outside.with_suffix(".out")])
        def copy() -> None:
            pass

        t = ctx.registry.get("copy")
        assert t is not None
        assert t.inputs == (outside,)
        assert t.outputs == (outside.with_suffix(".out"),)

    def test_touch_resolves_against_cwd(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)

        @ctx.task(touch=".done")
        def marker() -> None:
            pass

        t = ctx.registry.get("marker")
        assert t is not None
        assert t.touch == tmp_path / ".done"


class TestRun:
    def test_run_executes_happy_path(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)
        (tmp_path / "in.txt").write_text("hello")

        ran: list[str] = []

        @ctx.task(inputs=["in.txt"], outputs=["out.txt"])
        def build() -> None:
            ran.append("build")
            (tmp_path / "out.txt").write_text("HELLO")

        executed = ctx.run(build)
        assert executed is True
        assert ran == ["build"]
        assert (tmp_path / "out.txt").exists()

    def test_run_default_target(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)
        (tmp_path / "in.txt").write_text("x")

        @ctx.task(inputs=["in.txt"], outputs=["out.txt"])
        def build() -> None:
            (tmp_path / "out.txt").write_text("y")

        ctx.default(build)
        assert ctx.run() is True

    def test_run_skips_when_up_to_date(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)
        _write(tmp_path / "in.txt", "in")
        _touch_older(tmp_path / "in.txt", seconds=5.0)

        runs: list[str] = []

        @ctx.task(inputs=["in.txt"], outputs=["out.txt"])
        def build() -> None:
            runs.append("build")
            (tmp_path / "out.txt").write_text("out")

        ctx.run(build)
        assert runs == ["build"]
        # Second run: up-to-date, body should not re-run.
        ctx.run(build)
        assert runs == ["build"]

    def test_force_reruns_up_to_date_task(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)
        _write(tmp_path / "in.txt", "in")
        _touch_older(tmp_path / "in.txt", seconds=5.0)

        runs: list[str] = []

        @ctx.task(inputs=["in.txt"], outputs=["out.txt"])
        def build() -> None:
            runs.append("build")
            (tmp_path / "out.txt").write_text("out")

        ctx.run(build)
        ctx.run(build, force=True)
        assert runs == ["build", "build"]

    def test_force_from_forces_anchor_and_downstream(
        self, tmp_path: Path
    ) -> None:
        ctx = context(cwd=tmp_path)
        _write(tmp_path / "src.txt", "src")
        _touch_older(tmp_path / "src.txt", seconds=10.0)

        runs: list[str] = []

        @ctx.task(inputs=["src.txt"], outputs=["mid.txt"])
        def upstream() -> None:
            runs.append("upstream")
            (tmp_path / "mid.txt").write_text("m")

        @ctx.task(inputs=["mid.txt"], outputs=["final.txt"])
        def anchor() -> None:
            runs.append("anchor")
            (tmp_path / "final.txt").write_text("f")

        @ctx.task(inputs=["final.txt"], outputs=["pub.txt"])
        def downstream() -> None:
            runs.append("downstream")
            (tmp_path / "pub.txt").write_text("p")

        # Prime everything.
        ctx.run(downstream)
        assert runs == ["upstream", "anchor", "downstream"]
        runs.clear()

        # Backdate so nothing naturally needs to run.
        for p in (tmp_path / "mid.txt", tmp_path / "final.txt", tmp_path / "pub.txt"):
            _touch_older(p, seconds=1.0)

        # force_from anchor: anchor + downstream re-run, upstream stays.
        ctx.run(downstream, force_from="anchor")
        assert runs == ["anchor", "downstream"]

    def test_force_and_force_from_mutually_exclusive(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)

        @ctx.task()
        def phony() -> None:
            pass

        with pytest.raises(ValueError, match="force= OR force_from="):
            ctx.run(phony, force=True, force_from="phony")

    def test_dry_run_does_not_execute(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)
        _write(tmp_path / "in.txt", "x")

        runs: list[str] = []

        @ctx.task(inputs=["in.txt"], outputs=["out.txt"])
        def build() -> None:
            runs.append("build")
            (tmp_path / "out.txt").write_text("y")

        executed = ctx.run(build, dry_run=True)
        assert executed is False
        assert runs == []
        assert not (tmp_path / "out.txt").exists()

    def test_run_no_default_no_target_raises(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)
        with pytest.raises(ValueError, match="no default registered"):
            ctx.run()


class TestNoCrossLeak:
    def test_two_contexts_independent(self, tmp_path: Path) -> None:
        a_dir = tmp_path / "A"
        b_dir = tmp_path / "B"
        a_dir.mkdir()
        b_dir.mkdir()
        (a_dir / "in.txt").write_text("A-in")
        (b_dir / "in.txt").write_text("B-in")

        def build_ctx(root: Path) -> TaskContext:
            ctx = context(cwd=root)

            @ctx.task(inputs=["in.txt"], outputs=["out.txt"])
            def hello() -> None:
                (root / "out.txt").write_text(root.name)

            ctx.default(hello)
            return ctx

        a = build_ctx(a_dir)
        b = build_ctx(b_dir)

        # Neither context's registration leaked into the global singleton.
        assert pymake.task.get("hello") is None

        a.run()
        b.run()
        assert (a_dir / "out.txt").read_text() == "A"
        assert (b_dir / "out.txt").read_text() == "B"

        # Each registry only sees its own tasks.
        assert {t.name for t in a.registry.all_tasks()} == {"hello"}
        assert {t.name for t in b.registry.all_tasks()} == {"hello"}
        # And the output maps don't cross.
        assert a.registry.by_output(a_dir / "out.txt") is not None
        assert a.registry.by_output(b_dir / "out.txt") is None


class TestSubTaskInputs:
    def test_task_as_input_resolves_dependency(self, tmp_path: Path) -> None:
        """Spec open question 4: referencing another context task by its
        decorated function in ``inputs=`` should keep working."""
        ctx = context(cwd=tmp_path)
        ran: list[str] = []

        @ctx.task()
        def setup() -> None:
            ran.append("setup")

        @ctx.task(inputs=[setup])
        def build() -> None:
            ran.append("build")

        ctx.run(build)
        # setup ran before build via task-reference dependency
        assert ran == ["setup", "build"]


class TestContextDecoratorName:
    def test_custom_name_via_decorator(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)

        @ctx.task(name="translate:fr")
        def translate() -> None:
            pass

        assert ctx.registry.get("translate:fr") is not None
        # The function's __name__ is NOT registered.
        assert ctx.registry.get("translate") is None


class TestIntrospection:
    def test_which_returns_execution_order(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)

        @ctx.task()
        def a() -> None:
            pass

        @ctx.task(inputs=[a])
        def b() -> None:
            pass

        assert ctx.which(b) == ["a", "b"]

    def test_graph_returns_dot(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)

        @ctx.task(outputs=["out.txt"])
        def make() -> None:
            pass

        dot = ctx.graph(make)
        assert "digraph tasks" in dot
        assert "make" in dot

    def test_clean_removes_outputs(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)

        @ctx.task(outputs=["out.txt"])
        def make() -> None:
            (tmp_path / "out.txt").write_text("x")

        ctx.run(make)
        removed = ctx.clean(make)
        assert removed == [tmp_path / "out.txt"]
        assert not (tmp_path / "out.txt").exists()

    def test_clean_dry_does_not_delete(self, tmp_path: Path) -> None:
        ctx = context(cwd=tmp_path)

        @ctx.task(outputs=["out.txt"])
        def make() -> None:
            (tmp_path / "out.txt").write_text("x")

        ctx.run(make)
        removed = ctx.clean(make, dry=True)
        assert removed == [tmp_path / "out.txt"]
        assert (tmp_path / "out.txt").exists()
