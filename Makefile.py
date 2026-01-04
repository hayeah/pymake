"""Makefile.py for pymake project."""

from pymake import sh, task


@task()
def lint():
    """Run ruff linter."""
    sh("ruff check src/pymake")


@task()
def typecheck():
    """Run mypy type checker."""
    sh("mypy src/pymake")


@task()
def test():
    """Run pytest."""
    sh("pytest -v src/pymake")


@task()
def check():
    """Run all checks (lint, typecheck, test)."""
    sh("ruff check src/pymake")
    sh("mypy src/pymake")
    sh("pytest -v src/pymake")

@task()
def format():
    """Format code with ruff."""
    sh("ruff format src/pymake")
    sh("ruff check --fix src/pymake", check=False)
