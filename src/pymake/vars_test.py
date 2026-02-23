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


def test_parse_vars_entry_bulk_json() -> None:
    task_name, var_name, value = parse_vars_entry('deploy={"env":"prod","port":443}')
    assert task_name == "deploy"
    assert var_name is None
    assert value == {"env": "prod", "port": 443}


def test_parse_vars_entry_requires_equals() -> None:
    with pytest.raises(ValueError, match="missing '='"):
        parse_vars_entry("deploy.port")


def test_parse_vars_entry_bulk_requires_json_object() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        parse_vars_entry("deploy=123")


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


def test_resolve_bulk_json_override() -> None:
    registry = TaskRegistry()

    def deploy(env: str | None = None, port: int = 8080) -> None:
        pass

    task = registry.register(deploy)
    resolver = VarsResolver(vars_overrides=['deploy={"env":"staging","port":3000}'])
    resolved = resolver.resolve(task)
    assert resolved == {"env": "staging", "port": 3000}


def test_resolve_optional_null_from_bulk_json() -> None:
    registry = TaskRegistry()

    def deploy(env: str | None = "staging") -> None:
        pass

    task = registry.register(deploy)
    resolver = VarsResolver(vars_overrides=['deploy={"env":null}'])
    assert resolver.resolve(task) == {"env": None}


def test_unknown_var_name_raises() -> None:
    registry = TaskRegistry()

    def deploy(port: int = 8080) -> None:
        pass

    task = registry.register(deploy)
    resolver = VarsResolver(vars_overrides=["deploy.nope=1"])
    with pytest.raises(ValueError, match="unknown var 'nope'"):
        resolver.resolve(task)


def test_type_mismatch_in_bulk_json_raises() -> None:
    registry = TaskRegistry()

    def deploy(port: int = 8080) -> None:
        pass

    task = registry.register(deploy)
    resolver = VarsResolver(vars_overrides=['deploy={"port":"not-an-int"}'])
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
