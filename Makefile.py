"""Makefile.py for pymake project."""

from pathlib import Path

from pymake import sh


@task(outputs=["build/.ruff-check"])
def lint():
    """Run ruff linter."""
    Path("build").mkdir(exist_ok=True)
    sh("ruff check pymake")
    Path("build/.ruff-check").touch()


@task(outputs=["build/.mypy-check"])
def typecheck():
    """Run mypy type checker."""
    Path("build").mkdir(exist_ok=True)
    sh("mypy pymake")
    Path("build/.mypy-check").touch()


@task(outputs=["build/.pytest-done"])
def test():
    """Run pytest."""
    Path("build").mkdir(exist_ok=True)
    sh("pytest -v pymake")
    Path("build/.pytest-done").touch()


@task(inputs=["build/.ruff-check", "build/.mypy-check", "build/.pytest-done"])
def check():
    """Run all checks (lint, typecheck, test)."""
    print("All checks passed!")


@task()
def clean():
    """Clean build artifacts."""
    import shutil

    if Path("build").exists():
        shutil.rmtree("build")
        print("Cleaned build directory.")
    else:
        print("Nothing to clean.")


@task()
def format():
    """Format code with ruff."""
    sh("ruff format pymake")
    sh("ruff check --fix pymake", check=False)
