"""Shared typing contracts for CLI command handlers."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Protocol, TypeAlias

from .context import CommandContext

if TYPE_CHECKING:
    Subparsers: TypeAlias = argparse._SubParsersAction[argparse.ArgumentParser]
else:
    Subparsers = argparse._SubParsersAction


class CommandHandler(Protocol):
    """Structural interface implemented by every CLI command handler."""

    def __init__(self, ctx: CommandContext) -> None: ...

    @staticmethod
    def add_arguments(subparsers: Subparsers) -> None: ...

    def execute(self) -> None: ...
