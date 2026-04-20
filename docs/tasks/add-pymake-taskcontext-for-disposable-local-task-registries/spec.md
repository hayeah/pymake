# Add pymake TaskContext for disposable local task registries

## Goal

Expose pymake's task registry + resolver + executor as a library surface so a
caller can build a disposable DAG inside a regular Python function, run it,
and throw it away. The b2cast pipeline variant is the motivating consumer
(out of scope here) — this section lands the pymake side only.

Primary brief:
`/Users/me/Dropbox/boss/tasks/design-b2cast-pipeline-verb-for-standard-subs-thumbs-flow/specs/pymake-pipeline.md`.

Shape from the spec:

```python
ctx = pymake.context(cwd=sid_dir)
task = ctx.task
@task(inputs=[...], outputs=[...])
def prepare(): ...
ctx.default(prepare)
ctx.run(force=..., force_from=..., dry_run=...)
```

Out of scope:
- Any change to the pymake CLI, `Makefile.py` discovery, or the global `task`
  singleton.
- Any b2cast code.

## Architecture

Target repo: `~/github.com/hayeah/pymake`.

New files:

- `src/pymake/context.py` — `TaskContext` class and `context(...)` factory.
- `src/pymake/context_test.py` — unit + smoke tests.
- `example/hello_context.py` — 10-ish-line self-hosted demo (hello-world
  with two tasks + `ctx.run()`).

Modified files:

- `src/pymake/__init__.py` — re-export `context`, `TaskContext`.
- Light edits to `src/pymake/task.py` only if needed for `@task(name=...)`
  support at decorator time (currently `TaskRegistry.__call__` doesn't take
  `name`; the spec's example uses `@ctx.task(name=f"translate:{lang}", ...)`).
  Cleanest fix: add `name=None` to `TaskRegistry.__call__` in one tiny edit
  so the decorator surfaces match.

`TaskContext` API (mirrors the spec):

```python
class TaskContext:
    cwd: Path
    registry: TaskRegistry
    task: _ContextDecorator        # @task(...) and task.register(...)
    def register(func, *, name=None, inputs=(), outputs=(),
                 run_if=None, run_if_not=None, touch=None) -> Task
    def default(task_or_name) -> None
    def run(target=None, *, force=False, force_from=None,
            dry_run=False, parallel=False, jobs=None,
            console=None) -> bool
    def which(target=None, *, dependents=False) -> str
    def graph(target=None) -> str
    def clean(target=None, *, up=False, down=False, dry=False,
              all_tasks=False) -> list[Path]
```

Path resolution: inside `register`, each `inputs=` / `outputs=` / `touch=`
entry is passed through `_resolve(p, cwd)`: `Path(p)` if already absolute,
else `cwd / p`. Task-object inputs (callables) pass through untouched.

`cwd` kwarg name: keeping `cwd` (spec default). `base_dir` / `root` were
alternatives but `cwd` matches the familiar "working dir for this registry"
framing and aligns with Python's `subprocess.run(cwd=...)`. No rename.

### `force_from` semantics

Mirror of the CLI's `pymake redo <target>`: run the requested `target` (or
the default), but every task in the set `{force_from} ∪ transitive_dependents(force_from)`
is forced regardless of freshness. Tasks outside that set run normally.
Mutually exclusive with `force=True` — raise `ValueError` if both given.

### `dry_run`

Prints the execution plan (ordered tasks + a `*` marker for tasks that
*would* run given current `force` / `force_from` state), then returns
without invoking any task body. Implementation: reuse
`DependencyResolver.resolve` for ordering and `Task.should_run` for the
marker; rendered with `rich` (falls back to the `console=` param).

### Internal registry-thread cleanup

REPOMAP already states `Executor`, `DependencyResolver`, `Doctor`, and the
CLI commands all take a `TaskRegistry` arg. A quick grep of `executor.py` /
`resolver.py` / `cli/*` confirms — none of them reach for the global
singleton. The only consumer of the global is `cli/__init__.py`'s
`_load_makefile()` (which we're not touching by design). So this section
needs no threading work; noting the verification as evidence in the
worklog.

## Steps

1. Spec-authoring pass (this file) + seed todos.
2. Add tiny `name=` kwarg to `TaskRegistry.__call__` so the decorator
   supports `@task(name=...)`. Tests for that in `task_test.py`.
3. Write `src/pymake/context.py` — factory, `TaskContext`, path resolver,
   decorator wrapper, `register` / `default` / `run` / `which` / `graph` /
   `clean`. Plumb `force_from` and `dry_run` semantics.
4. Re-export from `src/pymake/__init__.py`.
5. Tests `src/pymake/context_test.py` covering:
   - relative vs absolute path resolution
   - `ctx.run()` happy path (both tasks execute)
   - `force=True` re-runs up-to-date tasks
   - `force_from="X"` forces X + downstream, leaves upstream alone
   - `dry_run=True` does not execute bodies (use a sentinel list in the
     task body to assert nothing ran)
   - two `ctx`s in one process don't leak into each other
   - sub-tasks referenced via `inputs=[other_decorated_fn]` (confirms the
     spec's open-question-4 "falls out of existing behavior")
   - `@ctx.task(name=...)` uses the custom name
6. Smoke example `example/hello_context.py` — printable run transcript.
7. Run the full repo test suite (`pytest -v src/pymake`) and the
   self-hosted `pymake` (`pymake test`) — paste evidence into worklog.

## Verification

- `pytest -v src/pymake` → green, including new `context_test.py`.
- `python example/hello_context.py` → prints the two tasks running once;
  running a second time prints "skip" for the cached task (freshness
  check works against `cwd`).
- A section of the hello-world example that constructs two contexts with
  different `cwd`s, registers tasks with the same name in each, and runs
  both — proves no cross-context leak.

## Open questions

- None blocking. All four of the section's flagged open questions have
  defaults I'll take: `cwd` as kwarg name, `pymake.context` as factory,
  include `which/graph/clean` for v1 symmetry, verify the "sub-tasks as
  inputs=" case via a test.

## Design notes
