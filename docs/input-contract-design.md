# Input contract design

Unify staleness under one concept: every dependency of a task is an **input
with a fingerprint**, and the executor owns comparing and recording those
fingerprints. This retires the `run_if` predicate side-channel and the
`TreeDigest` commit protocol, both of which degrade silently when misused.

## Problem

Staleness currently lives in three half-connected mechanisms — mtime
comparison over path inputs/outputs, `run_if` predicates, and `TreeDigest`
with its commit side-protocol — and the seams between them are where real
bugs live:

- **Severed commit.** The executor discovers a digest's commit half by
  convention: `.commit` on the `run_if` callable or its `__self__`
  (`src/pymake/executor.py:304-308`). Wrap the digest call in a plain
  function — natural the moment you want `digest.changed() or <condition>` —
  and the convention finds nothing. No error, no warning; the digest file
  never settles and the task rebuilds on every invocation, forever.
- **Ordering trap.** mtime staleness is evaluated BEFORE `run_if`
  (`executor.py:251` then `:257`). Declaring `outputs=` on a digest-gated
  task makes `should_run` return False on fresh-looking outputs and the
  digest is never consulted — real source edits get silently skipped. The
  intuitive change is the wrong one.
- **Blind consumers.** Callable inputs become dependency edges only — they
  carry no mtimes (`src/pymake/task.py:365-366`). A packaging task listing
  `build_native` as an input cannot see that `tool.exe` was re-linked; its
  own mtime check consults only its path inputs.
- **Stale-cache commit.** `TreeDigest.commit()` writes the fingerprint cached
  by the pre-run `changed()` walk (`src/pymake/digest.py:135-163`). A task
  that touches any walked file during its run commits a snapshot that never
  matches reality — the rebuild loop again, through a different door.
- **Global digest records.** The stored digest file is a per-INPUT record.
  If two tasks ever gate on the same digest, the first to run commits it and
  the second skips forever. Nothing prevents this; it is avoided only by
  accident of usage.

Downstream Makefiles grow the same compensating patterns to survive this:
config values serialized to "echo" files with write-if-changed discipline,
purely to become mtime-visible; hand-rolled wrapper classes combining
digest-changed with outputs-exist; per-Makefile glob helpers; staged artifact
paths hand-listed on consumers to work around blind dependency edges. All of
these are one missing feature: user-defined inputs.

## The contract

`inputs=` accepts, besides paths / strings / task refs, any object
satisfying:

```py
class Input(Protocol):
    id: str                               # REQUIRED: globally unique, greppable
    def fingerprint(self) -> str:
        """Cheap, stable identity of the input's CURRENT state."""
```

The contract is exactly these two members. There is no `describe()` — the id
is the canonical name and the captured definition site is the canonical
location; a free-text description would be a second, drifting source of
truth.

Staleness is one rule, with nothing to interleave and nothing severable:

```
run = cli_force or fingerprints_changed or outputs_missing or no_record
```

After success the executor re-walks every input FRESH (post-run) and records
the fingerprints in a per-task state file. Recording is the executor's job,
part of the contract — there is no commit protocol to discover, nothing a
wrapper function can disconnect.

## Greppable identity

The `id` is the FIRST positional argument of every input constructor —
uniform, so the grep target sits in a predictable position and no signature
lookup is needed:

```py
NATIVE_TREE  = git("native-sources", REPO_ROOT, paths=["Cargo.toml", "core", "cli"])
BUILD_CONFIG = value("build-config", config.raw_text)
```

- **Enforcement at registration, fail loud.** A missing/empty id is a
  registration error; two DIFFERENT Input objects claiming the same id is a
  registration error naming both definition sites. (Reusing ONE object across
  tasks is fine — the id then names the shared thing.)
- **Three kinds, three namespaces.** The id registry applies ONLY to Input
  objects. Paths are tracked as paths and task-refs as task names, each in
  its own namespace — a user id can never clobber or be shadowed by a real
  path or task. There are no "implicit ids"; a path is already its own
  greppable name.
- **Definition-site capture.** Input constructors record their caller's
  `file:line` (one `inspect` hop at Makefile load). Errors, warnings, and
  doctor output print it.
- **Decision lines say why, naming kind + name.**
  `[run] build_native (input native-sources changed)` /
  `(path assets/icon.png changed)` / `(dep build_native outputs changed)` /
  `[skip] build_native (unchanged)`. A reader greps the name and lands on
  exactly one thing.

## State: records are per-task, computation is per-run

A fingerprint record answers "did THIS task last run against this input
state?" — inherently per-task. Each task's state file (e.g.
`build/.pymake/state/<task>.json`) records kind-partitioned fingerprints:

```json
{ "paths": {"assets/icon.png": "..."},
  "deps":  {"build_native": "..."},
  "inputs": {"native-sources": "..."} }
```

When two tasks share one Input object, each carries its OWN last-seen
fingerprint: task A running does not settle the input for task B (shared
records would let A's run silently satisfy B's staleness — B skips forever).
What IS shared is the computation: one walk per input id per invocation,
memoized for the run. Per-task files also keep parallel-executor writes
contention-free (atomic temp+rename like all pymake writes).

## Built-in inputs

Exactly two, plus the existing kinds:

- `value(id, x)` — fingerprint = stable hash of a Python value
  (str/bytes/JSON-able). Config state becomes a first-class input; the echo
  file pattern (serialize config to disk with write-if-changed, solely for
  mtime visibility) stops existing.
- `git(id, repo, ref=None, paths=[...])` — the git-backed tree input, below.
- `Path` / `str` — unchanged, fingerprint `mtime_ns:size`.
- Task refs — still dependency edges, but a dep now also **contributes its
  declared outputs' fingerprints** to the consumer's staleness. This is the
  structural fix for blind consumers: a packaging task re-runs when the built
  binary actually changed, without hand-listing its path twice.

**No `glob` built-in — deliberately.** A raw filesystem glob walks whatever
is on disk (build residue, editor droppings) unless every Makefile
re-discovers the right exclusions — the `exclude=["target"]` chore
generalized into a footgun. Anything worth globbing is in git, and git
pathspecs already do suffix filtering (`:(glob)ui/**/*.ts`) with `.gitignore`
carve-outs inherited — use `git` with `paths=`. A genuinely un-gitted tree is
what the Input contract is FOR: write a custom Input.

## The git input

Git already owns tree walking, ignore rules, and content identity — reuse it
instead of re-fingerprinting large trees:

```py
git("native-sources", REPO_ROOT, paths=["Cargo.toml", "core", "cli", "vendor"])
git("proto-defs", "~/src/other-repo", ref="master", paths=["proto/"])
```

- **Clean** scoped tree: fingerprint = the resolved commit id (plus the
  scoped tree hash via `git rev-parse <commit>:<path>` when `paths` narrows
  it). No filesystem walk — a vendored tree with thousands of files costs one
  `rev-parse`.
- **Dirty** scoped tree: commit id + a dirt component built from
  `git status --porcelain -- <paths>` rows and the dirty files'
  `(mtime, size)` — edits retrigger while dirty; returning to clean settles
  back to the commit id. Untracked files count as dirt.
- `ref=` pins a branch: the fingerprint follows the branch tip, so the task
  re-runs when the branch moves — a dependency on another repo's published
  state, scoped to a path.
- `.gitignore` carve-outs are inherited: `target/`-style build residue inside
  the source tree never enters the fingerprint, with no per-Makefile exclude
  lists.

## run_if is deprecated (and no force_if is added)

Predicates in the task graph are where the severed-commit class of bug
lived, and every real use decomposes:

- Data gates ("rebuild when this config slice changes") → a `value(...)`
  input.
- Structural gates ("this task exists only on windows") → separate task
  registrations.
- Always-rebuild policy ("a release cut must not reuse a cached artifact") →
  the CALLER forces, with verbs that already exist: `pymake -B <task>`
  (whole-graph force) and `pymake redo <target> [--only]`. Policy about WHEN
  to disbelieve the cache belongs to the invocation, not the graph.

Forced runs record fingerprints normally, so a forced build settles state
and the next ordinary run skips. During migration, a legacy `run_if` keeps
working with a deprecation warning; a `run_if` callable that reaches a
`TreeDigest` but exposes no `.commit` (the severed-wrapper signature) gets a
hard warning.

## Nondeterministic inputs and self-mutating tasks

**An input must be an idempotent read of world state** — same world, same
fingerprint, within and across invocations. Clock, randomness, and counters
are parameters a task computes when it runs, never inputs.

The canonical violation is a version/identity string embedding a timestamp,
fed into the build. The fix is to remove the nondeterminism at the source:
derive the embedded identity from the INPUTS (the `git` input already
computes it — the commit id), build to an identity-keyed path
(`build/native/<commit>/tool.exe`), and re-point a stable-path hardlink as a
side effect. Same tree → same path, same bytes: rebuilds are idempotent,
forced rebuilds converge instead of churning identity, and consumers re-run
exactly when content genuinely changed. Wall-clock belongs in release-layer
version strings minted at cut time, not inside a build task's artifact.

**Self-mutating tasks** (writing inside their own input scope — compilers
touching lockfiles, toolchains dropping caches next to sources) are handled
by three mechanisms:

- **Record post-run.** Incidental self-mutation is absorbed and settles
  instead of looping. This kills the stale-cache-commit class outright.
- **Divergence warns INLINE, on every run.** The check is effectively free:
  record-post-run already mandates the post-run walk, so divergence is a diff
  of two datasets the executor necessarily holds. One warn line, named input,
  sample files, non-fatal. Burying this in a doctor verb would recreate the
  silent-degradation disease. The warning also covers the mid-run-edit race:
  an EXTERNAL edit landing while the task runs is absorbed by post-run
  recording (the classic make race — the next run would silently skip);
  divergence is that event's signature, so the warning doubles as its
  tripwire ("input native-sources changed during the run — if that was you,
  `redo`").
- **For `git` inputs the warning is an artifact-hygiene scan.** Post-run dirt
  that wasn't there pre-run is precisely "this task dirtied the repo" —
  tracked edits or untracked droppings leaking outside `.gitignore`.

**Nondeterminism detection is also inline**: a per-input flip counter in the
task's state file (incremented when the input changed since last record,
reset when it holds still). The executor warns once it crosses N: `input
build-config (Makefile.py:42) has changed on every one of the last 3 runs —
nondeterministic value or self-mutating task?`.

## Doctor

Divergence and nondeterminism warn inline; doctor is only for what needs
cross-run history or repo-wide sweeps — aggregating the flip counters across
tasks, and the migration-time severed-wrapper scan. Nothing a single run can
see is allowed to hide behind a verb someone must remember to invoke.

## Non-goals

- **FS event watchers / daemons.** Watching only ever replaces the poll; the
  staleness read path is identical, and a watcher adds lifecycle and
  missed-event caveats. On-demand fingerprints are fast enough (`git` on
  clean trees is one rev-parse).
- **Content hashing by default.** mtime+size everywhere; the `git` input gets
  content accuracy from git for free where it matters.
- **A global build database.** Per-task state files under the build dir keep
  the no-magic feel and stay `rm -rf`-able.
- **A raw `glob` built-in.** See above.

## Compatibility and phasing

Existing Makefiles must keep working at every commit: `tree_digest`,
`run_if`, and `touch=` remain functional through the transition, with
deprecation warnings pointing at their replacements.

- Input protocol + `value` + executor-owned per-task state; paths and task
  refs migrate transparently onto fingerprint records.
- Dep-outputs contribution + decision-line reasons (`[run] ... (input X
  changed)`).
- The `git` input.
- Inline divergence + flip-counter warnings; doctor aggregation; `run_if`
  deprecation warnings.

## Open questions

- Fingerprint format versioning: a pymake upgrade that changes fingerprint
  composition should invalidate loudly ("state format v2 — full rebuild"),
  not silently mass-rebuild.
- Dep-outputs contribution vs deliberately per-build outputs: with
  deterministic identity this settles, but audit for tasks where a consumer
  would ping-pong.
- `git` in repos with submodules: `status --porcelain` covers the outer
  repo; decide whether submodule pointer changes count as dirt.

## As implemented (deviations and precisions)

Everything above is implemented; the deltas, where the code had to make a
call the spec left open (or where compatibility forced one):

- **`git` with `paths=` drops the commit id from the fingerprint.** The
  spec reads "the resolved commit id (plus the scoped tree hash …)";
  implemented as: unscoped → commit id, scoped → the scoped tree hashes
  ONLY. Including the commit id would retrigger on every commit anywhere
  in the repo, defeating the scope's purpose (don't rebuild natives when
  docs change). Same for `ref=` + `paths=`: the task follows the branch's
  *scoped content*, not every commit on the branch.
- **Pathspec scoping falls back to `git log -1`.** `rev-parse
  <commit>:<path>` cannot address pathspec magic (`:(glob)ui/**/*.ts`);
  such paths resolve to the last commit touching the pathspec instead —
  same identity semantics, one extra subprocess.
- **Legacy compatibility matrix** (tasks with NO Input objects keep their
  historical semantics — the formula governs tasks that opt into inputs):
  - No outputs and no Input objects → phony, always runs. (A task WITH an
    Input object and no outputs is gated by its record; the state file is
    the marker, as the digest file used to be.)
  - Outputs but no record yet → one-time fallback to the mtime rule, and
    the record is bootstrapped on the skip path too — existing Makefiles
    migrate onto fingerprint records with no mass rebuild.
  - Outputs and nothing fingerprintable at all → outputs-missing check
    only, never recorded (identical to historical behavior).
  - `run_if` still evaluates AFTER the staleness check, as before; the
    documented ordering trap is preserved rather than silently reordered,
    since run_if is deprecated wholesale.
- **`touch=` does not warn.** The compatibility section says tree_digest,
  run_if, and touch= all deprecation-warn; only `tree_digest()` and
  `run_if`/`run_if_not` do. For legacy path-input tasks `touch=` remains
  the only output marker (phony tasks always run), so there is no working
  replacement to point at short of adopting Input objects — warning on it
  would nag with no migration. Tasks with Input objects simply no longer
  need it.
- **Divergence sample files** are provided where the kind has them: for
  `paths` the name IS the file; for `git` inputs the warning appends up to
  three current porcelain rows (re-scanned at warn time, so it reports
  present dirt rather than a strict pre/post set difference). `value` and
  custom inputs have no file sample.
- **Severed-wrapper detection is one inspection hop**: closure cells,
  referenced module globals (the canonical Makefile shape), `partial`
  func/args/keywords, argument defaults, and a bound method's instance
  attributes. A digest behind deeper indirection (returned by a call,
  nested two wrappers down) is not detected — best-effort tripwire, not an
  analysis pass.
- **State format versioning is minimal**: state files carry `"version": 1`;
  any mismatch (or corrupt file) reads as "no record" and the task re-runs
  and re-records. The loud "state format v2 — full rebuild" banner remains
  an open question.
- **Flip counters warn at N=3**, at recording time (so the warning
  accompanies the run that exhibits the flip); `pymake doctor` aggregates
  recorded counters and runs the severed-wrapper sweep. The pre-run check
  before `run`/`redo` stays errors-only (warnings print but never block).
- **`pymake which` / dry-run plans still use the legacy mtime heuristic**
  for their would-run markers; the executor's decision lines are the
  authoritative account. Folding the fingerprint decision into the
  planners is future work.
