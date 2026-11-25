"""OpenSSH connection reuse scoped to one pymake process.

Every ssh call that carries `mux.options()` shares one ControlMaster per
destination: the first call to a destination becomes its master
(ControlMaster=auto), every later call in this process -- any task, any
dependency, any thread -- rides that connection, and the masters exit with the
process. The control sockets live in a private per-process directory, so two
pymake runs never share or tear down each other's connections.

A host behind a jump host passes `jump=`: the hop is a ProxyCommand running
`ssh <mux options> <jump> -W %h:%p`, so it rides the jump host's master.
`-J` cannot do this, because ProxyJump does not pass `-o` options to the hop.

    from pymake.ssh import mux
    subprocess.run(["ssh", *mux.options(), "devbox", "true"])
    subprocess.run(["ssh", *mux.options(jump="devbox"), "-p", "12226", "root@127.0.0.1", "true"])
    subprocess.run(["nix", "copy", "--to", "ssh-ng://devbox", path], env={**os.environ, **mux.nix_env()})
"""

from __future__ import annotations

import atexit
import shlex
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path


class SSHMux:
    def __init__(self) -> None:
        self._directory: Path | None = None
        self._lock = threading.Lock()

    def directory(self) -> Path:
        with self._lock:
            if self._directory is None:
                # /tmp, not $TMPDIR: macOS's TMPDIR is long enough that
                # sockets overflow sun_path (104 bytes) once %C is appended.
                self._directory = Path(tempfile.mkdtemp(prefix="pymake-ssh-", dir="/tmp"))
                atexit.register(self.close)
            return self._directory

    def options(self, *, jump: str | None = None) -> list[str]:
        """`-o` options for an ssh argv; with `jump`, the hop rides its own master."""
        options = [
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={self.directory()}/%C",
            "-o", "ControlPersist=yes",
        ]
        if jump is not None:
            # ProxyCommand expands % tokens itself: %%C reaches the hop as %C.
            hop = shlex.join(["ssh", *options, jump, "-W", "%h:%p"]).replace("/%C", "/%%C")
            options += ["-o", f"ProxyCommand={hop}"]
        return options

    def nix_env(self) -> dict[str, str]:
        """NIX_SSHOPTS for ssh:// and ssh-ng:// stores (Nix splits it on spaces,
        hence no jump form)."""
        return {"NIX_SSHOPTS": " ".join(self.options())}

    def close(self) -> None:
        """Exit every master this process started and remove the socket dir."""
        with self._lock:
            directory, self._directory = self._directory, None
        if directory is None:
            return
        for socket in directory.iterdir():
            subprocess.run(
                ["ssh", "-o", f"ControlPath={socket}", "-O", "exit", "pymake-mux"],
                stdin=subprocess.DEVNULL, capture_output=True, check=False,
            )
        shutil.rmtree(directory, ignore_errors=True)


mux = SSHMux()
