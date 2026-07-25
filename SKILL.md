---
description: Python Makefile alternative with dependency tracking, tree_digest change detection, and parallel execution. Use for build tasks, setup verification, and incremental rebuilds.
---

# pymake

A Python Makefile alternative with dependency tracking and parallel execution.

## Installation

### From PyPI

```bash
# Run directly without installing
uvx --from hayeah-pymake pymake --help

# Or install globally
uv tool install hayeah-pymake
pymake --help
```

### Local Development

```bash
# Editable install for development
uv pip install -e .

# Or as a global editable tool (source changes take effect immediately)
uv tool install -e .
```

## Project Dependencies via uv

A Makefile.py that needs third-party imports declares them in an adjacent
`pyproject.toml` and opts in with a `[tool.pymake]` table:

```toml
# pyproject.toml, next to Makefile.py
[project]
name = "myproject-build"
version = "0"
requires-python = ">=3.12"
dependencies = ["maxminddb>=2.6"]

[tool.uv]
package = false        # deps-only project; nothing to build/install

[tool.pymake]          # opt-in: pymake bootstraps `uv run --project` here
```

With that in place, a bare `pymake <task>` re-execs itself under
`uv run --project <dir>` before loading the Makefile — uv syncs the project venv
(creating `.venv/` and `uv.lock`) and every `import` inside tasks resolves against
it. pymake injects **itself** into that run (`--with-editable <checkout>` when
installed editable, else `--with hayeah-pymake==<ver>`), so the project does *not*
list pymake in its dependencies.

Details:

- The guard env `PYMAKE_UV_PROJECT=<abs dir>` marks the active project. It is
  dir-valued, so a task that shells out to `pymake -C <other-project>` still
  bootstraps the *other* project's env.
- `PYMAKE_NO_UV=1` disables the bootstrap.
- No `uv` on PATH or no `[tool.pymake]` table → plain behavior, no re-exec.
- Gitignore `.venv/`; commit `uv.lock` for reproducible task deps.

## Complete Example

Here's a typical `Makefile.py` showing common patterns for a data processing pipeline:

```python
"""Data processing pipeline with pymake.

Run with: pymake
List tasks: pymake list
"""

from pathlib import Path

from pymake import sh, task

# Configuration
OUTPUT_DIR = Path("output")
DATA_DIR = Path("data")

# Output files
RAW_DATA = OUTPUT_DIR / "raw.json"
PROCESSED = OUTPUT_DIR / "processed.json"
STATS = OUTPUT_DIR / "stats.json"
REPORT = OUTPUT_DIR / "report.html"
DATABASE = OUTPUT_DIR / "data.db"


# Task with outputs only: runs if output is missing
@task(outputs=[RAW_DATA])
def fetch():
    """Download raw data from API."""
    sh(f"curl -o {RAW_DATA} https://api.example.com/data")


# Multiple outputs: both files are produced together
@task(inputs=[RAW_DATA], outputs=[PROCESSED, STATS])
def process():
    """Transform raw data and compute statistics."""
    sh(f"python scripts/transform.py {RAW_DATA} {PROCESSED} {STATS}")


# Depend on one output: still runs process, which produces both PROCESSED and STATS
@task(inputs=[PROCESSED], outputs=[DATABASE])
def load_db():
    """Load processed data into SQLite database."""
    sh(f"python scripts/load_db.py {PROCESSED} {DATABASE}")


# Mix file and task inputs: STATS is a file, load_db is a task
@task(inputs=[STATS, load_db], outputs=[REPORT])
def report():
    """Generate HTML report with statistics."""
    sh(f"python scripts/report.py {DATABASE} {STATS} {REPORT}")


# Meta task: no body, just ensures dependencies run
@task(inputs=[report])
def pipeline():
    """Run full pipeline: fetch → process → load → report."""
    pass


# Phony task: no outputs, so it always runs when invoked
@task()
def lint():
    """Run code linting."""
    sh("ruff check scripts/")


@task()
def test():
    """Run tests."""
    sh("pytest tests/")


@task(inputs=[lint, test])
def check():
    """Run all checks (lint + test)."""
    pass


# Default task: runs when pymake is invoked without arguments
task.default(pipeline)
```

Run tasks:

```bash
pymake                      # Run default task (pipeline)
pymake check                # Run the check task
pymake lint test            # Run multiple tasks
pymake -B fetch             # Force re-run even if up-to-date
pymake output/report.html   # Run by output file (runs report task)
```

List available tasks:

```bash
$ pymake list
Tasks:
  pipeline (default) - Run full pipeline: fetch → process → load → report.
  check - Run all checks (lint + test).
  fetch - Download raw data from API.
  lint - Run code linting.
  load_db - Load processed data into SQLite database.
  process - Transform raw data and compute statistics.
  report - Generate HTML report with statistics.
  test - Run tests.
```

Trace dependencies for a task:

```bash
$ pymake which report
report
├── ← output/stats.json
├── → output/report.html
└── load_db
    ├── ← output/processed.json
    ├── → output/data.db
    └── process
        ├── ← output/raw.json
        ├── → output/processed.json
        ├── → output/stats.json
        └── fetch
            └── → output/raw.json
```

Tasks that would rerun (based on timestamps) are shown in red with `(*)` marker.

Key patterns demonstrated:

- **Configuration at top**: Centralize paths and settings
- **Explicit I/O**: Declare `inputs` and `outputs` for dependency tracking
- **Multiple outputs**: A task can produce several files; depending on one runs the whole task
- **Mixed inputs**: Combine file paths and task functions in `inputs`
- **Phony tasks**: Omit outputs for tasks that always run (e.g., `lint`, `test`)
- **Meta tasks**: Use task functions as inputs for aggregation (e.g., `pipeline`, `check`)
- **Default task**: Set with `task.default()` for `pymake` with no arguments

## Task Definition

### Task groups: a class as a namespace of tasks

Tasks that share context — paths, injected services, a change digest — are
naturally a class: the instance carries the context, the methods are the task
bodies. Register a **bound method** by passing it to `task(...)` directly; the
task name is inferred as `<ClassName>.<method>`:

```python
from pymake import sh, task


class Windows:
    """Everything Windows.* — the instance carries the platform context."""

    def __init__(self, out_dir: Path, digest):
        self.out_dir = out_dir
        self.digest = digest
        self.installer = out_dir / "app.msi"

    def build_app(self, type: str = "release"):
        """Compile the app for Windows."""
        sh(f"./build.sh --target windows --type {type} -o {self.installer}")

    def sign_app(self):
        """[needs a signing cert] Sign the installer."""
        sh(f"./sign.sh {self.installer}")


windows = Windows(Path("dist/windows"), tree_digest("src", digest=".build/win.digest"))

task(windows.build_app, outputs=[windows.installer], run_if=windows.digest.changed)
task(windows.sign_app, inputs=[windows.build_app])
# → registers Windows.build_app and Windows.sign_app
```

```bash
pymake Windows.sign_app
```

Rules:

- The **first positional argument** decides the form. A callable registers
  immediately (this is `task.register` with name inference); anything else
  returns the familiar `@task(inputs=..., outputs=...)` decorator, which is
  unchanged and still the right spelling for loose module-level tasks.
- Same keyword vocabulary as the decorator: `inputs`, `outputs`, `run_if`,
  `run_if_not`, `touch`, `name`.
- The namespace is the **runtime** class name, verbatim — a subclass renames
  the whole group, and an inherited method lands in the subclass's namespace.
  Name the class after the namespace and the CLI name is the source spelling.
- `name="Apple.build_lib"` is the full-name escape hatch; it skips inference
  entirely, so a task can live on one instance under another namespace.
- Lambdas and functions defined inside other functions cannot be named
  automatically — pass `name=`.
- Every argument is a **plain expression evaluated after construction**:
  `run_if=windows.digest.changed` is already the bound predicate, and
  `outputs=[windows.installer]` is already this instance's artifact.

The class itself stays completely ordinary — no decorator on methods, no base
class, no metaclass, no pymake import in its module. Calling
`Windows(...).build_app()` directly just runs the body, which makes the class
the natural unit of testing: construct it against fakes and assert on what it
did, with no registry or executor involved. The `task(...)` calls in
`Makefile.py` are the only place pymake learns the class exists.

**Parallel execution**: `-j` runs sibling tasks on threads that share one
instance. Treat instance state as frozen after `__init__` (path catalogs,
injected services) and keep per-run mutable state in locals — the same
discipline module globals already require.

### Task groups with explicit names: `task.group(...)`

Inference covers the common case; `task.group(namespace=..., sep=".")` covers
the rest. It returns a small registrar whose `.task(...)` takes exactly the
same arguments, naming tasks `<namespace><sep><method>`:

```python
group = task.group(namespace="build", sep="_")
group.task(windows.build_app, outputs=[windows.installer])   # → build_app
```

Use it for:

- **Parameterized groups** — two instances of one class would infer the same
  name and collide (the duplicate-name error says so). Only the wiring knows
  which is which:

  ```python
  class Probe:
      def build_probe(self):
          ...

  mac_probe = Probe(targets=("aarch64-apple-darwin",), out=MAC_PROBE)
  win_probe = Probe(targets=("x86_64-pc-windows-gnu",), out=WIN_PROBE)

  task.group(namespace="Macos").task(mac_probe.build_probe, outputs=[MAC_PROBE])
  task.group(namespace="Windows").task(win_probe.build_probe, outputs=[WIN_PROBE])
  ```

- **Converting an existing flat-name Makefile** — `sep="_"` reproduces
  today's `namespace_verb_output` names byte-for-byte, so a Makefile can move
  to classes with zero renames; switching to inferred dotted names is then an
  independent change.

`namespace` is a single identifier (no dots — one namespace level) and `sep`
is `"."` or `"_"`. The registrar holds no registry state, so two registrars
for the same namespace are fine.

### String task references

A dependency can be written as a **quoted task name** instead of a callable:

```python
task(windows.build_app, inputs=["Common.build_assets", Path("src/main.c")])
```

- A `str` input that exactly matches a registered task name becomes a task
  dependency; every other string, and **every `Path` object unconditionally**,
  is a file. Task names contain no `/`, so real paths never collide — pass
  `Path(...)` for files and quoted names for tasks when in doubt.
- String references are resolved after the whole Makefile has been imported,
  so they are **forward references**: registration order does not matter, and
  they are the only spelling that crosses modules without an import. Bound
  methods (`inputs=[windows.build_app]`) are resolved at registration and
  therefore must already be registered — but they are instance-precise, so
  two instances of one class can never be confused.
- The name must match the *registered* name — under a `sep="_"` group that is
  `"build_app"`, not `"Build.app"`.
- A dotted, slash-free string that matches no task and no file is reported as
  `no task and no file named 'Common.build_assets' — is its group
  registered?`, both at run time and by `pymake doctor`.

`task.default(...)` accepts either spelling: `task.default("Windows.build_app")`
or `task.default(windows.build_app)`.

### Task vars

Task parameters are first-class vars. Declare them in the function signature:

```python
from pathlib import Path
from pymake import sh, task


@task(outputs=[Path("build/app")])
def build(optimize: bool = False, target: str = "x86_64"):
    """Compile."""
    sh(f"gcc -O{'2' if optimize else '0'} -march={target} -o build/app main.c")


@task(inputs=[build])
def deploy(env: str | None = None, port: int = 8080):
    """Deploy."""
    sh(f"./deploy.sh --env {env} --port {port}")
```

Rules:
- Supported var types: `str`, `int`, `float`, `bool`, `Path`, and optional forms like `str | None`
- Every var must have a default value, or be optional (e.g. `env: str | None`)
- `*args` and `**kwargs` are not allowed in task signatures

Set vars from a TOML file — one section per task, including namespaced ones:

```toml
[build]
optimize = true

[deploy]
env = "production"
port = 443

[Windows.build_app]     # or the quoted form: ["Windows.build_app"]
type = "release"
```

```bash
pymake deploy --vars-file vars/prod.toml
# or:
PYMAKE_VARS_FILE=vars/prod.toml pymake deploy
```

Set vars from CLI overrides:

```bash
# Qualified: the LAST dot-separated segment is the var name, everything
# before it is the task name (so namespaced tasks work unchanged).
pymake deploy --vars deploy.port=9090 --vars deploy.env=staging
pymake Windows.build_app --vars Windows.build_app.type=dev

# Naked: a key with no dot sets that var on every target NAMED on the
# command line that declares it.
pymake Windows.build_app --vars type=dev
pymake Windows.build_app Macos.build_app --vars type=dev   # sets both
```

Rules for `--vars`:

- Split on the first `=`, then on the **last** dot of the key. Var names are
  Python identifiers and never contain dots, so this is purely positional;
  dots in the *value* are untouched.
- A naked key applies only to explicit targets, never to their dependencies —
  vars are per-task. If no named target declares the var, that is an error.
- There is no bulk-JSON form (`--vars 'deploy={"env":"prod"}'`); use a vars
  file section, or one `--vars` per var.

Precedence is:

```
function defaults < vars file < --vars overrides
```

`pymake list` shows vars and defaults for each task:

```text
Tasks:
  build  - Compile.
           vars: optimize (bool=false), target (str="x86_64")
  deploy - Deploy.
           vars: env (str?), port (int=8080)
```

### Touch files

Use `touch` for tasks that don't produce output files but should track execution:

```python
@task(touch="build/.lint-done")
def lint():
    """Run linter."""
    sh("ruff check src/")
```

The touch file is created after the task runs and acts as an output for dependency tracking.

### Dynamic registration

```python
from pathlib import Path
from pymake import task

for src in Path("src").glob("*.c"):
    obj = Path("build") / (src.stem + ".o")

    def run(s=src, o=obj):
        sh(f"gcc -c {s} -o {o}")

    task.register(
        run,
        name=f"cc:{src}",
        inputs=[src],
        outputs=[obj],
    )
```

**Note:** Use default arguments (`s=src, o=obj`) to capture loop variables. Without this, all tasks would reference the final loop values due to Python's closure semantics.

## Execution Semantics

A task runs if **any** of these conditions are true (checked in order):

1. **Force flag**: `-B` or `--force` was specified
2. **Phony target**: Task has no outputs (and no `touch` file)
3. **Missing output**: Any output file does not exist
4. **Stale output**: Any input file is newer than the oldest output file

A task is **skipped** if:

- All outputs exist AND no inputs are defined (nothing to compare)
- All outputs exist AND all inputs are older than the oldest output
- `run_if` callback returns `False` (checked after file conditions)

### Timestamp comparison

When comparing timestamps:
- pymake uses the **oldest** output file's mtime
- If **any** input is newer than this, the task runs

### Input/Output validation

pymake enforces strict validation of input and output files:

1. **Before execution**: Each input file must either exist OR have a task that produces it. If neither is true, an error is raised immediately.

2. **At task execution**: All input files must exist when a task runs. If a producing task failed to create its outputs, dependent tasks will error.

3. **After task execution**: All declared output files must exist after the task completes (excluding `touch` files, which are created automatically by pymake).

## Custom Conditions

Use `run_if` for additional conditions after dependency checks:

```python
def should_deploy():
    return os.environ.get("DEPLOY") == "1"

@task(run_if=should_deploy)
def deploy():
    sh("./deploy.sh")
```

Use `run_if_not` for the inverse (skip if condition is true):

```python
def is_ci():
    return os.environ.get("CI") == "1"

@task(run_if_not=is_ci)
def local_only():
    """Only runs locally, skipped in CI."""
    sh("./local-setup.sh")
```

`--force` / `-B` bypasses both `run_if` and `run_if_not` (force means force).

## Directory Change Detection: `tree_digest`

`tree_digest` fingerprints a set of files/directories via mtime+size (the
rsync trick) and persists the fingerprint to a caller-specified digest file.
Use it as a `run_if` predicate for tasks that depend on entire source trees,
where listing every file as an `input=` would be impractical:

```python
from pymake import task, tree_digest, sh

web_sources = tree_digest(
    "webreader/src",
    "webreader/package.json",
    digest=".build/web_bundle.digest",
)

@task(run_if=web_sources.changed)
def web_bundle():
    """Rebuild webreader only when something under webreader/ changed."""
    sh("cd webreader && pnpm exec vp build")
    sh("cp -r webreader/dist/ resources/webcontent/")
```

- On the first run (digest file missing) `changed()` returns `True` and the
  task runs. After a successful run, pymake automatically calls
  `web_sources.commit()` to write the new fingerprint — no separate
  `touch=` marker needed; the digest file IS the marker.
- On subsequent runs with an untouched tree, `changed()` returns `False`
  and the task is skipped (`[skip] web_bundle (run_if returned False)`).
- `tree_digest` stores only the final digest text at the caller-supplied
  `digest=` path. There is no sidecar SQLite cache or project-local
  metadata database to manage.
- `pymake -B` forces the task to run even when the digest is unchanged.
- Directory walking respects `.gitignore` and a builtin ignore list
  (`node_modules/`, `__pycache__/`, `.venv/`, …) so you don't need to spell
  out every junk dir in `exclude=`. Pymake also excludes its own `.pymake/`
  bookkeeping directory from tree digests.

Pitfalls to avoid:

- **Don't track a task's output directory** in the same digest you gate that
  task on. `changed()` caches the pre-body snapshot the first time it's
  called, and `commit()` (which runs *after* the task body) writes that
  cached snapshot — so the digest never sees the body's writes, and the next
  invocation always sees a diff and re-runs. Track inputs only; let the
  digest file itself be the output marker.
- **Don't track lockfiles** like `pnpm-lock.yaml`, `package-lock.json`, or
  `uv.lock`. Package managers re-touch them (mtime bump) on every install,
  even on a no-op, so the digest flips every run. Track `package.json` /
  `pyproject.toml` instead, or gate the install task on the same digest so
  the lockfile-toucher doesn't run either.

Parameters:

- `*paths` — files and/or directories to watch.
- `digest=...` — **required**. Path to the digest file. Pick a location
  that's already gitignored — typically next to your build output or in a
  generic build dir (e.g. `.build/web.digest`). There is no default; the
  caller always specifies the path.
- `exclude=[...]` — extra exclude patterns layered on top of the defaults.
- `globs=[...]` — optional include filter, e.g. `["**/*.ts", "**/*.tsx"]`.

Installation: directory walking is provided by `pymake.lstree`, which is
vendored into pymake — no extra install needed. Fingerprint hashing prefers
`xxhash` (install with `uv pip install xxhash` for the fastest path) and
falls back to stdlib `hashlib.blake2b`.

## CLI Reference

```
pymake [options] [command] [targets...]

Commands:
  list [--all]       List tasks with docstrings (--all includes dynamic tasks)
  graph <target>     Output DOT graph of dependencies
  which <target>     Show dependency tree for a task or output file
  redo <target>      Force re-run a target and its dependents
  doctor [target]    Check for dependency issues
  clean [target]     Clean output files of tasks
  run <targets>      Run specified targets
  help               Show help

Options:
  -f, --file FILE    Makefile path (default: Makefile.py)
  -C, --directory DIR  Change to DIR before doing anything
  -p, --parallel     Enable parallel execution
  -j, --jobs N       Number of parallel workers
  -B, --force        Force rerun all tasks
  -q, --quiet        Suppress output
  --vars-file FILE   Load task vars from TOML file (or PYMAKE_VARS_FILE)
  --vars KEY=VALUE   Override vars: <task>.<var>=value, or a bare <var>=value
                     applying to the named targets; repeatable

Shorthand:
  pymake build       Same as: pymake run build
  pymake build test  Same as: pymake run build test

Force subcommand mode:
  pymake -- <command> [options]   Always run as subcommand (-- must be first)

Examples:
  pymake -C subproject build      # Run build in subproject directory
  pymake -f custom.py build       # Use custom.py instead of Makefile.py
  pymake graph build | dot -Tpng > deps.png   # Generate dependency graph
  pymake -- list                  # Force 'list' subcommand even if 'list' task exists
  pymake -- -f other.py list      # Force subcommand mode with options after --
```

### which command

Show the dependency tree for a task or output file. Tasks that would rerun are shown in red.

```bash
pymake which fetch           # Show dependencies of the 'fetch' task
pymake which output/raw.json # Show dependencies by output file
pymake which -d fetch        # Show tasks that depend on 'fetch' (--dependents)
```

Options:
- `-d, --dependents`: Show tasks that depend on the target instead of its dependencies

### redo command

Force re-run a target task. By default, also re-runs all tasks that depend on it.

```bash
pymake redo fetch            # Re-run fetch and all tasks that depend on it
pymake redo --only fetch     # Re-run only fetch, not its dependents
```

Options:
- `--only`: Only redo the target task, not its dependents. Warns if the task was skipped due to a `run_if` condition.

### doctor command

Check for dependency issues without running any tasks. Reports all problems found.

```bash
pymake doctor              # Check all tasks
pymake doctor build        # Check only tasks needed for 'build'
```

Reports cyclic dependencies, inputs that don't exist and no task produces, and
string task references (`"Ns.method"`) that match no task and no file.

### clean command

Remove output files produced by tasks. Use this instead of writing custom clean tasks.

```bash
pymake clean fetch              # Clean output files of 'fetch' task
pymake clean --up report        # Clean report and all upstream dependencies
pymake clean --down fetch       # Clean fetch and all downstream dependents
pymake clean --all              # Clean all known output files
pymake clean --dry --all        # Dry run: show what would be deleted
```

Options:
- `--up`: Also clean output files of dependencies (upstream tasks)
- `--down`: Also clean output files of dependents (downstream tasks)
- `--all`: Clean all known output files from all tasks
- `--dry`: Show what would be deleted without actually deleting

**Note for AI assistants:** Do not create custom clean tasks in Makefile.py. Use the built-in `pymake clean` command instead.

## Shell Utility

The `sh()` function runs shell commands:

```python
from pymake import sh

sh("echo hello")                    # Output to terminal
output = sh("cat file", capture=True)  # Capture output
sh("might-fail", check=False)       # Don't raise on error
```

## Library Use: `pymake.context(cwd=...)`

For ad-hoc or parameterized pipelines that don't fit the `Makefile.py`
model, build a disposable `TaskContext` inside a regular Python function.
The context holds its own registry — nothing leaks into the global
`pymake.task` singleton, so multiple contexts can coexist in one process.

```python
from pathlib import Path
import pymake

def pipeline(root: Path, greeting: str) -> pymake.TaskContext:
    (root / "name.txt").write_text("world")
    ctx = pymake.context(cwd=root)  # relative inputs/outputs resolve here
    task = ctx.task                 # alias so the body mirrors Makefile.py

    @task(inputs=["name.txt"], outputs=["greet.txt"])
    def greet():
        name = (root / "name.txt").read_text().strip()
        (root / "greet.txt").write_text(f"{greeting}, {name}!\n")

    @task(inputs=["greet.txt"], outputs=["shout.txt"])
    def shout():
        (root / "shout.txt").write_text(
            (root / "greet.txt").read_text().upper()
        )

    ctx.default(shout)
    return ctx

ctx = pipeline(Path("/tmp/demo"), "Hello")
ctx.run()                       # runs the default (shout) + its deps
ctx.run(force=True)             # re-run everything
ctx.run(force_from="greet")     # re-run greet and its downstream
ctx.run(dry_run=True)           # print the plan, don't execute
```

The `task = ctx.task` alias is deliberate: `TaskContext` is designed to be
**syntactically compatible** with the module-level `Makefile.py` idiom, so a
function body written against `ctx` reads identically to a `Makefile.py`
body — same decorator shape, same `sh(...)` usage, same
`@task(inputs=..., outputs=...)` kwargs. With only the one-line prelude, a
`Makefile.py` body can be copy-pasted into a `context(cwd=...)` function and
back. Future additions to `TaskContext` should preserve this invariant.

The context API mirrors the CLI: `ctx.task` is the decorator (with
`.register(...)` for dynamic cases), `ctx.default(target)` picks the
default, and `ctx.which(target)` / `ctx.graph(target)` / `ctx.clean(target)`
provide the same introspection surface as `pymake which / graph / clean`.
Relative paths resolve against `ctx.cwd`; absolute paths pass through.

Task groups work the same way here: `ctx.task(instance.method, ...)` registers
with the inferred `<ClassName>.<method>` name, and `ctx.task.group(...)`
returns the same registrar. String **task** references are the one exception —
a context resolves every string input as a path against `ctx.cwd`, so depend on
tasks by bound method (`inputs=[pipeline.fetch]`) inside a context.

See `example/hello_context.py` for a runnable demo.

## Error Handling

- Cyclic dependencies are detected and reported
- Duplicate output files across tasks raise an error
- Duplicate task names raise an error; when two instances of one class infer
  the same name, the message points at `task.group(namespace=...)`
- An unresolved `"Ns.method"` string input is reported as a missing task
  reference, not a missing file
- Task failures stop execution and report the error
- Missing input files (not produced by any task) raise `UnproducibleInputError`
- Input files that don't exist at execution time raise `MissingInputError`
- Output files not created by a task raise `MissingOutputError`
