"""Makefile.py for taskpy project."""

from taskpy import sh, task

@task()
def lint():
    """Run ruff linter."""
    sh("ruff check src/taskpy")


@task()
def typecheck():
    """Run mypy type checker."""
    sh("mypy src/taskpy")

@task()
def format():
    """Format code with ruff."""
    sh("ruff format src/taskpy")
    sh("ruff check --fix src/taskpy", check=False)

@task()
def test():
    """Run pytest."""
    sh("pytest -v src/taskpy")

@task(inputs=[lint, typecheck, format, test])
def all():
    pass


task.default(all)
