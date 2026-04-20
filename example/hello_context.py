"""Self-hosted smoke demo for ``pymake.context``.

Builds two independent contexts in the same process. Each one runs a
two-task "greet → shout" mini-pipeline rooted at a separate directory.
Proves (a) the context surface works end-to-end, (b) freshness checks
resolve against ``ctx.cwd``, and (c) two contexts in one process do not
cross-pollinate.

Run: ``python example/hello_context.py``. The first invocation executes
both tasks in each context; re-running skips anything already up to date.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pymake


def make_pipeline(greeting: str, root: Path) -> pymake.TaskContext:
    """Return a populated TaskContext rooted at *root*."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "name.txt").write_text("world")

    ctx = pymake.context(cwd=root)

    @ctx.task(inputs=["name.txt"], outputs=["greet.txt"])
    def greet() -> None:
        name = (root / "name.txt").read_text().strip()
        (root / "greet.txt").write_text(f"{greeting}, {name}!\n")

    @ctx.task(inputs=["greet.txt"], outputs=["shout.txt"])
    def shout() -> None:
        msg = (root / "greet.txt").read_text()
        (root / "shout.txt").write_text(msg.upper())

    ctx.default(shout)
    return ctx


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="pymake-hello-context-"))
    try:
        en = make_pipeline("Hello", tmp / "en")
        fr = make_pipeline("Bonjour", tmp / "fr")

        print("=== first run (both pipelines fresh) ===")
        en.run()
        fr.run()

        print("\n=== second run (everything up to date) ===")
        en.run()
        fr.run()

        print("\n=== results ===")
        for label, ctx in (("en", en), ("fr", fr)):
            out = (ctx.cwd / "shout.txt").read_text().rstrip()
            print(f"  {label}: {out}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
