# pymake

A Python Makefile alternative with dependency tracking and parallel execution.

## Installation

```bash
pip install -e .
```

## Quick Start

Create a `Makefile.py` in your project:

```python
from pymake import sh

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
    sh("gcc -c src/main.c -o build/main.o")
```

### Dynamic registration

```python
from pathlib import Path
from pymake import task

for src in Path("src").glob("*.c"):
    obj = Path("build") / (src.stem + ".o")

    def make_compile(s=src, o=obj):
        def run():
            sh(f"gcc -c {s} -o {o}")
        return run

    task.register(
        make_compile(),
        name=f"cc:{src}",
        inputs=[src],
        outputs=[obj],
    )
```

## Execution Semantics

| Condition | Behavior |
|-----------|----------|
| No outputs | Always run (phony target) |
| Output missing | Run |
| Input newer than output | Run |
| Output exists, no inputs | Skip |
| `--force` flag | Always run |

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
  list [--all]       List tasks (--all includes dynamic tasks)
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
