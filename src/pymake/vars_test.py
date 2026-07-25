"""Tests for vars.py."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from .task import TaskRegistry
from .vars import VarsResolver, parse_vars_entry


def test_parse_vars_entry_dot_notation() -> None:
    task_name, var_name, value = parse_vars_entry("deploy.port=3000")
    assert task_name == "deploy"
    assert var_name == "port"
    assert value == "3000"


def test_parse_vars_entry_splits_on_last_dot() -> None:
    task_name, var_name, value = parse_vars_entry("Windows.build_app.stamp=3.26.900")
    assert task_name == "Windows.build_app"
    assert var_name == "stamp"
    assert value == "3.26.900"


def test_parse_vars_entry_naked_var() -> None:
    task_name, var_name, value = parse_vars_entry("stamp=3.26.900")
    assert task_name is None
    assert var_name == "stamp"
    assert value == "3.26.900"


def test_parse_vars_entry_requires_equals() -> None:
    with pytest.raises(ValueError, match="missing '='"):
        parse_vars_entry("deploy.port")


def test_parse_vars_entry_bulk_json_removed() -> None:
    with pytest.raises(ValueError, match="bulk JSON form was removed"):
        parse_vars_entry('deploy={"env":"prod","port":443}')


def test_parse_vars_entry_naked_value_may_start_with_brace() -> None:
    task_name, var_name, value = parse_vars_entry("shape={not json")
    assert (task_name, var_name, value) == (None, "shape", "{not json")


def test_resolve_defaults_only() -> None:
    registry = TaskRegistry()

    def deploy(env: str | None = None, port: int = 8080, dry_run: bool = False) -> None:
        pass

    task = registry.register(deploy)
    resolver = VarsResolver()
    assert resolver.resolve(task) == {"env": None, "port": 8080, "dry_run": False}


def test_resolve_vars_file_then_dot_override(tmp_path: Path) -> None:
    vars_file = tmp_path / "prod.toml"
    vars_file.write_text(
        "\n".join(
            [
                "[deploy]",
                'env = "production"',
                "port = 443",
            ]
        )
    )

    registry = TaskRegistry()

    def deploy(env: str | None = None, port: int = 8080) -> None:
        pass

    task = registry.register(deploy)
    resolver = VarsResolver(
        vars_file=vars_file,
        vars_overrides=["deploy.port=9090"],
    )
    resolved = resolver.resolve(task)
    assert resolved == {"env": "production", "port": 9090}


def test_bulk_json_override_is_rejected() -> None:
    with pytest.raises(ValueError, match="bulk JSON form was removed"):
        VarsResolver(vars_overrides=['deploy={"env":"staging","port":3000}'])


def test_resolve_naked_var_binds_to_named_target() -> None:
    registry = TaskRegistry()

    def deploy(env: str | None = None, port: int = 8080) -> None:
        pass

    task = registry.register(deploy)
    resolver = VarsResolver(vars_overrides=["port=3000"], targets=["deploy"])
    resolver.validate_tasks(registry.all_tasks())
    assert resolver.resolve(task) == {"env": None, "port": 3000}


def test_resolve_naked_var_fans_out_to_every_named_target() -> None:
    registry = TaskRegistry()

    def build_app(type: str = "release") -> None:
        pass

    def build_assets(type: str = "release") -> None:
        pass

    def sign_app(type: str = "release") -> None:
        pass

    win = registry.register(build_app, name="Windows.build_app")
    mac = registry.register(build_assets, name="Macos.build_assets")
    # A dependency, not a named target: naked vars never reach it.
    dep = registry.register(sign_app, name="Macos.sign_app")

    resolver = VarsResolver(
        vars_overrides=["type=dev"],
        targets=["Windows.build_app", "Macos.build_assets"],
    )
    resolver.validate_tasks(registry.all_tasks())

    assert resolver.resolve(win) == {"type": "dev"}
    assert resolver.resolve(mac) == {"type": "dev"}
    assert resolver.resolve(dep) == {"type": "release"}


def test_naked_var_declared_by_no_target_raises() -> None:
    registry = TaskRegistry()

    def build_app(type: str = "release") -> None:
        pass

    registry.register(build_app, name="Windows.build_app")
    resolver = VarsResolver(
        vars_overrides=["stamp=3.26.900"], targets=["Windows.build_app"]
    )

    with pytest.raises(ValueError, match="no target declares var 'stamp'"):
        resolver.validate_tasks(registry.all_tasks())


def test_naked_var_ignores_a_target_that_does_not_declare_it() -> None:
    registry = TaskRegistry()

    def build_app(type: str = "release") -> None:
        pass

    def lint() -> None:
        pass

    build = registry.register(build_app, name="Windows.build_app")
    check = registry.register(lint)

    resolver = VarsResolver(
        vars_overrides=["type=dev"], targets=["Windows.build_app", "lint"]
    )
    resolver.validate_tasks(registry.all_tasks())

    assert resolver.resolve(build) == {"type": "dev"}
    assert resolver.resolve(check) == {}


def test_dotted_override_targets_a_namespaced_task() -> None:
    registry = TaskRegistry()

    def build_release(stamp: str = "", type: str = "release") -> None:
        pass

    task = registry.register(build_release, name="Windows.build_release")
    resolver = VarsResolver(
        vars_overrides=[
            "Windows.build_release.stamp=3.26.900",
            "Windows.build_release.type=dev",
        ]
    )
    resolver.validate_tasks(registry.all_tasks())
    assert resolver.resolve(task) == {"stamp": "3.26.900", "type": "dev"}


def test_unknown_var_name_raises() -> None:
    registry = TaskRegistry()

    def deploy(port: int = 8080) -> None:
        pass

    task = registry.register(deploy)
    resolver = VarsResolver(vars_overrides=["deploy.nope=1"])
    with pytest.raises(ValueError, match="unknown var 'nope'"):
        resolver.resolve(task)


def test_type_mismatch_in_naked_var_raises() -> None:
    registry = TaskRegistry()

    def deploy(port: int = 8080) -> None:
        pass

    task = registry.register(deploy)
    resolver = VarsResolver(vars_overrides=["port=not-an-int"], targets=["deploy"])
    with pytest.raises(ValueError, match="expected int"):
        resolver.resolve(task)


def test_type_mismatch_in_dot_notation_raises() -> None:
    registry = TaskRegistry()

    def build(optimize: bool = False) -> None:
        pass

    task = registry.register(build)
    resolver = VarsResolver(vars_overrides=["build.optimize=yes"])
    with pytest.raises(ValueError, match="expected bool"):
        resolver.resolve(task)


def test_path_and_float_coercion_from_toml(tmp_path: Path) -> None:
    vars_file = tmp_path / "vars.toml"
    vars_file.write_text(
        "\n".join(
            [
                "[build]",
                'output = "dist/app"',
                "ratio = 2",
            ]
        )
    )

    registry = TaskRegistry()

    def build(output: Path = Path("build/app"), ratio: float = 1.5) -> None:
        pass

    task = registry.register(build)
    resolver = VarsResolver(vars_file=vars_file)
    resolved = resolver.resolve(task)
    assert resolved["output"] == Path("dist/app")
    assert resolved["ratio"] == 2.0


def test_vars_file_nested_section_flattens_to_dotted_task(tmp_path: Path) -> None:
    vars_file = tmp_path / "vars.toml"
    vars_file.write_text(
        "\n".join(
            [
                "[Windows.build_release]",
                'stamp = "3.26.900"',
                'type = "dev"',
                "",
                "[deploy]",
                "port = 443",
            ]
        )
    )

    registry = TaskRegistry()

    def build_release(stamp: str = "", type: str = "release") -> None:
        pass

    def deploy(port: int = 8080) -> None:
        pass

    release = registry.register(build_release, name="Windows.build_release")
    deployment = registry.register(deploy)

    output = io.StringIO()
    resolver = VarsResolver(vars_file=vars_file, output=output)
    resolver.validate_tasks(registry.all_tasks())

    assert resolver.resolve(release) == {"stamp": "3.26.900", "type": "dev"}
    assert resolver.resolve(deployment) == {"port": 443}
    # The intermediate [Windows] table is not a task section.
    assert "unknown task section" not in output.getvalue()


def test_vars_file_quoted_dotted_section(tmp_path: Path) -> None:
    vars_file = tmp_path / "vars.toml"
    vars_file.write_text('["Windows.build_release"]\ntype = "dev"\n')

    registry = TaskRegistry()

    def build_release(type: str = "release") -> None:
        pass

    task = registry.register(build_release, name="Windows.build_release")
    resolver = VarsResolver(vars_file=vars_file)
    assert resolver.resolve(task) == {"type": "dev"}


def test_vars_file_two_level_nesting_raises(tmp_path: Path) -> None:
    vars_file = tmp_path / "vars.toml"
    vars_file.write_text('[a.b.c]\ntype = "dev"\n')

    with pytest.raises(ValueError, match="one namespace level deep"):
        VarsResolver(vars_file=vars_file)


def test_validate_tasks_warns_unknown_task_in_vars_file(tmp_path: Path) -> None:
    vars_file = tmp_path / "vars.toml"
    vars_file.write_text(
        "\n".join(
            [
                "[deploy]",
                "port = 443",
                "",
                "[ghost]",
                "port = 123",
            ]
        )
    )

    registry = TaskRegistry()
    registry.register(lambda: None, name="deploy")
    output = io.StringIO()
    resolver = VarsResolver(vars_file=vars_file, output=output)
    resolver.validate_tasks(registry.all_tasks())

    assert "unknown task section [ghost]" in output.getvalue()


def test_validate_tasks_errors_on_unknown_task_in_override() -> None:
    registry = TaskRegistry()
    registry.register(lambda: None, name="build")
    resolver = VarsResolver(vars_overrides=["deploy.port=3000"])

    with pytest.raises(ValueError, match="unknown task 'deploy'"):
        resolver.validate_tasks(registry.all_tasks())
