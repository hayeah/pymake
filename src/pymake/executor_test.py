"""Tests for executor.py."""

import io
import os
import tempfile
from pathlib import Path

import pytest

from pymake import (
    CyclicDependencyError,
    ExecutionError,
    Executor,
    MissingOutputError,
    TaskRegistry,
    UnproducibleInputError,
    VarsResolver,
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

    def test_executor_passes_resolved_vars(self) -> None:
        registry = TaskRegistry()
        seen: list[tuple[bool, int]] = []

        def build(optimize: bool = False, jobs: int = 1) -> None:
            seen.append((optimize, jobs))

        registry.register(build)
        resolver = VarsResolver(vars_overrides=["build.optimize=true", "build.jobs=4"])
        executor = Executor(registry, vars_resolver=resolver, verbose=False)
        executor.run("build")
        assert seen == [(True, 4)]

    def test_executor_uses_defaults_when_no_vars_sources(self) -> None:
        registry = TaskRegistry()
        seen: list[str] = []

        def greet(name: str = "world") -> None:
            seen.append(name)

        registry.register(greet)
        executor = Executor(registry, verbose=False)
        executor.run("greet")
        assert seen == ["world"]

    def test_execute_task_validates_vars_overrides_once(self) -> None:
        registry = TaskRegistry()
        task = registry.register(lambda: None, name="build")
        resolver = VarsResolver(vars_overrides=["ghost.port=1"])
        executor = Executor(registry, vars_resolver=resolver, verbose=False)

        with pytest.raises(ValueError, match="unknown task 'ghost'"):
            executor._execute_task(task)


class _Predicate:
    """Test double for a stateful run_if predicate (mimics TreeDigest)."""

    def __init__(self, answers: list[bool]) -> None:
        self.answers = answers
        self.calls = 0
        self.commits = 0

    def __call__(self) -> bool:
        result = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return result

    def commit(self) -> None:
        self.commits += 1


class TestRunIfCommitProtocol:
    """Executor integration for the ``run_if.commit()`` hook."""

    def test_commit_called_after_successful_run(self) -> None:
        registry = TaskRegistry()
        executed: list[str] = []
        predicate = _Predicate([True])

        registry.register(
            lambda: executed.append("a"),
            name="a",
            run_if=predicate,
        )

        Executor(registry, verbose=False).run("a")
        assert executed == ["a"]
        assert predicate.commits == 1

    def test_commit_not_called_when_run_if_skipped(self) -> None:
        registry = TaskRegistry()
        executed: list[str] = []
        predicate = _Predicate([False])

        registry.register(
            lambda: executed.append("a"),
            name="a",
            run_if=predicate,
        )

        Executor(registry, verbose=False).run("a")
        assert executed == []
        assert predicate.commits == 0

    def test_commit_not_called_when_task_fails(self) -> None:
        registry = TaskRegistry()
        predicate = _Predicate([True])

        def boom() -> None:
            raise RuntimeError("nope")

        registry.register(boom, name="boom", run_if=predicate)

        with pytest.raises(ExecutionError):
            Executor(registry, verbose=False).run("boom")
        assert predicate.commits == 0

    def test_plain_callable_run_if_without_commit_attr(self) -> None:
        """Back-compat: a bare lambda run_if must still work."""
        registry = TaskRegistry()
        executed: list[str] = []

        registry.register(
            lambda: executed.append("a"),
            name="a",
            run_if=lambda: True,  # no .commit attribute
        )

        Executor(registry, verbose=False).run("a")
        assert executed == ["a"]


class TestForceBypassesRunIf:
    """``--force`` should run the task regardless of run_if / run_if_not."""

    def test_force_bypasses_run_if_false(self) -> None:
        registry = TaskRegistry()
        executed: list[str] = []
        predicate = _Predicate([False])

        registry.register(
            lambda: executed.append("a"),
            name="a",
            run_if=predicate,
        )

        Executor(registry, force=True, verbose=False).run("a")
        assert executed == ["a"]
        # run_if should not even have been consulted
        assert predicate.calls == 0
        # and commit still happens after the forced run so state stays fresh
        assert predicate.commits == 1

    def test_force_bypasses_run_if_not_true(self) -> None:
        registry = TaskRegistry()
        executed: list[str] = []

        registry.register(
            lambda: executed.append("a"),
            name="a",
            run_if_not=lambda: True,
        )

        Executor(registry, force=True, verbose=False).run("a")
        assert executed == ["a"]


class TestRunIfEndToEndWithTreeDigest:
    """Drive the full digest-based skip loop through the executor."""

    def test_digest_skip_then_change_then_force(self, tmp_path: Path) -> None:
        from pymake.digest import TreeDigest

        src = tmp_path / "src"
        src.mkdir()
        f = src / "main.py"
        f.write_text("print('one')\n")

        digest = TreeDigest(src, digest=tmp_path / ".state")

        registry = TaskRegistry()
        runs: list[int] = []

        def build() -> None:
            runs.append(1)

        registry.register(build, run_if=digest.changed)

        # First run: state file missing → runs and commits.
        Executor(registry, verbose=False).run("build")
        assert len(runs) == 1
        assert (tmp_path / ".state").exists()

        # Second run (fresh digest instance to simulate a new invocation):
        # nothing changed, should skip.
        digest2 = TreeDigest(src, digest=tmp_path / ".state")
        registry2 = TaskRegistry()
        registry2.register(lambda: runs.append(2), name="build", run_if=digest2.changed)
        Executor(registry2, verbose=False).run("build")
        assert len(runs) == 1  # still 1 — task skipped

        # Mutate source: mtime bump is enough.
        st = f.stat()
        os.utime(f, (st.st_atime, st.st_mtime + 5))

        digest3 = TreeDigest(src, digest=tmp_path / ".state")
        registry3 = TaskRegistry()
        registry3.register(lambda: runs.append(3), name="build", run_if=digest3.changed)
        Executor(registry3, verbose=False).run("build")
        assert len(runs) == 2

        # --force runs even if digest would say "unchanged".
        digest4 = TreeDigest(src, digest=tmp_path / ".state")
        registry4 = TaskRegistry()
        registry4.register(lambda: runs.append(4), name="build", run_if=digest4.changed)
        Executor(registry4, force=True, verbose=False).run("build")
        assert len(runs) == 3


class _Group:
    """A group class whose instance carries context and a stateful digest."""

    def __init__(self, label: str, out: Path) -> None:
        self.label = label
        self.out = out
        self.digest = _Predicate([True, False])
        self.ran: list[str] = []

    def build_assets(self) -> None:
        self.ran.append("build_assets")

    def build_app(self, type: str = "release") -> None:
        self.ran.append(f"build_app:{type}")
        self.out.write_text(f"{self.label}:{type}")


class TestGroupTasks:
    """End-to-end execution of tasks registered from class instances."""

    def test_bound_methods_run_with_vars_and_deps(self, tmp_path: Path) -> None:
        registry = TaskRegistry()
        group = _Group("win", tmp_path / "app.bin")

        registry(group.build_assets)
        registry(
            group.build_app,
            inputs=[group.build_assets],
            outputs=[group.out],
        )

        resolver = VarsResolver(
            vars_overrides=["type=dev"], targets=["_Group.build_app"]
        )
        executor = Executor(registry, vars_resolver=resolver, verbose=False)
        executor.run("_Group.build_app")

        assert group.ran == ["build_assets", "build_app:dev"]
        assert group.out.read_text() == "win:dev"

    def test_string_reference_runs_the_referenced_task(self, tmp_path: Path) -> None:
        registry = TaskRegistry()
        group = _Group("mac", tmp_path / "app.bin")

        # Forward reference: build_app registered before build_assets exists.
        registry(group.build_app, inputs=["_Group.build_assets"], outputs=[group.out])
        registry(group.build_assets)
        registry.finalize()

        Executor(registry, verbose=False).run("_Group.build_app")

        assert group.ran == ["build_assets", "build_app:release"]

    def test_unresolved_dotted_reference_reports_a_task_ref(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="build", inputs=["Common.build_assets"])
        registry.finalize()

        executor = Executor(registry, verbose=False)
        with pytest.raises(UnproducibleInputError, match="is its group registered"):
            executor.run("build")

    def test_missing_plain_file_keeps_the_file_message(self) -> None:
        registry = TaskRegistry()
        registry.register(lambda: None, name="build", inputs=["src/missing.c"])
        registry.finalize()

        executor = Executor(registry, verbose=False)
        with pytest.raises(UnproducibleInputError, match="no task produces it"):
            executor.run("build")

    def test_instance_owned_digest_is_committed_after_a_run(self) -> None:
        registry = TaskRegistry()
        group = _Group("win", Path("unused"))

        registry(group.build_assets, run_if=group.digest)

        executor = Executor(registry, verbose=False)
        assert executor.run("_Group.build_assets") is True
        assert group.digest.commits == 1

        # Second run: the predicate now answers False, so no commit.
        assert Executor(registry, verbose=False).run("_Group.build_assets") is False
        assert group.digest.commits == 1

    def test_parallel_run_over_one_instance(self, tmp_path: Path) -> None:
        registry = TaskRegistry()
        group = _Group("win", tmp_path / "app.bin")

        registry(group.build_assets)
        registry(group.build_app, inputs=[group.build_assets], outputs=[group.out])

        executor = Executor(registry, parallel=True, verbose=False)
        executor.run("_Group.build_app")

        assert group.ran == ["build_assets", "build_app:release"]


class _CountingInput:
    """Custom Input (id + fingerprint) with a controllable fingerprint."""

    def __init__(self, id: str, fp: str = "one") -> None:
        self.id = id
        self.fp = fp
        self.calls = 0

    def fingerprint(self) -> str:
        self.calls += 1
        return self.fp


class TestFingerprintStaleness:
    """The single staleness rule over per-task fingerprint records."""

    def _executor(
        self, registry: TaskRegistry, tmp_path: Path, **kw: object
    ) -> Executor:
        return Executor(registry, verbose=False, state_dir=tmp_path / "state", **kw)  # type: ignore[arg-type]

    def test_value_input_gates_an_outputless_task(self, tmp_path: Path) -> None:
        from pymake import value

        runs: list[int] = []
        config = {"opt": False}
        cfg = value("build-config", config)

        def make_registry() -> TaskRegistry:
            registry = TaskRegistry()
            registry.register(lambda: runs.append(1), name="build", inputs=[cfg])
            return registry

        # First run: no record.
        self._executor(make_registry(), tmp_path).run("build")
        assert len(runs) == 1

        # Second invocation: unchanged value — the state file is the marker.
        self._executor(make_registry(), tmp_path).run("build")
        assert len(runs) == 1

        # Value changes: re-run.
        config["opt"] = True
        self._executor(make_registry(), tmp_path).run("build")
        assert len(runs) == 2

    def test_no_record_runs_even_when_outputs_look_fresh(self, tmp_path: Path) -> None:
        from pymake import value

        out = tmp_path / "tool.exe"
        out.write_text("stale binary")
        runs: list[int] = []

        registry = TaskRegistry()
        registry.register(
            lambda: runs.append(1),
            name="build_native",
            inputs=[value("native-cfg", "x")],
            outputs=[out],
        )

        self._executor(registry, tmp_path).run("build_native")
        assert runs == [1]

    def test_missing_output_reruns(self, tmp_path: Path) -> None:
        from pymake import value

        out = tmp_path / "tool.exe"
        runs: list[int] = []

        def build() -> None:
            runs.append(1)
            out.write_text("bin")

        def make_registry() -> TaskRegistry:
            registry = TaskRegistry()
            registry.register(
                build, name="build", inputs=[value("cfg", "x")], outputs=[out]
            )
            return registry

        self._executor(make_registry(), tmp_path).run("build")
        self._executor(make_registry(), tmp_path).run("build")
        assert len(runs) == 1  # settled

        out.unlink()
        self._executor(make_registry(), tmp_path).run("build")
        assert len(runs) == 2

    def test_forced_runs_record_fingerprints_normally(self, tmp_path: Path) -> None:
        from pymake import value

        runs: list[int] = []

        def make_registry() -> TaskRegistry:
            registry = TaskRegistry()
            registry.register(
                lambda: runs.append(1), name="build", inputs=[value("cfg", "x")]
            )
            return registry

        # Forced first run records state...
        self._executor(make_registry(), tmp_path, force=True).run("build")
        assert len(runs) == 1
        # ...so the next ordinary run skips.
        self._executor(make_registry(), tmp_path).run("build")
        assert len(runs) == 1

    def test_records_are_per_task_not_per_input(self, tmp_path: Path) -> None:
        """Task A running must not settle a shared input for task B."""
        shared = _CountingInput("shared-tree")
        runs: list[str] = []

        registry = TaskRegistry()
        registry.register(lambda: runs.append("a"), name="a", inputs=[shared])
        registry.register(lambda: runs.append("b"), name="b", inputs=[shared])

        # Run ONLY task a.
        self._executor(registry, tmp_path).run("a")
        assert runs == ["a"]

        # A fresh invocation running b: b has no record and must run,
        # even though a's run recorded the same input id.
        registry2 = TaskRegistry()
        registry2.register(lambda: runs.append("a"), name="a", inputs=[shared])
        registry2.register(lambda: runs.append("b"), name="b", inputs=[shared])
        self._executor(registry2, tmp_path).run("b")
        assert runs == ["a", "b"]

    def test_fingerprint_computed_once_per_run_for_shared_inputs(
        self, tmp_path: Path
    ) -> None:
        shared = _CountingInput("shared-tree")

        def make_registry() -> TaskRegistry:
            registry = TaskRegistry()
            registry.register(lambda: None, name="a", inputs=[shared])
            registry.register(lambda: None, name="b", inputs=[shared, "a"])
            registry.finalize()
            return registry

        # Settle both tasks.
        self._executor(make_registry(), tmp_path).run("b")

        # Steady-state invocation: both tasks skip; ONE walk serves both.
        shared.calls = 0
        self._executor(make_registry(), tmp_path).run("b")
        assert shared.calls == 1

    def test_dep_outputs_contribution(self, tmp_path: Path) -> None:
        """A consumer re-runs when its dep's declared outputs changed."""
        src = tmp_path / "src.txt"
        src.write_text("v1")
        out = tmp_path / "tool.exe"
        pkg = tmp_path / "pkg.txt"
        runs: list[str] = []

        def make_registry() -> TaskRegistry:
            registry = TaskRegistry()

            def build() -> None:
                runs.append("build")
                out.write_text(f"bin:{src.read_text()}")

            def package() -> None:
                runs.append("package")
                pkg.write_text(f"pkg:{out.read_text()}")

            build_task = registry.register(build, inputs=[src], outputs=[out])
            registry.register(
                package, name="package", inputs=[build_task.func], outputs=[pkg]
            )
            return registry

        self._executor(make_registry(), tmp_path).run("package")
        assert runs == ["build", "package"]

        # Nothing changed: both settle.
        self._executor(make_registry(), tmp_path).run("package")
        assert runs == ["build", "package"]

        # Source edit: build re-links, and package SEES the fresh binary
        # through the dep edge — no hand-listed path needed.
        src.write_text("v2 with longer content")
        self._executor(make_registry(), tmp_path).run("package")
        assert runs == ["build", "package", "build", "package"]

    def test_decision_lines_say_why(self, tmp_path: Path) -> None:
        from pymake import value

        icon = tmp_path / "icon.png"
        icon.write_text("png")
        config = {"opt": False}
        runs: list[int] = []

        def make_executor(out: io.StringIO) -> Executor:
            registry = TaskRegistry()
            registry.register(
                lambda: runs.append(1),
                name="build_native",
                inputs=[value("build-config", config), icon],
            )
            return Executor(
                registry, verbose=True, output=out, state_dir=tmp_path / "state"
            )

        out1 = io.StringIO()
        make_executor(out1).run("build_native")
        assert "[run] build_native (no record)" in out1.getvalue()

        out2 = io.StringIO()
        make_executor(out2).run("build_native")
        assert "[skip] build_native (unchanged)" in out2.getvalue()

        config["opt"] = True
        out3 = io.StringIO()
        make_executor(out3).run("build_native")
        assert "[run] build_native (input build-config changed)" in out3.getvalue()

        import time

        time.sleep(0.01)
        icon.write_text("png2")
        out4 = io.StringIO()
        make_executor(out4).run("build_native")
        assert f"[run] build_native (path {icon.as_posix()} changed)" in out4.getvalue()

        out5 = io.StringIO()
        registry = TaskRegistry()
        registry.register(
            lambda: runs.append(1),
            name="build_native",
            inputs=[value("build-config", config), icon],
        )
        Executor(
            registry,
            verbose=True,
            output=out5,
            force=True,
            state_dir=tmp_path / "state",
        ).run("build_native")
        assert "[run] build_native (forced)" in out5.getvalue()

    def test_dep_change_names_the_dep(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("v1")
        out = tmp_path / "tool.exe"
        pkg = tmp_path / "pkg.txt"

        def make_executor(sink: io.StringIO) -> Executor:
            registry = TaskRegistry()

            def build() -> None:
                out.write_text(f"bin:{src.read_text()}")

            def package() -> None:
                pkg.write_text("pkg")

            registry.register(build, name="build_native", inputs=[src], outputs=[out])
            registry.register(
                package, name="package", inputs=["build_native"], outputs=[pkg]
            )
            registry.finalize()
            return Executor(
                registry, verbose=True, output=sink, state_dir=tmp_path / "state"
            )

        make_executor(io.StringIO()).run("package")
        src.write_text("v2 with longer content")
        sink = io.StringIO()
        make_executor(sink).run("package")
        assert "[run] package (dep build_native outputs changed)" in sink.getvalue()

    def test_legacy_task_bootstraps_a_record_on_skip(self, tmp_path: Path) -> None:
        """Fresh-by-mtime legacy tasks skip once, then live on fingerprints."""
        import time

        src = tmp_path / "src.txt"
        src.write_text("v1")
        time.sleep(0.01)
        out = tmp_path / "out.txt"
        out.write_text("built")  # output newer than input: fresh by mtime
        runs: list[int] = []

        def make_registry() -> TaskRegistry:
            registry = TaskRegistry()
            registry.register(
                lambda: runs.append(1), name="build", inputs=[src], outputs=[out]
            )
            return registry

        sink = io.StringIO()
        registry = make_registry()
        Executor(registry, verbose=True, output=sink, state_dir=tmp_path / "state").run(
            "build"
        )
        assert runs == []
        assert "[skip] build (up to date)" in sink.getvalue()
        # The skip bootstrapped a fingerprint record.
        assert (tmp_path / "state").exists()

        # An edit that mtime staleness would MISS (backdated mtime, same
        # size is avoided by changing length) now triggers via fingerprints.
        src.write_text("v2 much longer than before")
        st = src.stat()
        os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns - 10_000_000_000))
        self._executor(make_registry(), tmp_path).run("build")
        assert runs == [1]

    def test_legacy_phony_tasks_still_always_run(self, tmp_path: Path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("x")
        runs: list[str] = []

        def make_registry() -> TaskRegistry:
            registry = TaskRegistry()
            registry.register(lambda: runs.append("lint"), name="lint")
            registry.register(lambda: runs.append("test"), name="test", inputs=[src])
            return registry

        for _ in range(2):
            executor = self._executor(make_registry(), tmp_path)
            executor.run("lint")
            executor.run("test")

        # Phony semantics preserved: both ran both times, input change or not.
        assert runs == ["lint", "test", "lint", "test"]

    def test_state_file_is_kind_partitioned(self, tmp_path: Path) -> None:
        import json

        from pymake import value

        icon = tmp_path / "icon.png"
        icon.write_text("png")
        out = tmp_path / "tool.exe"

        def build() -> None:
            out.write_text("bin")

        registry = TaskRegistry()
        registry.register(lambda: None, name="build_assets", outputs=[icon])
        registry.register(
            build,
            name="build_native",
            inputs=[value("build-config", "opt=1"), icon, "build_assets"],
            outputs=[out],
        )
        registry.finalize()

        self._executor(registry, tmp_path).run("build_native")

        from pymake.state import StateStore

        store = StateStore(tmp_path / "state")
        data = json.loads(store.path_for("build_native").read_text())
        assert set(data["paths"]) == {icon.as_posix()}
        assert set(data["deps"]) == {"build_assets"}
        assert set(data["inputs"]) == {"build-config"}


class TestInlineWarnings:
    """Divergence and nondeterminism warn inline, on the runs that show them."""

    def test_self_mutating_task_warns_and_settles(self, tmp_path: Path) -> None:
        """A task rewriting its own input diverges (warn) but settles."""
        src = tmp_path / "notes.txt"
        src.write_text("v1")
        runs: list[int] = []

        def make_executor(sink: io.StringIO) -> Executor:
            registry = TaskRegistry()

            def mutate() -> None:
                runs.append(1)
                src.write_text(f"run {len(runs)} content")

            registry.register(
                mutate, name="build", inputs=[src], touch=tmp_path / ".done"
            )
            return Executor(
                registry, verbose=True, output=sink, state_dir=tmp_path / "state"
            )

        sink = io.StringIO()
        make_executor(sink).run("build")
        assert runs == [1]
        warning = f"[warn] build: path {src.as_posix()} changed during the run"
        assert warning in sink.getvalue()
        assert "redo build" in sink.getvalue()

        # Post-run recording absorbed the self-mutation: next run settles.
        sink2 = io.StringIO()
        make_executor(sink2).run("build")
        assert runs == [1]
        assert "[skip] build (unchanged)" in sink2.getvalue()

    def test_git_divergence_reports_dirt_sample(self, tmp_path: Path) -> None:
        """A task dirtying its own git input names the droppings."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        for args in (
            ["init", "-q", "-b", "master"],
            ["config", "user.email", "t@example.com"],
            ["config", "user.name", "T"],
        ):
            subprocess.run(["git", "-C", str(repo), *args], check=True)
        (repo / "main.c").write_text("v1")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "c"], check=True)

        from pymake import git

        def leak() -> None:
            (repo / "droppings.tmp").write_text("junk")

        registry = TaskRegistry()
        registry.register(
            leak,
            name="build",
            inputs=[git("native-sources", repo)],
            touch=tmp_path / ".done",
        )

        sink = io.StringIO()
        Executor(registry, verbose=True, output=sink, state_dir=tmp_path / "state").run(
            "build"
        )

        out = sink.getvalue()
        assert "[warn] build: input native-sources" in out
        assert "changed during the run" in out
        assert "?? droppings.tmp" in out

    def test_flip_counter_warns_at_three(self, tmp_path: Path) -> None:
        """An input that changes on every run gets named, with its defsite."""
        flippy = _CountingInput("build-config")

        def run_once(sink: io.StringIO) -> None:
            registry = TaskRegistry()
            registry.register(lambda: None, name="build", inputs=[flippy])
            Executor(
                registry, verbose=True, output=sink, state_dir=tmp_path / "state"
            ).run("build")

        warning = (
            "[warn] build: input build-config has changed on every one of "
            "the last 3 runs"
        )

        outputs: list[str] = []
        for i in range(4):
            flippy.fp = f"world-state-{i}"  # stable within a run
            sink = io.StringIO()
            run_once(sink)
            outputs.append(sink.getvalue())

        # Runs 1-3: no warning yet (run 1 has no prior record; flips reach
        # 2 by run 3).
        assert all(warning not in out for out in outputs[:3])
        # Run 4: three consecutive changed-since-last-record runs.
        assert warning in outputs[3]
        assert "nondeterministic value or self-mutating task?" in outputs[3]

    def test_steady_input_resets_the_flip_counter(self, tmp_path: Path) -> None:
        flippy = _CountingInput("build-config")
        out = tmp_path / "out.txt"

        def run_once() -> str:
            registry = TaskRegistry()

            def build() -> None:
                out.write_text("x")

            registry.register(build, name="build", inputs=[flippy], outputs=[out])
            sink = io.StringIO()
            Executor(
                registry, verbose=True, output=sink, state_dir=tmp_path / "state"
            ).run("build")
            return sink.getvalue()

        # Two flips...
        for i in range(3):
            flippy.fp = f"state-{i}"
            run_once()
        # ...then the value holds still but the task re-runs for another
        # reason (missing output): the counter resets.
        out.unlink()
        run_once()

        from pymake.state import StateStore

        record = StateStore(tmp_path / "state").load("build")
        assert record is not None
        assert record.flip_count("inputs", "build-config") == 0
