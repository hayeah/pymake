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
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = ["Input", "ValueInput", "value", "is_input", "input_defsite"]


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
