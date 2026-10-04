"""Sandboxes: every process the eval starts runs in one of five profiles.

| Profile | Network          | Holds                                                     |
|---------|------------------|-----------------------------------------------------------|
| agent   | shared           | the checkout (rw), _temp (rw; _temp/cadence/input ro), a  |
|         |                  | fresh HOME, the plugin (factory only); live: the key      |
| gate    | shared (npm)     | the checkout (rw), a per-attempt npm cache copy; no key   |
| tools   | off              | like gate, plus the pinned tools (ro); every tool/*.py    |
| fetch   | shared           | gate, running npm install --ignore-scripts on a result    |
| score   | off (loopback)   | the grader copy (ro), the result repo (ro), its own /tmp, |
|         |                  | an npm cache copy; npm runs offline                       |

With ``sandbox: bwrap`` each profile is a bubblewrap namespace that sees
/usr, /etc, the toolchain and the venv read-only and only the listed paths
besides: never /mnt, /init, /run, the user's home, the results, the config
directory, the key file, a chain's bare repo or another chain. With
``sandbox: none`` (stub runs only) a command is a plain subprocess with the
same explicit environment. No subprocess inherits the runner's environment.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from config import EvalError

PROFILES = ("agent", "gate", "tools", "fetch", "score")
NETWORK_OFF = {"tools", "score"}

RUNNER_HOME = "/home/runner"
RUNNER_WORK = "/home/runner/work"
RUNNER_TEMP = "/home/runner/work/_temp"
OPT_TOOLCHAIN = "/opt/toolchain"
OPT_VENV = "/opt/venv"
OPT_GUARD = "/opt/guard"
OPT_PLUGIN = "/opt/cadence-plugin"
OPT_TOOLS = "/opt/cadence-tools"
OPT_GRADER = "/opt/grader"
SRV_RESULT = "/srv/result.git"
SRV_CACHE = "/srv/npm-cache"

GIT_IDENTITY = ("cadence-eval[bot]", "cadence-eval@localhost")
GIT_SAFE = (
    "-c", "core.hooksPath=/dev/null",
    "-c", f"user.name={GIT_IDENTITY[0]}",
    "-c", f"user.email={GIT_IDENTITY[1]}",
    "-c", "commit.gpgsign=false",
    "-c", "core.autocrlf=false",
    "-c", "init.defaultBranch=main",
)


@dataclass
class Result:
    exit: int | None
    timed_out: bool
    stdout: bytes
    stderr: bytes
    seconds: float
    start_failed: bool = False

    @property
    def ok(self) -> bool:
        return self.exit == 0 and not self.timed_out

    def text(self) -> str:
        return self.stdout.decode("utf-8", "replace")


@dataclass
class Bind:
    src: Path
    dst: str
    ro: bool = True
    file: bool = False


@dataclass
class Box:
    """One sandboxed environment: a profile, its binds and its runner dir.

    ``root`` is the job's runner dir: ``root/work`` is /home/runner/work and
    ``root/home`` is /home/runner. Paths handed to commands go through
    :meth:`inside`.
    """

    mode: str                      # bwrap | none
    profile: str
    home: Path                     # the eval home (toolchain, venv, guard)
    root: Path                     # this job's runner dir
    binds: list[Bind] = field(default_factory=list)
    guard: bool = True             # stub runs: a `claude` that exits 99 first on PATH
    env: dict[str, str] = field(default_factory=dict)
    secret_env: dict[str, str] = field(default_factory=dict)
    net: bool | None = None        # None: the profile's default

    def __post_init__(self) -> None:
        if self.profile not in PROFILES:
            raise EvalError(f"unknown sandbox profile {self.profile!r}")
        if self.mode not in ("bwrap", "none"):
            raise EvalError(f"unknown sandbox mode {self.mode!r}")
        (self.root / "work" / "_temp").mkdir(parents=True, exist_ok=True)
        (self.root / "home").mkdir(parents=True, exist_ok=True)

    # --- paths -----------------------------------------------------------------------

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def temp(self) -> Path:
        return self.root / "work" / "_temp"

    def inside(self, real: Path | str) -> str:
        """The path a sandboxed command uses for ``real``."""
        real = Path(real)
        if self.mode == "none":
            return real.as_posix()
        for base, dst in ((self.work, RUNNER_WORK), (self.root / "home", RUNNER_HOME)):
            try:
                rel = real.relative_to(base)
                return (dst + "/" + rel.as_posix()).rstrip("/") if rel.parts else dst
            except ValueError:
                pass
        for bind in self.binds:
            try:
                rel = real.relative_to(bind.src)
                return (bind.dst + "/" + rel.as_posix()).rstrip("/") if rel.parts else bind.dst
            except ValueError:
                pass
        raise EvalError(f"{real} is not visible in the {self.profile} sandbox")

    def opt(self, name: str) -> str:
        """Where a shared read-only directory lives for commands in this box."""
        real = {
            OPT_TOOLCHAIN: self.home / "toolchain",
            OPT_VENV: self.home / "venv",
            OPT_GUARD: self.home / "guard",
        }
        if self.mode == "none":
            if name in real:
                return real[name].as_posix()
            for bind in self.binds:
                if bind.dst == name:
                    return bind.src.as_posix()
            raise EvalError(f"{name} is not bound in the {self.profile} sandbox")
        return name

    # --- environment -------------------------------------------------------------------

    def base_env(self) -> dict[str, str]:
        guard = [self.opt(OPT_GUARD)] if self.guard else []
        path = ":".join(guard + [self.opt(OPT_TOOLCHAIN) + "/bin", self.opt(OPT_VENV) + "/bin", "/usr/bin", "/bin"])
        home = RUNNER_HOME if self.mode == "bwrap" else (self.root / "home").as_posix()
        temp = RUNNER_TEMP if self.mode == "bwrap" else self.temp.as_posix()
        env = {
            "PATH": path,
            "HOME": home,
            "RUNNER_TEMP": temp,
            "CI": "true",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "DISABLE_AUTOUPDATER": "1",
            "npm_config_cache": f"{home}/.npm",
        }
        if self.profile == "tools":
            # The tools run git on clones no agent code has touched; hooks off.
            env.update(
                {
                    "GIT_CONFIG_COUNT": "2",
                    "GIT_CONFIG_KEY_0": "core.hooksPath",
                    "GIT_CONFIG_VALUE_0": "/dev/null",
                    "GIT_CONFIG_KEY_1": "safe.directory",
                    "GIT_CONFIG_VALUE_1": "*",
                }
            )
        return env

    # --- bwrap ------------------------------------------------------------------------------

    def etc_args(self, network: bool, etc_root: Path = Path("/etc")) -> list[str]:
        """/etc read-only; in a networked profile, the resolver copy over
        /etc/resolv.conf. When the host's resolv.conf is a symlink (WSL points
        it into /mnt/wsl, systemd into /run), a bind cannot land on it inside
        the sandbox (the target does not exist there), so /etc is then bound
        entry by entry without it, and symlinks into /mnt, /run or /init,
        which would dangle anyway, are left out."""
        resolv = self.home / "etc" / "resolv.conf"
        if not network or not resolv.is_file():
            return ["--ro-bind", etc_root.as_posix(), "/etc"]
        if not (etc_root / "resolv.conf").is_symlink():
            return ["--ro-bind", etc_root.as_posix(), "/etc", "--ro-bind", str(resolv), "/etc/resolv.conf"]
        args = ["--dir", "/etc"]
        for entry in sorted(os.scandir(etc_root), key=lambda e: e.name):
            if entry.name == "resolv.conf":
                continue
            dst = f"/etc/{entry.name}"
            if entry.is_symlink():
                target = os.readlink(entry.path)
                if any(target == p or target.startswith(p + "/") for p in ("/mnt", "/run", "/init")):
                    continue
                args += ["--symlink", target, dst]
            else:
                args += ["--ro-bind", entry.path, dst]
        return args + ["--ro-bind", str(resolv), "/etc/resolv.conf"]

    def bwrap_argv(self) -> list[str]:
        network = (self.profile not in NETWORK_OFF) if self.net is None else self.net
        argv = [
            "bwrap", "--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid",
            "--unshare-ipc", "--unshare-uts",
            "--ro-bind", "/usr", "/usr", *self.etc_args(network),
            "--symlink", "usr/bin", "/bin", "--symlink", "usr/sbin", "/sbin",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--ro-bind", str(self.home / "toolchain"), OPT_TOOLCHAIN,
            "--ro-bind", str(self.home / "venv"), OPT_VENV,
        ]
        if not network:
            argv.append("--unshare-net")
        if self.guard:
            argv += ["--ro-bind", str(self.home / "guard"), OPT_GUARD]
        argv += ["--bind", str(self.root / "home"), RUNNER_HOME, "--bind", str(self.work), RUNNER_WORK]
        for bind in self.binds:
            argv += ["--ro-bind" if bind.ro else "--bind", str(bind.src), bind.dst]
        return argv

    def _check_binds(self) -> None:
        for bind in [*self.binds, Bind(self.root, "")]:
            text = bind.src.as_posix()
            if text == "/mnt" or text.startswith("/mnt/") or text in ("/init", "/run") or text.startswith("/run/"):
                raise EvalError(f"refused: {text} may never be bound into a sandbox")

    # --- run ---------------------------------------------------------------------------------

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        stdin: bytes | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
    ) -> Result:
        full_env = {**self.base_env(), **self.env, **(env or {}), **self.secret_env}
        if self.mode == "bwrap":
            self._check_binds()
            chdir = self.inside(cwd) if cwd is not None else RUNNER_WORK
            command = [*self.bwrap_argv(), "--chdir", chdir, "--", *argv]
            real_cwd = None
        else:
            command = list(argv)
            real_cwd = str(cwd) if cwd is not None else str(self.work)
        return spawn(command, env=full_env, cwd=real_cwd, timeout=timeout, stdin=stdin,
                     stdout_path=stdout_path, stderr_path=stderr_path)

    def bash(self, script: str, **kw) -> Result:
        """A step's script, as GitHub runs it: bash --noprofile --norc -eo pipefail."""
        file = self.temp / f".step-{time.monotonic_ns()}.sh"
        file.write_text(script, encoding="utf-8", newline="\n")
        try:
            return self.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", self.inside(file)], **kw)
        finally:
            try:
                file.unlink()
            except OSError:
                pass


def spawn(
    command: Sequence[str],
    *,
    env: dict[str, str],
    cwd: str | None = None,
    timeout: float | None = None,
    stdin: bytes | None = None,
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
) -> Result:
    """Run with an explicit environment, in its own process group, killed on timeout."""
    start = time.monotonic()
    out_fh = open(stdout_path, "wb") if stdout_path else subprocess.PIPE
    err_fh = open(stderr_path, "wb") if stderr_path else subprocess.PIPE
    try:
        try:
            proc = subprocess.Popen(
                list(command), env=env, cwd=cwd, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=out_fh, stderr=err_fh, start_new_session=True,
            )
        except OSError as exc:
            return Result(None, False, b"", str(exc).encode(), time.monotonic() - start, start_failed=True)
        timed_out = False
        try:
            out, err = proc.communicate(input=stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, AttributeError):
                proc.kill()
            out, err = proc.communicate()
        # Leftovers in the command's own process group (a server the hidden
        # harness started and only SIGTERMed through its shell) die with it.
        # Under bwrap the pid namespace already does this; with sandbox: none
        # a leaked server would keep its port and answer a later score.
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        return Result(proc.returncode, timed_out, out or b"", err or b"", time.monotonic() - start)
    finally:
        for fh in (out_fh, err_fh):
            if fh is not subprocess.PIPE:
                fh.close()


# --- the runner's own git ---------------------------------------------------------------------


def git_env(date: str | None = None) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin" if os.name != "nt" else os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/nonexistent") if os.name != "nt" else os.environ.get("USERPROFILE", ""),
        "LANG": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    if os.name == "nt":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    return env


def git(cwd: Path | None, *args: str, date: str | None = None, check: bool = True,
        stdin: bytes | None = None, git_dir: Path | None = None) -> Result:
    """The runner's git: hooks off, a fixed identity, only on clones it made."""
    prefix = ["git", *GIT_SAFE]
    if git_dir is not None:
        prefix += ["--git-dir", str(git_dir)]
    elif cwd is not None:
        prefix += ["-C", str(cwd)]
    env = git_env(date)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    # Never inherit the runner's cwd: git would discover whatever repo holds it.
    result = spawn([*prefix, *args], env=env, stdin=stdin, cwd=os.path.abspath(os.sep))
    if check and not result.ok:
        raise EvalError(
            f"git {' '.join(args[:4])} failed ({result.exit}): {result.stderr.decode('utf-8', 'replace').strip()[:500]}",
            1,
        )
    return result


def git_out(cwd: Path | None, *args: str, **kw) -> str:
    return git(cwd, *args, **kw).stdout.decode("utf-8", "replace").strip()


def which_in(path_dirs: Sequence[Path], name: str) -> Path | None:
    for d in path_dirs:
        found = d / name
        if found.is_file() and os.access(found, os.X_OK):
            return found
    return None


def write_guard(home: Path) -> Path:
    """The stub guard: a `claude` first on PATH that always exits 99."""
    guard = home / "guard"
    guard.mkdir(parents=True, exist_ok=True)
    script = guard / "claude"
    script.write_text(
        "#!/bin/sh\necho 'cadence-eval: claude is disabled in a stub run' >&2\nexit 99\n",
        encoding="utf-8", newline="\n",
    )
    script.chmod(0o755)
    return guard


def copy_resolv(home: Path) -> None:
    """A copy of the host's resolver config, bound over /etc/resolv.conf in
    networked profiles (on WSL the original is a symlink into /mnt or /run)."""
    src = Path("/etc/resolv.conf")
    try:
        data = src.read_bytes()
    except OSError:
        return
    dst = home / "etc" / "resolv.conf"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)


def untar(data: bytes, dest: Path) -> None:
    """Extract a tar stream from our own git archive (no links out, no devices)."""
    import io
    import tarfile

    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="data")
        else:  # Python < 3.11.4
            tar.extractall(dest)  # noqa: S202 - our own archive


def rmtree(path: Path) -> None:
    def onerror(func, p, exc):  # read-only files from git objects
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

    if path.exists():
        shutil.rmtree(path, onerror=onerror)
