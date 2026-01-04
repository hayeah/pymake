# pymake

A Python Makefile alternative with dependency tracking and parallel execution.

## Installation

```bash
pip install -e .
```

## Quick Start

Create a `Makefile.py` in your project:

```python
from pymake import sh, task

@task(outputs=["build/app"])
def build():
    sh("gcc -o build/app src/*.c")

@task(inputs=["build/app"])
def test():
    sh("./build/app --test")

@task()
def clean():
    sh("rm -rf build")
```

Run tasks:

```bash
pymake build      # Run the build task
pymake test       # Run test (builds first if needed)
pymake -B build   # Force rebuild
pymake -p check   # Run in parallel
```

## Task Definition

### Using the `@task` decorator

```python
@task(inputs=["src/main.c"], outputs=["build/main.o"])
def compile():
    """Compile main.c to object file."""
    sh("gcc -c src/main.c -o build/main.o")
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

### Default task

Set a default task to run when `pymake` is invoked without arguments:

```python
task.default("check")
```

### Meta tasks

Use task functions as inputs to create aggregate tasks:

```python
@task()
def lint():
    sh("ruff check src/")

@task()
def test():
    sh("pytest")

@task(inputs=[lint, test])
def all():
    pass
```

Dependency tasks run in order, each following normal run rules.

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

### Output files

Outputs can be specified via `outputs` or `touch`:

```python
@task(outputs=["build/app"])      # Explicit output file
@task(touch="build/.done")        # Touch file (auto-created after task runs)
@task()                           # Phony - always runs
```

The `touch` file is automatically created after successful execution and counts as an output.

### Timestamp comparison

When comparing timestamps:
- pymake uses the **oldest** output file's mtime
- If **any** input is newer than this, the task runs
- Missing input files are ignored (no error, no trigger)

## Custom Conditions

Use `run_if` for additional conditions after dependency checks:

```python
def should_deploy():
    return os.environ.get("DEPLOY") == "1"

@task(run_if=should_deploy)
def deploy():
    sh("./deploy.sh")
```

## CLI Reference

```
pymake [options] [command] [targets...]

Commands:
  list [--all]       List tasks with docstrings (--all includes dynamic tasks)
  graph <target>     Output DOT graph of dependencies
  run <targets>      Run specified targets
  help               Show help

Options:
  -f, --file FILE    Makefile path (default: Makefile.py)
  -p, --parallel     Enable parallel execution
  -j, --jobs N       Number of parallel workers
  -B, --force        Force rerun all tasks
  -q, --quiet        Suppress output

Shorthand:
  pymake build       Same as: pymake run build
  pymake build test  Same as: pymake run build test
```

## Shell Utility

The `sh()` function runs shell commands:

```python
from pymake import sh

sh("echo hello")                    # Output to terminal
output = sh("cat file", capture=True)  # Capture output
sh("might-fail", check=False)       # Don't raise on error
```

## Dependency Graph

Generate a DOT graph for visualization:

```bash
pymake graph build | dot -Tpng > deps.png
```

## Error Handling

- Cyclic dependencies are detected and reported
- Duplicate output files across tasks raise an error
- Task failures stop execution and report the error
