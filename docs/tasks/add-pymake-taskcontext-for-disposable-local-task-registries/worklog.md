---
status: done
section: Add pymake TaskContext for disposable local task registries
slug: add-pymake-taskcontext-for-disposable-local-task-registries
mode: worktree
spec: spec.md
created: 2026-04-20T07:51:11Z
---

> ## Add pymake TaskContext for disposable local task registries
>
> ---
> status:
>   type: open
> ---
>
> Implement the pymake feature described in `/Users/me/Dropbox/boss/tasks/design-b2cast-pipeline-verb-for-standard-subs-thumbs-flow/specs/pymake-pipeline.md`. **Only the pymake side** — the b2cast consumer is out of scope for this section.
>
> Primary brief — read this first, it contains the full design, rationale, internal-changes list, and open questions:
>
> - `/Users/me/Dropbox/boss/tasks/design-b2cast-pipeline-verb-for-standard-subs-thumbs-flow/specs/pymake-pipeline.md`
>
> Target repo: `~/github.com/hayeah/pymake` (source under `src/pymake/`).
>
> Deliverables:
>
> - New `pymake.context(cwd=...)` factory + `TaskContext` class as specified (`.task` decorator bound to the context, `.register`, `.default`, `.run` with `force` / `force_from` / `dry_run` / `parallel` / `jobs` / `console`).
> - Relative-path resolution against `ctx.cwd` for `inputs=` / `outputs=`; absolute paths pass through unchanged.
> - No changes to the existing CLI, `Makefile.py` discovery, or global `task` decorator — `TaskContext` is purely library-only.
> - Internal cleanup: thread a registry argument wherever `executor.py` / `resolver.py` / `cli/*` reach for the global singleton (spec flagged this as best-effort; REPOMAP suggests most paths already take registries).
> - Tests covering: relative vs absolute path resolution, `.run()` / `force` / `force_from` / `dry_run`, multiple concurrent contexts in one process (no cross-leak), and the "sub-tasks as inputs= entries" case from the open questions.
> - A self-hosted smoke example: a 10-line two-task parameterized "hello world" that exercises the context end-to-end (the spec's "Why do pymake first" section calls this out as the acceptance demo).
>
> Agent's call on open questions (spec has defaults, override only with reason):
>
> - Kwarg name: default `cwd`; `base_dir` / `root` / `rel_to` are alternatives — pick whichever reads cleanest once you've written the tests.
> - Factory name: default `pymake.context`; `scope` / `local_tasks` / `registry` are alternatives — default unless something forces your hand.
> - `ctx.graph()` / `.which()` / `.clean()` symmetry: spec says "probably yes for v1, cheap since resolver exists". Include them unless they balloon the diff — then note the skip in the trouble report.
> - Sub-tasks referenced by decorated function in another task's `inputs=`: the spec says this should fall out of the existing global `task` behavior — confirm with a test rather than explicitly coding it.
>
> Evidence I want to see before lgtm:
>
> - Full pytest green.
> - The hello-world smoke example output (pasted into the worklog evidence).
> - One worked example showing two `pymake.context()` calls in the same process producing independent DAGs (proves the no-global-leak property).
>
> - [ ] implement TaskContext + tests + smoke example

## Todos

- [x] set up worktree + spec
- [x] add `name=` kwarg to `TaskRegistry.__call__` (+ task_test)
- [x] write `src/pymake/context.py` (factory, decorator, path resolver)
- [x] wire `force_from` / `dry_run` / plain run through context
- [x] add `which` / `graph` / `clean` to context
- [x] re-export `context` / `TaskContext` from `__init__.py`
- [x] write `src/pymake/context_test.py` covering all spec cases
- [x] add `example/hello_context.py` smoke demo
- [x] run full test suite + capture evidence
- [x] update README + SKILL.md with the new context surface

## Agent log

- 2026-04-20T14:52Z — Worktree created; spec drafted at `spec.md`; seeded todos.
- 2026-04-20T14:59Z — Landed `8fc0ca3 Add pymake.context library surface for
  disposable task registries`. 220/220 tests green (21 new in
  `context_test.py`). Smoke example `example/hello_context.py` runs two
  contexts with colliding task names in separate cwds — both run independently
  and the second invocation skips as up-to-date. README + SKILL.md updated.

## Boss log

## Evidence

### Agent calls on spec open questions

- Kwarg name: kept `cwd` — matches spec default and aligns with Python's
  `subprocess.run(cwd=...)` mental model.
- Factory name: kept `pymake.context` — reads well, no collision.
- `ctx.which` / `ctx.graph` / `ctx.clean`: included all three. Cost was tiny
  (~30 LoC total) since the resolver and the CLI command logic are already
  there to crib from.
- Sub-task-as-input: confirmed via `TestSubTaskInputs.test_task_as_input_resolves_dependency`.
  No extra code needed — falls out of the existing `TaskRegistry.register`
  behavior (callable entries in `inputs=` become `depends` names).

### Internal registry-thread cleanup — verification

Grepped `executor.py`, `resolver.py`, `doctor.py`, and every file under
`cli/` for references to the module-global `task` singleton. Only
`cli/__init__.py`'s `_load_makefile()` path reaches for it, which we're
expressly not touching (CLI / Makefile.py discovery stays unchanged per
spec). Executor / Resolver / Doctor / individual CLI commands all accept
a `TaskRegistry` via constructor and honor it. No threading work needed.

### Full test suite green

```
$ uv run pytest src/pymake
... (output omitted)
============================= 220 passed in 0.27s ==============================
```

21 of those are new (`src/pymake/context_test.py`), covering:

- relative vs absolute path resolution (+ `touch=` resolution)
- `ctx.run()` happy path, default target dispatch, up-to-date skip
- `force=True` re-runs an up-to-date task
- `force_from="X"` forces X + downstream, leaves upstream alone
- `force=` + `force_from=` mutually exclusive
- `dry_run=True` prints a plan and executes nothing
- two contexts in one process don't leak (neither `pymake.task` nor each
  other)
- sub-task references via `inputs=[decorated_fn]` (open question 4)
- `@ctx.task(name="...")` uses the custom name
- `ctx.which` / `ctx.graph` / `ctx.clean` (incl. `dry=True`)

### Smoke example transcript (`python example/hello_context.py`)

```
=== first run (both pipelines fresh) ===
[run] greet
[run] shout
[run] greet
[run] shout

=== second run (everything up to date) ===
[skip] greet (up to date)
[skip] shout (up to date)
[skip] greet (up to date)
[skip] shout (up to date)

=== results ===
  en: HELLO, WORLD!
  fr: BONJOUR, WORLD!
```

Both pipelines register tasks named `greet` and `shout`. They happily
coexist because each context owns its own `TaskRegistry` — exactly the
no-global-leak property the brief asked for. Second invocation hits the
freshness-check path resolving against each `ctx.cwd` and skips all four
tasks as expected. Archived at
`tmp/150033_178-hello-context-output.txt`.

### Two independent DAGs in one process (also covered as a unit test)

`src/pymake/context_test.py::TestNoCrossLeak::test_two_contexts_independent`
builds two contexts in separate `tmp/A` and `tmp/B` directories, each
with a task named `hello`, and asserts:

- `pymake.task.get("hello")` is `None` after registration (no leak into
  the global singleton)
- each registry sees only its own task
- `a.registry.by_output(b_dir / "out.txt")` is `None` (output maps don't
  cross)
- running each context writes only to its own `cwd`

### Ruff / mypy on the new files

```
$ uv run ruff check src/pymake/context.py src/pymake/context_test.py src/pymake/task.py example/hello_context.py
All checks passed!

$ uv run mypy src/pymake/context.py src/pymake/task.py
# (the 3 errors reported all live in pre-existing src/pymake/vars.py —
#  unchanged by this section)
```

### Commit

```
8fc0ca3 Add pymake.context library surface for disposable task registries
```

Branch: `add-pymake-taskcontext-for-disposable-local-task-registries` in
`~/github.com/hayeah/pymake`. 6 files changed, 815 insertions(+).

## Trouble report

- **SKILL.md ↔ README.md are kept in sync by hand.** Noticed after staging
  only the README change — the repo's last few commits consistently touch
  both together, and SKILL.md is a regular file (not a symlink). Bundled
  the same addition into SKILL.md so they don't drift. Not a bug, just a
  repo convention worth flagging for whoever maintains it.
- **Pre-existing mypy / ruff noise.** Running mypy over the full source
  tree surfaces ~13 errors in unrelated files (`vars.py` tomllib fallback,
  `cli/*` missing generic params on `_SubParsersAction`, a
  `digest_test.py` arg-type). Left untouched — out of scope for this
  section.
