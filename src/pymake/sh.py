"""Shell utilities for pymake."""

from __future__ import annotations

import subprocess


def sh(cmd: str | list[str], capture: bool = False, check: bool = True) -> str:
    """Run shell command.

    Parameters:
        cmd: Command to run. Can be a string (shell command) or list of
             strings (program and arguments).
        capture: If True, capture and return command output.
                 If False (default), output goes to terminal.
        check: If True (default), raise CalledProcessError for non-zero exit.

    Returns:
        Command output as string if capture=True, otherwise empty string.

    Raises:
        subprocess.CalledProcessError: If command fails and check=True.

    Examples:
        >>> sh('echo hello')  # prints to terminal
        ''
        >>> sh('echo hello', capture=True)
        'hello'
        >>> sh('exit 1', check=False)  # won't raise
        ''
    """
    result = subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        check=check,
        capture_output=capture,
        text=True,
    )
    if capture:
        return result.stdout.strip()
    return ""
