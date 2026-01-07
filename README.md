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
pip install -e .
```

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


@task()
def clean():
    """Remove all generated files."""
    sh(f"rm -rf {OUTPUT_DIR}")


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
  clean - Remove all generated files.
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
- **Phony tasks**: Omit outputs for tasks that always run (e.g., `lint`, `test`, `clean`)
- **Meta tasks**: Use task functions as inputs for aggregation (e.g., `pipeline`, `check`)
- **Default task**: Set with `task.default()` for `pymake` with no arguments

## Task Definition

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

## CLI Reference

```
pymake [options] [command] [targets...]

Commands:
  list [--all]       List tasks with docstrings (--all includes dynamic tasks)
  graph <target>     Output DOT graph of dependencies
  which <target>     Show dependency tree for a task or output file
  redo <target>      Force re-run a target and its dependents
  run <targets>      Run specified targets
  help               Show help

Options:
  -f, --file FILE    Makefile path (default: Makefile.py)
  -C, --directory DIR  Change to DIR before doing anything
  -p, --parallel     Enable parallel execution
  -j, --jobs N       Number of parallel workers
  -B, --force        Force rerun all tasks
  -q, --quiet        Suppress output

Shorthand:
  pymake build       Same as: pymake run build
  pymake build test  Same as: pymake run build test

Examples:
  pymake -C subproject build      # Run build in subproject directory
  pymake -f custom.py build       # Use custom.py instead of Makefile.py
  pymake graph build | dot -Tpng > deps.png   # Generate dependency graph
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

## Shell Utility

The `sh()` function runs shell commands:

```python
from pymake import sh

sh("echo hello")                    # Output to terminal
output = sh("cat file", capture=True)  # Capture output
sh("might-fail", check=False)       # Don't raise on error
```

## Error Handling

- Cyclic dependencies are detected and reported
- Duplicate output files across tasks raise an error
- Task failures stop execution and report the error
- Missing input files (not produced by any task) raise `UnproducibleInputError`
- Input files that don't exist at execution time raise `MissingInputError`
- Output files not created by a task raise `MissingOutputError`
