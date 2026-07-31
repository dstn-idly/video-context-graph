"""Fork-safe subprocess execution for macOS.

Both yt-dlp and TwitchDownloaderCLI died instantly with SIGSEGV when spawned
from inside Streamlit — while the identical commands worked from a shell. Two
unrelated binaries crashing the same way from one parent is not their bug:
subprocess's default path forks the parent, and forking a heavily-threaded
macOS process (Tornado, Neo4j driver, gRPC) can corrupt the child before exec.

os.posix_spawn skips the fork entirely — the kernel launches the child
directly — so the parent's thread soup can't hurt it. Output goes to temp
files, which also gives us live progress tailing for long downloads.
"""
import os
import tempfile
import time

from . import config

SCRATCH = config.ROOT / "clips" / ".proc"


class SpawnResult:
    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run(cmd: list[str], *, timeout: int = 900, tail=None,
        poll_interval: float = 0.25) -> SpawnResult:
    """Run a command via posix_spawn, capturing output to files.

    tail(line) is called for each NEW stdout line as it appears — that is how
    download progress reaches the UI without pipes (pipes would need reader
    threads; files need only polling).
    """
    SCRATCH.mkdir(parents=True, exist_ok=True)
    out_fd, out_path = tempfile.mkstemp(dir=SCRATCH, suffix=".out")
    err_fd, err_path = tempfile.mkstemp(dir=SCRATCH, suffix=".err")
    try:
        file_actions = [
            (os.POSIX_SPAWN_DUP2, out_fd, 1),
            (os.POSIX_SPAWN_DUP2, err_fd, 2),
        ]
        pid = os.posix_spawn(cmd[0], cmd, dict(os.environ),
                             file_actions=file_actions)

        deadline = time.time() + timeout
        offset = 0
        status = None
        while True:
            done_pid, status = os.waitpid(pid, os.WNOHANG)
            # stream any new stdout lines to the caller
            if tail is not None:
                try:
                    with open(out_path, "r", errors="replace") as fh:
                        fh.seek(offset)
                        chunk = fh.read()
                        offset = fh.tell()
                    for line in chunk.splitlines():
                        if line.strip():
                            try:
                                tail(line)
                            except Exception:
                                pass  # a UI callback must never kill the child
                except OSError:
                    pass
            if done_pid:
                break
            if time.time() > deadline:
                try:
                    os.kill(pid, 9)
                    os.waitpid(pid, 0)
                except OSError:
                    pass
                raise TimeoutError(f"{cmd[0]} timed out after {timeout}s")
            time.sleep(poll_interval)

        returncode = os.waitstatus_to_exitcode(status)
        with open(out_path, "r", errors="replace") as fh:
            stdout = fh.read()
        with open(err_path, "r", errors="replace") as fh:
            stderr = fh.read()
        return SpawnResult(returncode, stdout, stderr)
    finally:
        for fd in (out_fd, err_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        for path in (out_path, err_path):
            try:
                os.unlink(path)
            except OSError:
                pass
