"""Tests for task.py."""

import tempfile
from pathlib import Path

import pytest

from pymake import Task, TaskRegistry


class Common:
    """A group class: an ordinary class, no pymake import needed."""

    def __init__(self, label: str = "common") -> None:
        self.label = label
        self.calls: list[str] = []

    def build_assets(self) -> None:
        self.calls.append("build_assets")

    def build_app(self, type: str = "release") -> None:
        """Build the app."""
        self.calls.append(f"build_app:{type}")


class Windows(Common):
    """Inherits build_app — the runtime class names the group."""


def module_level_task() -> None:
    pass


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

    def test_register_extracts_vars_from_signature(self) -> None:
        registry = TaskRegistry()

        def deploy(
            env: str | None = None, port: int = 8080, loud: bool = False
        ) -> None:
            pass

        task = registry.register(deploy)
        assert len(task.vars) == 3
        assert task.vars[0].name == "env"
        assert task.vars[0].type is str
        assert task.vars[0].is_optional is True
        assert task.vars[0].default is None
        assert task.vars[1].name == "port"
        assert task.vars[1].type is int
        assert task.vars[1].default == 8080
        assert task.vars[2].name == "loud"
        assert task.vars[2].type is bool
        assert task.vars[2].default is False

    def test_register_optional_without_default_allowed(self) -> None:
        registry = TaskRegistry()

        def deploy(env: str | None) -> None:
            pass

        task = registry.register(deploy)
        assert len(task.vars) == 1
        assert task.vars[0].default is None
        assert task.vars[0].is_optional is True

    def test_register_missing_default_for_non_optional_raises(self) -> None:
        registry = TaskRegistry()

        def deploy(env: str) -> None:
            pass

        with pytest.raises(
            ValueError, match="must have a default value or be Optional"
        ):
            registry.register(deploy)

    def test_register_unsupported_var_type_raises(self) -> None:
        registry = TaskRegistry()

        def build(flags: list[str] | None = None) -> None:
            pass

        with pytest.raises(ValueError, match="unsupported type"):
            registry.register(build)

    def test_register_varargs_not_supported(self) -> None:
        registry = TaskRegistry()

        def build(*args: str) -> None:
            pass

        with pytest.raises(ValueError, match=r"\*args/\*\*kwargs not supported"):
            registry.register(build)

    def test_register_kwargs_not_supported(self) -> None:
        registry = TaskRegistry()

        def build(**kwargs: str) -> None:
            pass

        with pytest.raises(ValueError, match=r"\*args/\*\*kwargs not supported"):
            registry.register(build)


class TestBareRegistration:
    def test_bound_method_infers_class_dot_method(self) -> None:
        registry = TaskRegistry()
        common = Common()

        task = registry(common.build_app)

        assert task.name == "Common.build_app"
        assert registry.get("Common.build_app") is task
        assert task.doc == "Build the app."
        assert [v.name for v in task.vars] == ["type"]

    def test_inherited_method_uses_runtime_class(self) -> None:
        registry = TaskRegistry()
        windows = Windows()

        # build_app is defined on Common; __qualname__ would say "Common".
        task = registry(windows.build_app)

        assert task.name == "Windows.build_app"

    def test_plain_function_keeps_its_name(self) -> None:
        registry = TaskRegistry()

        task = registry(module_level_task)

        assert task.name == "module_level_task"

    def test_name_overrides_inference(self) -> None:
        registry = TaskRegistry()
        common = Common()

        task = registry(common.build_assets, name="Apple.build_assets")

        assert task.name == "Apple.build_assets"

    def test_lambda_without_name_raises(self) -> None:
        registry = TaskRegistry()

        with pytest.raises(ValueError, match="lambda"):
            registry(lambda: None)

    def test_lambda_with_name_registers(self) -> None:
        registry = TaskRegistry()

        task = registry(lambda: None, name="anonymous")

        assert task.name == "anonymous"

    def test_local_function_without_name_raises(self) -> None:
        registry = TaskRegistry()

        def local_task() -> None:
            pass

        with pytest.raises(ValueError, match="local function"):
            registry(local_task)

    def test_metadata_kwargs_match_the_decorator(self) -> None:
        registry = TaskRegistry()
        common = Common()
        flag = {"ran": False}

        task = registry(
            common.build_assets,
            inputs=["in.txt"],
            outputs=["out.txt"],
            run_if=lambda: flag["ran"],
        )

        assert task.inputs == (Path("in.txt"),)
        assert task.outputs == (Path("out.txt"),)
        assert task.run_if is not None

    def test_decorator_form_still_works(self) -> None:
        registry = TaskRegistry()

        @registry(inputs=["in.txt"], outputs=["out.txt"])
        def build() -> None:
            pass

        task = registry.get("build")
        assert task is not None
        assert task.inputs == (Path("in.txt"),)

    def test_decorator_form_with_positional_inputs(self) -> None:
        registry = TaskRegistry()

        @registry(["in.txt"], ["out.txt"])
        def build() -> None:
            pass

        task = registry.get("build")
        assert task is not None
        assert task.inputs == (Path("in.txt"),)
        assert task.outputs == (Path("out.txt"),)

    def test_decorator_form_rejects_double_inputs(self) -> None:
        registry = TaskRegistry()

        with pytest.raises(ValueError, match="not both"):
            registry(["in.txt"], inputs=["other.txt"])


class TestGroupRegistrar:
    def test_dotted_names_by_default(self) -> None:
        registry = TaskRegistry()
        common = Common()

        group = registry.group(namespace="Shared")
        task = group.task(common.build_assets)

        assert task.name == "Shared.build_assets"

    def test_underscore_separator_matches_flat_names(self) -> None:
        registry = TaskRegistry()
        windows = Windows()

        group = registry.group(namespace="windows", sep="_")
        task = group.task(windows.build_app, inputs=["in.txt"])

        assert task.name == "windows_build_app"
        assert task.inputs == (Path("in.txt"),)

    def test_name_overrides_the_namespace(self) -> None:
        registry = TaskRegistry()
        common = Common()

        group = registry.group(namespace="Shared")
        task = group.task(common.build_assets, name="Apple.build_assets")

        assert task.name == "Apple.build_assets"

    def test_registrar_is_stateless_value_object(self) -> None:
        registry = TaskRegistry()

        one = registry.group(namespace="Shared")
        two = registry.group(namespace="Shared")

        one.task(Common().build_assets)
        two.task(Common().build_app)

        assert {t.name for t in registry.all_tasks()} == {
            "Shared.build_assets",
            "Shared.build_app",
        }

    def test_invalid_separator_raises(self) -> None:
        registry = TaskRegistry()

        with pytest.raises(ValueError, match="separator"):
            registry.group(namespace="Shared", sep="-")

    def test_dotted_namespace_raises(self) -> None:
        registry = TaskRegistry()

        with pytest.raises(ValueError, match="plain identifier"):
            registry.group(namespace="Shared.sub")

    def test_empty_namespace_raises(self) -> None:
        registry = TaskRegistry()

        with pytest.raises(ValueError, match="non-empty"):
            registry.group(namespace="")

    def test_parameterized_group_gets_one_task_per_instance(self) -> None:
        registry = TaskRegistry()
        mac_probe = Common("mac")
        win_probe = Common("win")

        registry.group(namespace="Macos").task(mac_probe.build_assets)
        registry.group(namespace="Windows").task(win_probe.build_assets)

        mac = registry.get("Macos.build_assets")
        win = registry.get("Windows.build_assets")
        assert mac is not None and win is not None
        assert mac.func.__self__ is mac_probe  # type: ignore[attr-defined]
        assert win.func.__self__ is win_probe  # type: ignore[attr-defined]

    def test_two_instances_collide_with_a_pointed_message(self) -> None:
        registry = TaskRegistry()

        registry(Common().build_assets)
        with pytest.raises(ValueError, match=r"task\.group\(namespace=\.\.\.\)"):
            registry(Common().build_assets)


class TestCallableDependencies:
    def test_bound_method_dependency_resolves_to_registered_name(self) -> None:
        registry = TaskRegistry()
        common = Common()

        registry(common.build_assets)
        task = registry(common.build_app, inputs=[common.build_assets])

        assert task.depends == ("Common.build_assets",)

    def test_two_instances_of_one_class_stay_distinct(self) -> None:
        registry = TaskRegistry()
        mac = Common("mac")
        win = Common("win")

        registry.group(namespace="Macos").task(mac.build_assets)
        registry.group(namespace="Windows").task(win.build_assets)

        mac_app = registry.group(namespace="Macos").task(
            mac.build_app, inputs=[mac.build_assets]
        )
        win_app = registry.group(namespace="Windows").task(
            win.build_app, inputs=[win.build_assets]
        )

        assert mac_app.depends == ("Macos.build_assets",)
        assert win_app.depends == ("Windows.build_assets",)

    def test_default_accepts_a_bound_method(self) -> None:
        registry = TaskRegistry()
        common = Common()

        registry(common.build_app)
        registry.default(common.build_app)

        assert registry.default_task() == "Common.build_app"

    def test_default_accepts_a_dotted_string(self) -> None:
        registry = TaskRegistry()
        registry(Common().build_app)

        registry.default("Common.build_app")

        assert registry.default_task() == "Common.build_app"


class TestStringTaskReferences:
    def test_forward_reference_resolves_at_finalize(self) -> None:
        registry = TaskRegistry()
        common = Common()

        # Registered BEFORE its dependency exists.
        app = registry(common.build_app, inputs=["Common.build_assets"])
        assert app.depends == ()
        assert app.inputs == (Path("Common.build_assets"),)

        registry(common.build_assets)
        registry.finalize()

        assert app.depends == ("Common.build_assets",)
        assert app.inputs == ()

    def test_finalize_is_idempotent(self) -> None:
        registry = TaskRegistry()
        common = Common()

        registry(common.build_assets)
        app = registry(common.build_app, inputs=["Common.build_assets"])

        registry.finalize()
        registry.finalize()

        assert app.depends == ("Common.build_assets",)
        assert app.inputs == ()

    def test_unmatched_string_stays_a_file_input(self) -> None:
        registry = TaskRegistry()
        task = registry.register(lambda: None, name="build", inputs=["src/main.c"])

        registry.finalize()

        assert task.inputs == (Path("src/main.c"),)
        assert task.depends == ()

    def test_path_input_is_always_a_file(self) -> None:
        registry = TaskRegistry()
        common = Common()

        registry(common.build_assets)
        # Same spelling as the task name, but a Path: never a task reference.
        task = registry(common.build_app, inputs=[Path("Common.build_assets")])

        registry.finalize()

        assert task.inputs == (Path("Common.build_assets"),)
        assert task.depends == ()

    def test_self_reference_is_not_a_dependency(self) -> None:
        registry = TaskRegistry()
        task = registry.register(lambda: None, name="build", inputs=["build"])

        registry.finalize()

        assert task.depends == ()
        assert task.inputs == (Path("build"),)

    def test_ref_hint_flags_a_dotted_string_with_no_task(self) -> None:
        registry = TaskRegistry()
        task = registry.register(
            lambda: None, name="build", inputs=["Common.build_assets"]
        )
        registry.finalize()

        hint = task.ref_hint(Path("Common.build_assets"))
        assert hint is not None
        assert "is its group registered?" in hint

    def test_ref_hint_ignores_ordinary_files(self) -> None:
        registry = TaskRegistry()
        task = registry.register(
            lambda: None, name="build", inputs=["src/main.c", "notes.txt"]
        )
        registry.finalize()

        assert task.ref_hint(Path("src/main.c")) is None
        assert task.ref_hint(Path("notes.txt")) == (
            "no task and no file named 'notes.txt' — is its group registered?"
        )


class TestInputObjectRegistration:
    """Registration-time enforcement of the Input contract (id namespace)."""

    def test_input_objects_are_partitioned_from_paths_and_deps(self) -> None:
        from pymake import value

        registry = TaskRegistry()
        registry.register(lambda: None, name="build_native", outputs=["tool.exe"])
        cfg = value("build-config", "opt=1")

        task = registry.register(
            lambda: None,
            name="package",
            inputs=[cfg, "assets/icon.png", "build_native"],
        )
        registry.finalize()

        assert task.input_objects == (cfg,)
        assert task.inputs == (Path("assets/icon.png"),)
        assert task.depends == ("build_native",)

    def test_duplicate_id_across_different_objects_errors_with_sites(self) -> None:
        from pymake import value

        registry = TaskRegistry()
        a = value("build-config", "one")
        b = value("build-config", "two")
        registry.register(lambda: None, name="t1", inputs=[a])

        with pytest.raises(ValueError) as exc:
            registry.register(lambda: None, name="t2", inputs=[b])

        message = str(exc.value)
        assert "build-config" in message
        # Both definition sites are named.
        assert message.count("task_test.py") == 2

    def test_reusing_one_object_across_tasks_is_fine(self) -> None:
        from pymake import value

        registry = TaskRegistry()
        shared = value("build-config", "x")
        registry.register(lambda: None, name="t1", inputs=[shared])
        registry.register(lambda: None, name="t2", inputs=[shared])

    def test_missing_id_on_custom_input_is_a_registration_error(self) -> None:
        class NoId:
            def fingerprint(self) -> str:
                return "fp"

        registry = TaskRegistry()
        with pytest.raises(ValueError, match="missing or empty id"):
            registry.register(
                lambda: None,
                name="t",
                inputs=[NoId()],  # type: ignore[list-item]
            )

    def test_empty_id_on_custom_input_is_a_registration_error(self) -> None:
        class EmptyId:
            id = ""

            def fingerprint(self) -> str:
                return "fp"

        registry = TaskRegistry()
        with pytest.raises(ValueError, match="missing or empty id"):
            registry.register(lambda: None, name="t", inputs=[EmptyId()])

    def test_unsupported_input_type_errors(self) -> None:
        registry = TaskRegistry()
        with pytest.raises(ValueError, match="unsupported input"):
            registry.register(lambda: None, name="t", inputs=[42])  # type: ignore[list-item]

    def test_id_namespace_does_not_clobber_paths_or_tasks(self) -> None:
        """A user id can equal a task name or a path with no interference."""
        from pymake import value

        registry = TaskRegistry()
        registry.register(lambda: None, name="build_native", outputs=["tool.exe"])
        same_name = value("build_native", "not the task")

        task = registry.register(
            lambda: None,
            name="package",
            inputs=[same_name, "build_native"],
        )
        registry.finalize()

        assert task.input_objects == (same_name,)
        assert task.depends == ("build_native",)

    def test_clear_resets_the_id_registry(self) -> None:
        from pymake import value

        registry = TaskRegistry()
        registry.register(lambda: None, name="t1", inputs=[value("cfg", "one")])
        registry.clear()
        # Same id, different object: fine after clear().
        registry.register(lambda: None, name="t1", inputs=[value("cfg", "two")])
