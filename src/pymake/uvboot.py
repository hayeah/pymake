"""Seamless uv project integration.

A Makefile.py often wants third-party deps (e.g. `import maxminddb` inside a task)
without polluting the interpreter that pymake happens to be installed in. The
standard shape:

    myproject/
      Makefile.py
      pyproject.toml     # declares the deps, opts in via [tool.pymake]

When the Makefile.py sits next to a `pyproject.toml` containing a `[tool.pymake]`
table, the CLI re-execs itself under `uv run --project <dir>` before loading the
Makefile, so uv syncs the project venv and every task import resolves against it.
pymake itself is injected into that run as an overlay (`--with-editable <source
checkout>` when pymake is an editable install, else `--with hayeah-pymake==<ver>`),
so the project does NOT need to declare pymake as a dependency.

Guards:
  - PYMAKE_UV_PROJECT=<abs dir> marks "already re-exec'd for this project". It is
    dir-valued (not a boolean) so a task that shells out to `pymake -C <other>`
    still bootstraps the OTHER project's env instead of inheriting this one's.
  - PYMAKE_NO_UV=1 disables the whole mechanism.
  - No `uv` on PATH, or no `[tool.pymake]` in the pyproject: silently run as today.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

ENV_GUARD = "PYMAKE_UV_PROJECT"
ENV_DISABLE = "PYMAKE_NO_UV"


def project_dir(makefile: Path) -> Path | None:
    """The makefile's directory, iff it opts into uv via [tool.pymake]."""
    directory = makefile.resolve().parent
    pyproject = directory / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return None
    if "pymake" not in data.get("tool", {}):
        return None
    return directory


def source_root() -> Path | None:
    """pymake's own source checkout root, when running from an editable install."""
    marker = Path(__file__).resolve()
    for parent in marker.parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            name = tomllib.loads(pyproject.read_text())["project"]["name"]
        except (tomllib.TOMLDecodeError, OSError, KeyError):
            return None
        return parent if name == "hayeah-pymake" else None
    return None


def _self_overlay() -> list[str]:
    """uv args that make this pymake importable inside the project run."""
    src = source_root()
    if src is not None:
        return ["--with-editable", str(src)]
    from importlib.metadata import version

    return ["--with", f"hayeah-pymake=={version('hayeah-pymake')}"]


def uv_command(
    directory: Path, argv: list[str], environ: Mapping[str, str] = os.environ
) -> list[str] | None:
    """Build the `uv run` re-exec argv, or None to proceed without uv."""
    if environ.get(ENV_DISABLE):
        return None
    if environ.get(ENV_GUARD) == str(directory):
        return None  # already inside this project's uv run
    if shutil.which("uv") is None:
        return None
    return [
        "uv",
        "run",
        "--project",
        str(directory),
        *_self_overlay(),
        "python",
        "-m",
        "pymake",
        *argv,
    ]
