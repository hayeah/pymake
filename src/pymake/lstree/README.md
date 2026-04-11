# lstree — Sane Directory Tree Walker

A Python directory walker with sane default filtering — skip `.git/`,
`node_modules/`, `__pycache__/`, honour `.gitignore` when present, apply
include/exclude glob filters. Zero runtime dependencies, Python 3.11+.

Port of [go-lstree](https://github.com/hayeah/go-lstree). Same semantics,
same API shape, same builtin ignore list.

## Quick start

```python
from pymake.lstree import walk, Query

# Zero-config — yields the useful files in a tree
for entry in walk("src/"):
    print(entry.path)

# With glob filter and stat
q = Query(globs=["**/*.py"], stat=True)
for entry in walk(".", query=q):
    print(entry.path, entry.size, entry.mtime_ns)

# Multiple roots, file paths allowed
for entry in walk("src/", "lib/", "pyproject.toml"):
    ...

# Kill switch — disable all filtering
for entry in walk(".", query=Query(no_ignore=True)):
    ...
```

## The 3-stage filter pipeline

Every file passes through three stages, in order:

```
all files in tree
  │
  ├─ Stage 1 — BASE EXCLUDE (always on)
  │   ├─ .gitignore rules if <root>/.gitignore exists
  │   └─ otherwise builtin ignores (node_modules, __pycache__, …)
  │
  ├─ Stage 2 — INCLUDE (Query.globs, optional)
  │   └─ **/*.ts, **/*.tsx — narrows the result
  │
  └─ Stage 3 — EXCLUDE (Query.exclude, optional)
      └─ __generated__, *.test.* — additional filter, always wins
```

**Base exclude is always sane without configuration.** Include narrows.
Exclude adds.

### Stage 1: base exclude

When `<root>/.gitignore` exists, its rules are used and the builtin list
is turned off (same as go-lstree — projects that track `build/` intentionally
should not have it re-ignored by the walker).

When no `.gitignore` is present, the `BuiltinIgnorer` applies:

- VCS: `.git`, `.hg`, `.svn`
- JS/TS: `node_modules`, `.next`, `.nuxt`
- Python: `__pycache__`, `.venv`, `venv`, `*.egg-info`, `.mypy_cache`,
  `.pytest_cache`, `.ruff_cache`, `.tox`
- Rust: `target`
- Swift/Xcode: `.build`, `DerivedData`
- JVM: `.gradle`
- Ruby: `.bundle`
- OS junk: `.DS_Store`, `Thumbs.db`
- IDE: `.idea`, `.vscode`
- Generic: `dist`, `build`

The full list lives in `builtins.py`.

`Query(no_ignore=True)` is a true kill switch — it bypasses both
`.gitignore` and the builtin list entirely (matches go-lstree semantics;
its fixture `no-ignore-shows-everything` yields `.git/config`).

### Stage 2: include globs

When `query.globs` is set, only files matching at least one positive
glob pass. Directories are always traversed to reach matches inside.

| Pattern | Meaning |
|---|---|
| `*` | Any sequence except `/` |
| `**` | Zero or more path segments (crosses `/`) |
| `?` | One char except `/` |
| `[abc]` / `[!abc]` / `[a-z]` | Character class |
| `!pattern` | Negation — exclude files matching this |

Examples:

```python
Query(globs=["**/*.ts"])                       # all .ts anywhere
Query(globs=["*.json"])                        # .json only at root
Query(globs=["**/*.py", "!**/test_*"])         # py but not test_*
Query(globs=["src/**", "lib/**"])              # OR of subtrees
```

Rules (match `go-lstree`'s `GlobsMatch`):

- Any negated match rejects the path
- If any positive glob is listed, at least one must match
- If only negations are given, anything not rejected passes

### Stage 3: additional exclude

`query.exclude` is applied after everything else. It **always wins**, even
against positive include globs.

Two flavours:

- **Bare name** (no glob chars, no slash) — matches any path component.
  `exclude=["__generated__"]` skips any file or directory component named
  `__generated__` at any depth.
- **Glob** (contains `*`, `?`, `[`, or `/`) — matched with `glob_match`
  against the full relative path. `exclude=["**/*.test.*"]` skips
  `src/foo.test.ts`.

A trailing `/` is stripped for convenience (`"fixtures/"` behaves like
`"fixtures"`).

## API

```python
@dataclass(frozen=True)
class Entry:
    path: Path          # relative to the walk root
    size: int = 0       # 0 unless query.stat=True
    mtime_ns: int = 0   # 0 unless query.stat=True
    is_dir: bool = False

@dataclass
class Query:
    globs:     list[str] | None = None   # stage 2
    exclude:   list[str] | None = None   # stage 3
    max_depth: int  = 0                  # 0 = unlimited, 1 = flat listing
    stat:      bool = False              # populate size/mtime_ns
    no_ignore: bool = False              # disable all ignore processing

def walk(
    *paths: str | os.PathLike[str],
    query: Query | None = None,
) -> Iterator[Entry]: ...
```

### `max_depth` and flat listings

`max_depth=0` (default) is unlimited — full recursion.

`max_depth=1` yields a **flat listing**: files at the root plus the
names of its immediate subdirectories (as `Entry(is_dir=True)`). Useful
for "what's in this folder" displays.

`max_depth=N` (N>1) walks N levels deep. Directories at the final depth
are yielded as `is_dir=True` entries; files are yielded up to and
including depth N.

### Multiple roots, file paths

`walk()` accepts any number of positional paths. Each is processed in
order:

- A directory is walked with the full pipeline.
- A file is yielded directly (no filtering — you named it specifically).
- A missing path is silently skipped (like `fd` / `rg`).

Results are yielded in sorted order within each directory; across roots
they appear in the order you passed them.

## Performance

Directory pruning happens in-place on `os.walk`'s `dirnames` list — an
ignored subtree like `node_modules/` with 50,000 files is skipped with a
single basename check, not 50,000. Walking a typical source tree takes
~2-3 ms; the expensive part is `stat()` syscalls, so leave `stat=False`
unless you need size/mtime (the common `tree_digest` use case needs
both, which is why the opt-in exists).

## Files

- `glob.py` — `glob_match` / `globs_match` (port of go-lstree `glob.go`)
- `gitignore.py` — `.gitignore` parser + matcher
- `builtins.py` — `ALWAYS_IGNORED`, `BUILTIN_IGNORE_PATTERNS`, `BuiltinIgnorer`
- `walker.py` — `Query`, `Entry`, `walk()`
- `*_test.py` — colocated pytest tests
- `testdata/lstree_glob.json` — shared glob test vectors
- `testdata/lstree.json` — shared walker test vectors

## Relationship to go-lstree

Same semantics, same API shape, same builtin ignore list. Test vectors
under `testdata/` are shared with the Go and original Python ports.

Scope differences (v1):

- No nested `.gitignore` support — only the root `.gitignore` is read.
  Most projects put everything in the root file anyway; nested scopes
  are a straightforward v2.
- No sort/limit/offset/base — those live in go-lstree because it doubles
  as an HTTP API; Python consumers can sort the iterator themselves.
- No HTTP server.

## Consumers

- **pymake `tree_digest`** — `_walk()` calls `lstree.walk(stat=True)`,
  collects `(path, mtime_ns, size)` for fingerprinting.
- **AI agent tools** — "show me the files in this project" without
  drowning in `node_modules`.
- **Any Python CLI** that needs to list or process source files.
