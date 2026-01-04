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
def format():
    """Format code with ruff."""
    sh("ruff format src/pymake")
    sh("ruff check --fix src/pymake", check=False)

@task()
def test():
    """Run pytest."""
    sh("pytest -v src/pymake")

@task(inputs=[lint, typecheck, format, test])
def all():
    pass


task.default(all)
