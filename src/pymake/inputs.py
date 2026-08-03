"""User-defined inputs with fingerprints (the Input contract).

An input is any object with an ``id`` and a ``fingerprint()`` — a cheap,
stable identity of the input's CURRENT state. The executor owns comparing
and recording fingerprints in per-task state files; there is no commit
protocol for the input (or a wrapper around it) to sever.

The ``id`` is the FIRST positional argument of every input constructor:
globally unique among Input objects, greppable, and enforced at
registration. Paths and task references are NOT Input objects — they live
in their own namespaces (see ``docs/input-contract-design.md``).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Input",
    "ValueInput",
    "GitInput",
    "value",
    "git",
    "is_input",
    "input_defsite",
]


@runtime_checkable
class Input(Protocol):
    """The Input contract: exactly an id and a fingerprint.

    ``fingerprint()`` must be an idempotent read of world state — same
    world, same fingerprint, within and across invocations. Clock,
    randomness, and counters are parameters a task computes when it runs,
    never inputs.
    """

    id: str

    def fingerprint(self) -> str:
        """Cheap, stable identity of the input's CURRENT state."""
        ...


def is_input(obj: object) -> bool:
    """True if *obj* participates in the Input contract.

    Detection is by capability (a callable ``fingerprint`` attribute), so a
    fingerprint-bearing object with a missing ``id`` is recognized as an
    Input and rejected loudly at registration, instead of being silently
    misread as a path or a task reference.
    """
    if isinstance(obj, (str, Path)):
        return False
    return callable(getattr(obj, "fingerprint", None))


def input_defsite(obj: object) -> str | None:
    """The recorded ``file:line`` definition site of *obj*, if any."""
    site = getattr(obj, "defsite", None)
    if isinstance(site, str) and site:
        return site
    return None


def caller_site() -> str | None:
    """``file:line`` of the code that called the function calling this.

    One ``inspect`` hop, taken at Makefile load: input constructors record
    where they were written so errors, warnings, and doctor output can
    point at the definition site.
    """
    frame = inspect.currentframe()
    for _ in range(2):
        if frame is None:
            return None
        frame = frame.f_back
    if frame is None:
        return None
    return f"{frame.f_code.co_filename}:{frame.f_lineno}"


def check_id(id: str, kind: str, defsite: str | None) -> None:
    """Fail loud on a missing/empty input id."""
    if not isinstance(id, str) or not id:
        at = f" (at {defsite})" if defsite else ""
        raise ValueError(
            f"{kind} input requires a non-empty id as its first argument{at}"
        )


def _canonical_bytes(id: str, val: Any) -> bytes:
    """Stable byte encoding of a str/bytes/JSON-able value."""
    if isinstance(val, bytes):
        return b"bytes:" + val
    if isinstance(val, str):
        return b"str:" + val.encode()
    try:
        text = json.dumps(val, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as e:
        raise TypeError(
            f"value input '{id}': value must be str, bytes, or JSON-able: {e}"
        ) from e
    return b"json:" + text.encode()


class ValueInput:
    """A Python value as a first-class input.

    Fingerprint = stable hash of the value (str/bytes/JSON-able). Config
    state becomes an input directly — no serializing it to a file purely
    for mtime visibility.
    """

    def __init__(self, id: str, val: Any, *, defsite: str | None = None) -> None:
        check_id(id, "value", defsite)
        self.id = id
        self.value = val
        self.defsite = defsite
        _canonical_bytes(id, val)  # fail loud at construction, not first use

    def fingerprint(self) -> str:
        payload = _canonical_bytes(self.id, self.value)
        return hashlib.blake2b(payload, digest_size=16).hexdigest()

    def __repr__(self) -> str:
        return f"value({self.id!r})"


def value(id: str, val: Any) -> ValueInput:
    """Input whose fingerprint is a stable hash of *val*.

    ``val`` must be str, bytes, or JSON-able. The id is the greppable name;
    it must be globally unique among Input objects.
    """
    return ValueInput(id, val, defsite=caller_site())


def _short_hash(lines: Sequence[str]) -> str:
    hasher = hashlib.blake2b(digest_size=8)
    for line in lines:
        hasher.update(line.encode())
        hasher.update(b"\n")
    return hasher.hexdigest()


class GitInput:
    """The git-backed tree input.

    Git already owns tree walking, ignore rules, and content identity —
    reuse it instead of re-fingerprinting large trees:

    - **Clean** scoped tree: no filesystem walk. Without ``paths`` the
      fingerprint is the resolved commit id; with ``paths`` it is the
      scoped tree hashes (``git rev-parse <commit>:<path>``), so commits
      that do not touch the scope do not retrigger.
    - **Dirty** scoped tree: a dirt component built from
      ``git status --porcelain -- <paths>`` rows plus each dirty file's
      ``(mtime, size)`` — edits retrigger while dirty; returning to clean
      settles back. Untracked files count as dirt; ``.gitignore``
      carve-outs are inherited, so build residue never enters the
      fingerprint.
    - ``ref=`` pins a branch: the fingerprint follows the branch tip (a
      dependency on published state — working-tree dirt does not count).
    """

    def __init__(
        self,
        id: str,
        repo: str | Path,
        *,
        ref: str | None = None,
        paths: Sequence[str] = (),
        defsite: str | None = None,
    ) -> None:
        check_id(id, "git", defsite)
        self.id = id
        self.repo = Path(repo).expanduser()
        self.ref = ref
        self.paths = tuple(paths)
        self.defsite = defsite

    def __repr__(self) -> str:
        return f"git({self.id!r}, {str(self.repo)!r})"

    def _git(self, *args: str) -> tuple[int, str]:
        result = subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
        )
        # Note: stdout is returned raw — porcelain rows carry a significant
        # leading status column that must not be stripped.
        return result.returncode, result.stdout

    def _resolve_commit(self) -> str:
        rc, commit = self._git("rev-parse", "--verify", self.ref or "HEAD")
        if rc == 0:
            return commit.strip()
        # HEAD fails in a repo with no commits yet — that is a valid state
        # (everything is untracked dirt). A non-repo is a loud error.
        rc, _ = self._git("rev-parse", "--git-dir")
        if rc != 0:
            raise ValueError(
                f"git input '{self.id}': not a git repository: {self.repo}"
            )
        if self.ref is not None:
            raise ValueError(
                f"git input '{self.id}': cannot resolve ref '{self.ref}' in {self.repo}"
            )
        return "no-commit"

    def _scoped_tree(self, commit: str, path: str) -> str:
        """Content identity of *path* at *commit*.

        A literal path resolves to its tree/blob object id. A pathspec
        (``:(glob)ui/**/*.ts`` and friends) is not addressable as
        ``<commit>:<path>``, so it resolves to the last commit that touched
        it — same identity semantics, one ``git log -1``.
        """
        looks_like_pathspec = path.startswith(":") or any(ch in path for ch in "*?[")
        if not looks_like_pathspec:
            rc, obj = self._git("rev-parse", "--verify", f"{commit}:{path}")
            if rc == 0:
                return obj.strip()
        rc, last = self._git("log", "-1", "--format=%H", commit, "--", path)
        if rc == 0 and last.strip():
            return f"last:{last.strip()}"
        return "missing"

    def dirt_rows(self) -> list[str]:
        """Porcelain status rows for the scoped tree (untracked included).

        pymake's own ``.pymake/`` bookkeeping is excluded so state writes
        never count as dirt.
        """
        rc, out = self._git("status", "--porcelain", "--", *self.paths)
        if rc != 0:
            return []
        rows = []
        for row in out.splitlines():
            path_part = row[3:] if len(row) > 3 else row
            if " -> " in path_part:
                path_part = path_part.split(" -> ", 1)[1]
            if path_part.startswith(".pymake/") or path_part == ".pymake":
                continue
            rows.append(row)
        return sorted(rows)

    def _dirt_component(self) -> str | None:
        rows = self.dirt_rows()
        if not rows:
            return None
        lines: list[str] = []
        untracked = 0
        for row in rows:
            if row.startswith("??"):
                untracked += 1
            path_part = row[3:]
            if " -> " in path_part:
                path_part = path_part.split(" -> ", 1)[1]
            target = self.repo / path_part
            try:
                st = target.stat()
                stat_part = f"{st.st_mtime_ns}:{st.st_size}"
            except OSError:
                stat_part = "gone"
            lines.append(f"{row}\t{stat_part}")
        return f"dirty:{_short_hash(lines)}:{len(rows) - untracked}t:{untracked}u"

    def fingerprint(self) -> str:
        commit = self._resolve_commit()
        if self.paths and commit != "no-commit":
            parts = [f"{p}={self._scoped_tree(commit, p)}" for p in self.paths]
        else:
            parts = [f"commit:{commit}"]
        if self.ref is None:
            dirt = self._dirt_component()
            if dirt is not None:
                parts.append(dirt)
        return ";".join(parts)


def git(
    id: str,
    repo: str | Path,
    ref: str | None = None,
    paths: Sequence[str] = (),
) -> GitInput:
    """Input pinned to a git repo's state, optionally scoped by ``paths``.

    ``paths`` entries are git paths/pathspecs relative to *repo* —
    ``.gitignore`` carve-outs are inherited, and ``:(glob)`` magic works
    for suffix filtering. ``ref`` pins a branch (or any rev) instead of
    the working tree's HEAD+dirt.
    """
    return GitInput(id, repo, ref=ref, paths=paths, defsite=caller_site())
