# Design: Task Vars

## Motivation

People use environment variables to configure task behavior — choosing build targets, setting deploy environments, toggling flags. This is awkward:

```python
@task()
def deploy():
    env = os.environ.get("DEPLOY_ENV", "staging")
    port = int(os.environ.get("PORT", "8080"))
    sh(f"./deploy.sh --env {env} --port {port}")
```

Problems:

- No discoverability — you have to read the source to find what vars exist
- No type safety — everything is a string, manual parsing
- No validation — typos in var names silently ignored
- No defaults visible in `pymake list`

## Proposal: task function parameters as vars

Add parameters directly to `@task` functions. pymake introspects the signature and wires up values from external sources.

```python
@task(outputs=[BINARY])
def build(optimize: bool = False, target: str = "x86_64"):
    """Compile the project."""
    sh(f"gcc -O{'2' if optimize else '0'} -march={target} -o app main.c")

@task(inputs=[build])
def deploy(env: str | None = None, port: int = 8080):
    """Deploy the application."""
    sh(f"./deploy.sh --env {env} --port {port}")
```

**Constraint**: every var must have a default value or be `Optional` (i.e., `T | None = None`). No required vars — a task must always be runnable with defaults alone. This is enforced at registration time.

## Passing vars

Three mechanisms, in override order (lowest → highest):

### Function defaults (lowest priority)

The default values in the function signature. Always present.

### Vars file

A TOML file where sections correspond to task names:

```toml
[build]
optimize = true

[deploy]
env = "production"
port = 3000
```

Specified via:

```bash
pymake build --vars-file prod.toml
PYMAKE_VARS_FILE=./prod.toml pymake build
```

TOML is a natural fit — native support for `str`, `int`, `float`, `bool`. No quoting gymnastics.

### `--vars` CLI (highest priority)

The `--vars` flag supports two forms, detected by whether the key contains a dot:

**Bulk JSON** — set multiple vars for a task at once:

```bash
pymake deploy --vars 'deploy={"env":"staging","port":3000}'
pymake build --vars 'build={"optimize":true}'
```

Format: `task_name=json_object`. The left side has no dot, so the right side is parsed as a JSON object.

**Dot notation** — set a single var with type-directed parsing:

```bash
pymake deploy --vars deploy.env=staging --vars deploy.port=3000
pymake build --vars build.optimize=true
```

Format: `task.key=value`. The dot in the left side signals single-var mode. The string value is coerced to the var's declared type.

Both forms can be mixed. Multiple `--vars` flags are applied left-to-right (later wins for same key in same task):

```bash
pymake deploy \
  --vars 'deploy={"env":"production","port":443}' \
  --vars deploy.port=9090
# result: env="production", port=9090
```

### Detection rule

Split on the first `=`. Inspect the left side:

- Contains `.` → **dot notation**: `task.key=value`
- No `.` → **bulk JSON**: `task_name={...}`

## Resolution order

For each task var, the resolved value is:

```
--vars override  >  vars file  >  function default
```

If a var is `T | None = None` and no source provides a value, the function receives `None`. The function decides whether that's acceptable.

## Examples

### Basic usage

```python
@task()
def greet(name: str = "world", loud: bool = False):
    """Say hello."""
    msg = f"Hello, {name}!"
    if loud:
        msg = msg.upper()
    print(msg)
```

```bash
pymake greet                                    # Hello, world!
pymake greet --vars greet.name=alice            # Hello, alice!
pymake greet --vars greet.loud=true             # HELLO, WORLD!
```

### Environment-specific config

```toml
# vars/prod.toml
[build]
optimize = true

[deploy]
env = "production"
port = 443
```

```toml
# vars/dev.toml
[deploy]
env = "development"
port = 8080
```

```bash
pymake deploy --vars-file vars/prod.toml
pymake deploy --vars-file vars/dev.toml
```

### Mix vars file with CLI overrides

```bash
# Use prod config but override port
pymake deploy --vars-file vars/prod.toml --vars deploy.port=9090
```

### Vars flow through dependencies

```python
@task(outputs=[BINARY])
def build(optimize: bool = False):
    """Compile."""
    sh(f"gcc -O{'2' if optimize else '0'} -o app main.c")

@task(inputs=[build])
def deploy(env: str | None = None):
    """Deploy."""
    sh(f"./deploy.sh --env {env}")
```

```bash
# Both build and deploy get their vars from the same file
pymake deploy --vars-file prod.toml
```

The vars file applies to **all tasks** in the execution — not just the target. A `[build]` section in the vars file configures `build` even when you run `pymake deploy` (which depends on `build`).

## Type coercion

### From TOML / JSON

TOML and JSON values are natively typed, so coercion is minimal:

| Python annotation | TOML type | Notes |
|---|---|---|
| `str` | string | Direct |
| `int` | integer | Direct |
| `float` | float | Direct; also accepts TOML integer (widens) |
| `bool` | boolean | Direct |
| `Path` | string | `str` → `Path(value)` |
| `T \| None` | T or absent | Unwrap Optional, coerce inner type |

Type mismatches raise a clear error:

```
Error: Task 'build' var 'optimize': expected bool, got str ("yes")
```

### From dot notation (`--vars task.key=value`)

The string value is coerced based on the var's declared type:

| Declared type | Parsing rule |
|---|---|
| `str` | Used as-is |
| `int` | `int(value)` — error if not a valid integer |
| `float` | `float(value)` — error if not a valid number |
| `bool` | `true`/`false` (case-insensitive) — error otherwise |
| `Path` | `Path(value)` |

This is **type-directed** — the declared type in the function signature determines how the string is parsed. No ambiguity.

## Data model

```python
@dataclasses.dataclass
class TaskVar:
    """A var extracted from a task function's signature."""
    name: str
    type: type          # str, int, float, bool, Path
    default: Any        # the default value (always present)
    is_optional: bool   # True if annotation is T | None
```

The `Task` dataclass gains:

```python
@dataclasses.dataclass
class Task:
    name: str
    func: Callable[..., None]         # was Callable[[], None]
    inputs: tuple[Path, ...]
    outputs: tuple[Path, ...]
    vars: tuple[TaskVar, ...] = ()    # new
    # ... existing fields ...
```

## Introspection

```python
import inspect
from pathlib import Path
from typing import get_origin, get_args, Union, UnionType

SUPPORTED_TYPES = {str, int, float, bool, Path}

def vars_from_signature(func: Callable) -> tuple[TaskVar, ...]:
    sig = inspect.signature(func)
    result = []

    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            raise ValueError(
                f"Task '{func.__name__}': *args/**kwargs not supported"
            )

        annotation = p.annotation
        is_optional = False

        # Handle T | None
        if _is_optional(annotation):
            inner = _unwrap_optional(annotation)
            annotation = inner
            is_optional = True

        if annotation is inspect.Parameter.empty:
            annotation = str

        if annotation not in SUPPORTED_TYPES:
            raise ValueError(
                f"Task '{func.__name__}': unsupported type {annotation} "
                f"for var '{p.name}'"
            )

        # Must have a default
        if p.default is inspect.Parameter.empty:
            if not is_optional:
                raise ValueError(
                    f"Task '{func.__name__}': var '{p.name}' "
                    f"must have a default value or be Optional"
                )
            default = None
        else:
            default = p.default

        result.append(TaskVar(
            name=p.name,
            type=annotation,
            default=default,
            is_optional=is_optional,
        ))

    return tuple(result)
```

## Vars resolution

A `VarsResolver` collects values from all sources and produces a `dict[str, Any]` of kwargs for each task:

```python
class VarsResolver:
    def __init__(
        self,
        vars_file: Path | None = None,
        vars_overrides: list[str] | None = None,  # ["task.key=val", "task={json}", ...]
    ): ...

    def resolve(self, task: Task) -> dict[str, Any]:
        """Resolve var values for a task.

        Merge order: defaults < vars_file < vars_overrides.
        Validates types against TaskVar declarations.
        Returns kwargs dict ready to pass to task.func(**kwargs).
        """
        ...
```

### Parsing `--vars` entries

```python
def parse_vars_entry(entry: str) -> tuple[str, str | None, Any]:
    """Parse a --vars entry.

    Returns (task_name, var_name_or_none, value).
    - Dot notation: ("deploy", "port", "9090")  — raw string, coerced later
    - Bulk JSON:    ("deploy", None, {"env": "staging", "port": 3000})
    """
    key, _, raw_value = entry.partition("=")
    if not raw_value and not _:
        raise ValueError(f"Invalid --vars entry: {entry!r} (missing '=')")

    if "." in key:
        task_name, _, var_name = key.partition(".")
        return (task_name, var_name, raw_value)
    else:
        import json
        return (key, None, json.loads(raw_value))
```

## Executor changes

The `Executor` receives a `VarsResolver` and uses it when calling task functions:

```python
class Executor:
    def __init__(self, registry, *, vars_resolver: VarsResolver | None = None, ...):
        self.vars_resolver = vars_resolver or VarsResolver()

    def _execute_task(self, task: Task) -> bool:
        # ... existing checks ...
        kwargs = self.vars_resolver.resolve(task)
        task.func(**kwargs)
        # ...
```

Tasks with no vars behave exactly as before — `kwargs` is empty, `func()` is called with no arguments.

## CLI changes

New global options:

```
--vars-file FILE    Load task vars from TOML file
                    (also: PYMAKE_VARS_FILE env var)
--vars KEY=VALUE    Set vars (repeatable). Two forms:
                      task.var=value   Set one var (type-directed)
                      task={"json"}    Set multiple vars at once
```

These are parsed before target dispatch and passed to the `VarsResolver`.

## `pymake list` output

Vars shown alongside task docs:

```
$ pymake list
Tasks:
  build    - Compile the project.
             vars: optimize (bool=false), target (str="x86_64")
  deploy   - Deploy the application.
             vars: env (str?), port (int=8080)
  fetch    - Download raw data.
  test     - Run tests.
```

`str?` indicates `Optional` (can be `None`).

## Validation

At **registration time** (when `@task` is evaluated):

- All vars must have defaults or be `Optional`
- Type annotations must be in `SUPPORTED_TYPES`
- No `*args` or `**kwargs`

At **execution time** (when vars are resolved):

- TOML/JSON values must match the declared type
- Unknown task names in vars file → warning (not error, since the file may be shared across Makefiles)
- Unknown var names for a known task → error

## File changes

- `src/pymake/task.py` — add `TaskVar`, signature introspection, update `Task.func` type
- `src/pymake/vars.py` — new module: `VarsResolver`, TOML loading, JSON parsing, type validation
- `src/pymake/executor.py` — pass resolved vars to `task.func(**kwargs)`
- `src/pymake/cli/__init__.py` — `--vars-file` and `--vars` global options
- `src/pymake/cli/list_cmd.py` — show vars in listing
- `src/pymake/__init__.py` — export `TaskVar` if needed
- Tests for introspection, TOML loading, JSON parsing, type coercion, override precedence
