"""Task variable parsing and resolution."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

from .task import Task, TaskVar


@dataclass(frozen=True)
class ParsedVarsEntry:
    """Parsed --vars entry."""

    original: str
    task_name: str
    var_name: str | None
    value: Any


def parse_vars_entry(entry: str) -> tuple[str, str | None, Any]:
    """Parse a --vars entry into (task_name, var_name_or_none, value)."""
    key, separator, raw_value = entry.partition("=")
    if separator == "":
        raise ValueError(f"Invalid --vars entry: {entry!r} (missing '=')")
    if not key:
        raise ValueError(f"Invalid --vars entry: {entry!r} (missing key)")

    if "." in key:
        task_name, _, var_name = key.partition(".")
        if not task_name or not var_name:
            raise ValueError(f"Invalid --vars entry: {entry!r}")
        return (task_name, var_name, raw_value)

    parsed = json.loads(raw_value)
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Invalid --vars entry: {entry!r} (bulk value must be a JSON object)"
        )
    return (key, None, parsed)


class VarsResolver:
    """Resolve task vars from defaults, vars file, and --vars overrides."""

    def __init__(
        self,
        vars_file: Path | None = None,
        vars_overrides: list[str] | None = None,
        *,
        output: TextIO | None = None,
    ) -> None:
        self.vars_file = vars_file
        self.vars_overrides = vars_overrides or []
        self.output = output or sys.stderr
        self._warned_unknown_tasks: set[str] = set()
        self._vars_file_values = self._load_vars_file(self.vars_file)
        self._parsed_overrides = [self._parse_override(v) for v in self.vars_overrides]

    def validate_tasks(self, tasks: Sequence[Task]) -> None:
        """Validate task names in vars file and --vars entries."""
        known_task_names = {task.name for task in tasks}

        for task_name in sorted(self._vars_file_values):
            if (
                task_name not in known_task_names
                and task_name not in self._warned_unknown_tasks
            ):
                self._warned_unknown_tasks.add(task_name)
                print(
                    f"Warning: vars file has unknown task section [{task_name}]",
                    file=self.output,
                )

        for entry in self._parsed_overrides:
            if entry.task_name not in known_task_names:
                raise ValueError(
                    f"--vars entry {entry.original!r} references unknown task "
                    f"'{entry.task_name}'"
                )

    def resolve(self, task: Task) -> dict[str, Any]:
        """Resolve kwargs for a task using defaults < vars file < --vars."""
        resolved = {var.name: var.default for var in task.vars}
        vars_by_name = {var.name: var for var in task.vars}

        file_values = self._vars_file_values.get(task.name)
        if file_values is not None:
            self._apply_mapping(
                task_name=task.name,
                resolved=resolved,
                vars_by_name=vars_by_name,
                values=file_values,
                source=f"vars file [{task.name}]",
            )

        for entry in self._parsed_overrides:
            if entry.task_name != task.name:
                continue
            if entry.var_name is None:
                self._apply_mapping(
                    task_name=task.name,
                    resolved=resolved,
                    vars_by_name=vars_by_name,
                    values=entry.value,
                    source=f"--vars {entry.original}",
                )
                continue

            var = self._lookup_var(task.name, vars_by_name, entry.var_name)
            resolved[var.name] = self._coerce_from_string(task.name, var, entry.value)

        return resolved

    def _parse_override(self, entry: str) -> ParsedVarsEntry:
        task_name, var_name, value = parse_vars_entry(entry)
        return ParsedVarsEntry(
            original=entry,
            task_name=task_name,
            var_name=var_name,
            value=value,
        )

    def _load_vars_file(self, path: Path | None) -> dict[str, dict[str, Any]]:
        if path is None:
            return {}
        if not path.exists():
            raise ValueError(f"Vars file not found: {path}")

        with path.open("rb") as f:
            data = tomllib.load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Invalid vars file: {path}")

        result: dict[str, dict[str, Any]] = {}
        for task_name, raw_values in data.items():
            if not isinstance(task_name, str):
                raise ValueError(
                    f"Invalid vars file: task name must be string, got {task_name!r}"
                )
            if not isinstance(raw_values, dict):
                raise ValueError(
                    f"Invalid vars file section [{task_name}]: expected table"
                )
            result[task_name] = dict(raw_values)

        return result

    def _apply_mapping(
        self,
        *,
        task_name: str,
        resolved: dict[str, Any],
        vars_by_name: dict[str, TaskVar],
        values: Mapping[Any, Any],
        source: str,
    ) -> None:
        for raw_name, raw_value in values.items():
            if not isinstance(raw_name, str):
                raise ValueError(
                    f"Task '{task_name}' in {source}: var name must be string, "
                    f"got {type(raw_name).__name__}"
                )
            var = self._lookup_var(task_name, vars_by_name, raw_name)
            resolved[var.name] = self._coerce_typed_value(task_name, var, raw_value)

    def _lookup_var(
        self,
        task_name: str,
        vars_by_name: dict[str, TaskVar],
        var_name: str,
    ) -> TaskVar:
        var = vars_by_name.get(var_name)
        if var is None:
            raise ValueError(f"Task '{task_name}': unknown var '{var_name}'")
        return var

    def _coerce_from_string(self, task_name: str, var: TaskVar, value: str) -> Any:
        target_type = var.type

        if target_type is str:
            return value
        if target_type is int:
            try:
                return int(value)
            except ValueError as e:
                raise self._type_error(task_name, var, value) from e
        if target_type is float:
            try:
                return float(value)
            except ValueError as e:
                raise self._type_error(task_name, var, value) from e
        if target_type is bool:
            lowered = value.lower()
            if lowered == "true":
                return True
            if lowered == "false":
                return False
            raise self._type_error(task_name, var, value)
        if target_type is Path:
            return Path(value)

        raise ValueError(f"Task '{task_name}': unsupported var type {target_type}")

    def _coerce_typed_value(self, task_name: str, var: TaskVar, value: Any) -> Any:
        if value is None:
            if var.is_optional:
                return None
            raise self._type_error(task_name, var, value)

        target_type = var.type
        if target_type is str:
            if isinstance(value, str):
                return value
            raise self._type_error(task_name, var, value)
        if target_type is int:
            if type(value) is int:
                return value
            raise self._type_error(task_name, var, value)
        if target_type is float:
            if type(value) is float:
                return value
            if type(value) is int:
                return float(value)
            raise self._type_error(task_name, var, value)
        if target_type is bool:
            if isinstance(value, bool):
                return value
            raise self._type_error(task_name, var, value)
        if target_type is Path:
            if isinstance(value, str):
                return Path(value)
            raise self._type_error(task_name, var, value)

        raise ValueError(f"Task '{task_name}': unsupported var type {target_type}")

    def _type_error(self, task_name: str, var: TaskVar, value: Any) -> ValueError:
        got = type(value).__name__
        return ValueError(
            f"Task '{task_name}' var '{var.name}': expected {var.type.__name__}, "
            f"got {got} ({value!r})"
        )
