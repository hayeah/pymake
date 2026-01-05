"""taskpy - Python Makefile alternative."""

from .executor import (
    ExecutionError,
    Executor,
    MissingInputError,
    MissingOutputError,
    UnproducibleInputError,
)
from .resolver import CyclicDependencyError, DependencyResolver
from .sh import sh
from .task import Task, TaskRegistry, task

__all__ = [
    "task",
    "Task",
    "TaskRegistry",
    "Executor",
    "ExecutionError",
    "MissingInputError",
    "MissingOutputError",
    "UnproducibleInputError",
    "DependencyResolver",
    "CyclicDependencyError",
    "sh",
]
