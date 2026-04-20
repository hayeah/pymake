---
status: done
section: pymake SKILL.md: use `task = ctx.task` in library example for Makefile.py compat
slug: pymake-skill-md-use-task-ctx-task-in-library-example-for-makefile-py-compat
mode: worktree
spec:
created: 2026-04-20T11:26:10Z
---

> ## pymake SKILL.md: use `task = ctx.task` in library example for Makefile.py compat
>
> ---
> status:
>   type: open
> ---
>
> Quick doc tweak to pymake. The library-use example at https://github.com/hayeah/pymake/blob/master/SKILL.md#library-use-pymakecontextcwd currently writes `@ctx.task(...)` directly. Change it to assign `task = ctx.task` once at the top, then use `@task(...)` in the body — so the function body reads identically to what you'd write in a `Makefile.py`. The whole point: a Makefile.py body should copy-paste into a `context(cwd=...)` function with only the `task = ctx.task` prelude added.
>
> Generalize the principle: pymake's library surface (`TaskContext`) should be **syntactically compatible** with the module-level `Makefile.py` idiom wherever possible — same decorator shape, same `sh(...)` usage, same `@task(inputs=..., outputs=...)` kwargs. Document this as a short guiding note alongside the example so future additions to `TaskContext` preserve the invariant.
>
> Scope:
>
> - Update the `Library use: pymake.context(cwd=...)` example in `~/github.com/hayeah/pymake/SKILL.md` (and `README.md` if they're kept in sync — trouble report from the TaskContext section noted they are maintained together by hand).
> - Sweep for other places the same "direct `@ctx.task`" pattern shows up (probably the other library examples + `example/hello_context.py` + any docstrings) and flip them to `task = ctx.task` for consistency. Only change when it keeps the example cleaner, not when it makes things worse.
> - Add a one-sentence note near the example explaining the Makefile.py-compat goal.
>
> No code changes expected. If you find a real API asymmetry while sweeping — something a Makefile.py can do that `ctx` can't — flag it in the trouble report; don't fix it in this pass.
>
> Work in `~/github.com/hayeah/pymake` (worktree).
>
> - [ ] update SKILL.md + README.md + example + any stray docstrings; full `uv run pytest` still green (should be no-op since doc-only but confirm)

## Todos
<!-- Finer-grained than the boss-doc top-level checkboxes. Tick off as you go. -->

- [x] Update SKILL.md `Library Use` example: add `task = ctx.task` prelude, use `@task(...)` in body; add Makefile.py-compat note
- [x] Mirror the same changes in README.md (no-op: README.md is a symlink to SKILL.md)
- [x] Update `example/hello_context.py` to use `task = ctx.task` for consistency
- [x] Review docstrings in `src/pymake/context.py` — kept `ctx.task` API-reference phrasing; changing would be API-reference noise
- [x] Run `uv run pytest` to confirm green — 220/220 passed; example runs and prints expected output
- [x] Sweep for any real Makefile.py/TaskContext API asymmetries — noted in trouble report

## Agent log
- 2026-04-20T11:28Z landed doc tweak + example alias + compat note (884a6be); README.md is a symlink to SKILL.md so both cover together; 220/220 tests green

## Boss log

## Evidence

Commit: `884a6be` on branch `pymake-skill-md-use-task-ctx-task-in-library-example-for-makefile-py-compat`.

Diff scope (2 files, +14 / -4):

```
 SKILL.md                 | 13 +++++++++++--
 example/hello_context.py |  5 +++--
```

README.md is a symlink to SKILL.md (`lrwxr-xr-x README.md -> SKILL.md`), so
the README is updated transparently — the trouble-report note inherited from
the TaskContext section about them being "maintained together by hand" is
outdated; they're the same file on disk.

Full test suite green (doc-only change, sanity check):

```
$ uv run pytest
============================= 220 passed in 0.19s ==============================
```

Example still runs end-to-end with the new alias:

```
$ uv run python example/hello_context.py
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

Key excerpt from the updated SKILL.md (Library Use section):

```python
def pipeline(root: Path, greeting: str) -> pymake.TaskContext:
    (root / "name.txt").write_text("world")
    ctx = pymake.context(cwd=root)  # relative inputs/outputs resolve here
    task = ctx.task                 # alias so the body mirrors Makefile.py

    @task(inputs=["name.txt"], outputs=["greet.txt"])
    def greet():
        ...

    @task(inputs=["greet.txt"], outputs=["shout.txt"])
    def shout():
        ...

    ctx.default(shout)
    return ctx
```

Followed by a new prose paragraph documenting the Makefile.py-compat
invariant as a guiding principle for future `TaskContext` additions.

## Trouble report

- README.md being a symlink to SKILL.md means the "kept in sync by hand"
  note on the section (inherited from the earlier TaskContext section) is
  stale — one edit covers both. Worth updating that older report eventually
  but out of scope here.

- Minor API asymmetry found while sweeping (flagged only, not fixed per
  scope): `ctx.default(target)` is a first-class method on `TaskContext`,
  but the module-level `task` decorator singleton does not expose a
  `task.default(...)` attribute. In `Makefile.py` the default target is set
  via the CLI invocation, not the decorator surface. So a `Makefile.py`
  body that relies on `ctx.default(...)` cannot copy-paste back out 1:1 —
  you'd need to drop the `ctx.default(shout)` line and pass the target on
  the `pymake` CLI. Not blocking for the common case (most `Makefile.py`
  bodies never call `.default()` since the CLI handles it), but if full
  bidirectional copy-paste compat is desired, either add a
  `task.default(...)` shim at the module level or a short note in
  SKILL.md calling out `ctx.default(...)` as the one asymmetry.

- Docstrings inside `src/pymake/context.py` still describe the decorator
  as `@ctx.task(...)`. Left as-is: in an API reference the fully-qualified
  `ctx.task` is the accurate name of the attribute; the `task = ctx.task`
  alias is a usage convention for example bodies, not a rename of the
  attribute itself. Changing the docstrings would make the API reference
  less precise, not more.
