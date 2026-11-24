from __future__ import annotations

import contextlib
import json
import secrets
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .safety import check_command_safety, parse_linux_config_command
from .types import CommandResult, LabNode


@dataclass(frozen=True)
class LocalCommandRunner:
    """Small subprocess wrapper with explicit argument lists."""

    timeout_seconds: int = 30

    def run(
        self,
        args: list[str],
        timeout_seconds: int | None = None,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            input=input_text,
            timeout=timeout_seconds or self.timeout_seconds,
        )


CONFIG_COMMAND_PREFIXES = ("set /", "delete /")
SRL_COMMIT_CONFIRMED = "All changes have been committed. Leaving candidate mode."
#: What sr_cli answers when the candidate already equals the running datastore.
#: Proof of the requested state only where the caller asked for idempotent writes.
SRL_COMMIT_NOOP = "Nothing to commit. Leaving candidate mode."


def _linux_already_in_state(command: str, stderr: str) -> bool:
    """Whether a failed `ip` command asked for a state the host is already in."""
    words = command.split()
    if len(words) < 3 or words[0] != "ip":
        return False
    error = stderr.lower()
    verb = words[2]
    if words[1] in ("address", "addr", "a"):
        if verb in ("del", "delete"):
            return "cannot assign requested address" in error or "address not available" in error
        if verb == "add":
            return "file exists" in error
    if words[1] in ("route", "r"):
        if verb in ("del", "delete"):
            return "no such process" in error
        if verb == "add":
            return "file exists" in error
    return False
VYOS_COMMIT_CONFIRMED = "__ANI_VYOS_COMMIT_CONFIRMED__"
VYOS_DISCARD_CONFIRMED = "__ANI_VYOS_DISCARD_CONFIRMED__"


class IndeterminateExecutionError(RuntimeError):
    """The writer may have changed active state but did not confirm its outcome."""


class NoChangeExecutionError(RuntimeError):
    """The device answered that the requested state was already in place; nothing changed.

    A determinate outcome, unlike IndeterminateExecutionError: the active datastore is
    exactly what it was, so there is nothing to reconcile and nothing to roll back.
    """


def _output_tail(completed: subprocess.CompletedProcess[str], *, lines: int = 6,
                 width: int = 400, stdout: str | None = None,
                 stderr: str | None = None) -> str:
    """The last lines a CLI printed, so a refused batch names the refusing line.

    The lifecycle keeps only the exception text, and sr_cli reports a rejected
    line on stdout and the failed commit on stderr; without both, a broken
    reference state is indistinguishable from a transport failure. The newest
    line is the one that names the failure, so a long tail is cut from the front.
    """
    parts = []
    for stream, text in (("stdout", completed.stdout if stdout is None else stdout),
                         ("stderr", completed.stderr if stderr is None else stderr)):
        if not isinstance(text, str):
            continue
        kept = [line.strip() for line in text.splitlines() if line.strip()]
        if kept:
            joined = " | ".join(kept[-lines:])
            if len(joined) > width:
                joined = "..." + joined[-width:]
            parts.append(f"{stream} tail: {joined}")
    return ("; " + "; ".join(parts)) if parts else ""

# User commands never enter this program's source or argv. They arrive as JSON token
# arrays on stdin and are applied through VyOS's write API in one private candidate.
# A failed set/delete/commit discards that candidate before the process reports failure;
# therefore a returned non-zero status means no active configuration was committed.
VYOS_CONFIG_SESSION_DRIVER = r"""
import gc
import json
import os
import sys
import tempfile

from vyos.configsession import ConfigSession, ConfigSessionError


def commit(session):
    # vyos-configd writes a commit script's messages, the reason a commit is refused among
    # them, to the path /proc/<session id>/fd/1 links to. The session is named by this
    # process for that, and stdout is a file during the commit: a pipe's link is no path.
    with tempfile.NamedTemporaryFile("w+", prefix="ani-commit-") as messages:
        sys.stdout.flush()
        saved = os.dup(1)
        os.dup2(messages.fileno(), 1)
        try:
            output, refusal = session.commit(), None
        except ConfigSessionError as exc:
            # Only the text: holding the exception would tie this frame, and the session
            # in it, into a cycle that outlives the teardown below.
            output, refusal = None, str(exc).strip()
        finally:
            os.dup2(saved, 1)
            os.close(saved)
        messages.seek(0)
        text = messages.read().strip()
    if refusal is not None:
        raise ConfigSessionError("\n".join(part for part in (text, refusal) if part))
    return "\n".join(part for part in (text, (output or "").strip()) if part)


session = None
exit_status = 0
try:
    commands = json.load(sys.stdin)
    if not isinstance(commands, list) or not commands:
        raise ValueError("commands must be a non-empty list")
    session = ConfigSession(str(os.getpid()), app="ibn-eval-ani")
    for tokens in commands:
        if not isinstance(tokens, list) or len(tokens) < 2:
            raise ValueError("each command needs a verb and configuration path")
        verb, path = tokens[0], tokens[1:]
        if verb == "set":
            session.set(path)
        elif verb == "delete":
            session.delete(path)
        else:
            raise ValueError(f"unsupported configuration verb: {verb!r}")
    output = commit(session)
    if output:
        print(output)
    print("__ANI_VYOS_COMMIT_CONFIRMED__", file=sys.stderr)
except Exception as exc:
    discard_error = None
    discard_confirmed = session is None
    if session is not None:
        try:
            session.discard()
            discard_confirmed = True
        except Exception as discard_exc:
            discard_error = discard_exc
    print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
    if discard_error is not None:
        print(
            f"discard failed: {type(discard_error).__name__}: {discard_error}",
            file=sys.stderr,
        )
    if discard_confirmed:
        print("__ANI_VYOS_DISCARD_CONFIRMED__", file=sys.stderr)
    exit_status = 1
finally:
    if session is not None:
        # The pinned 1.5 image tears down sessions in __del__. Force that while
        # imports and the interpreter are still live instead of relying on shutdown.
        closing_session = session
        session = None
        del closing_session
        gc.collect()
if exit_status:
    raise SystemExit(exit_status)
""".strip()

# Configuration paths cross stdin as JSON for the same reason write commands do:
# `get_running_config` accepts them from an ANI caller, so interpolating one into a
# vbash script would turn a read operation into arbitrary shell source.  This fixed
# program passes the path only as argv data to VyOS's `showConfig` API.
VYOS_SHOW_CONFIG_DRIVER = r"""
import json
import subprocess
import sys

path = json.load(sys.stdin)
if not isinstance(path, list) or any(not isinstance(token, str) for token in path):
    raise ValueError("configuration path must be a JSON string array")
completed = subprocess.run(
    ["/bin/cli-shell-api", "--show-active-only", "showConfig", *path],
    check=False,
    capture_output=True,
    text=True,
)
sys.stdout.write(completed.stdout)
sys.stderr.write(completed.stderr)
raise SystemExit(completed.returncode)
""".strip()

VYOS_SHELL_CONTROL_TOKENS = frozenset({
    ";", "&&", "||", "|", "&", ">", ">>", "<", "<<", "(", ")",
})

SRL_CLI_CONTROL_TOKENS = frozenset({";", "|", "&", ">", ">>", "<", "<<"})

#: What an EOS configuration line may not start with, after an optional `no` or
#: `default`. The session is the executor's (`configure`, `commit`, `abort`, `end`,
#: and `exit` or `quit`, which at the top level leave it for exec mode, where the
#: lines after them would run as commands; `do` runs one from inside it);
#: the rest leaves the configuration for the switch's shell, flash or lifecycle --
#: `bash` and `run` execute programs, `event-handler`, `daemon`, `schedule` and
#: `alias` install ones that run later, `copy`/`delete`/`write`/`rename` touch flash,
#: `reload`, `zerotouch` and `boot` restart the switch -- or its access (`username`,
#: `aaa`, `management`), which is the lab's and not the network under repair.
EOS_FORBIDDEN_VERBS = frozenset({
    "configure", "commit", "abort", "end", "exit", "quit", "do", "bash", "run", "event-handler", "daemon",
    "schedule", "alias", "copy", "delete", "write", "rename", "reload", "zerotouch",
    "boot", "username", "aaa", "management", "enable", "disable", "terminal", "show",
    "clear", "send",
})
EOS_CLI_CONTROL_TOKENS = frozenset({"|", ">", ">>"})


#: The `!` notes of an EOS session that mean a line will not take effect.
EOS_FATAL_NOTES = ("Interface does not exist",)


def _eos_errors(output: str) -> list[str]:
    """The lines an EOS session prints when a line does not apply as written.

    `%` lines are errors (`% Invalid input at line 7`). A `!` line at the margin is a
    note, and most say nothing against the line: re-entering the MLAG peer-link or
    its SVI on an aggregate that already holds them prints `! Interface
    Port-Channel999 is configured as the MLAG peer link`, which a reference state
    re-applied on a healthy switch would otherwise fail on. One does: `! Interface
    does not exist. The configuration will not take effect until the module is
    inserted.` EOS keeps such a line for a module a container never gets, where the
    other platforms refuse it outright, so it fails the batch too. A diff never
    starts a line with either: its context lines are indented by one space.
    """
    return [line.strip() for line in output.splitlines()
            if line.startswith("%")
            or (line.startswith("! ") and any(note in line for note in EOS_FATAL_NOTES))]


def _without_protocol_markers(stderr: str) -> str:
    markers = {VYOS_COMMIT_CONFIRMED, VYOS_DISCARD_CONFIRMED}
    retained = [line for line in stderr.splitlines(keepends=True)
                if line.strip() not in markers]
    return "".join(retained)


class ContainerLabExecutor:
    """Executes lifecycle and node commands against a ContainerLab lab."""

    #: Set inside `idempotent_writes`; a class default so every executor starts strict.
    _accept_noop_commit = False

    def __init__(self, topology_path: Path, runner: LocalCommandRunner | None = None):
        self.topology_path = Path(topology_path)
        self.runner = runner or LocalCommandRunner()

    @contextlib.contextmanager
    def idempotent_writes(self):
        """Inside this block a write that finds its state already in place is a proven write.

        The judge's restoration re-applies the reference; when the subject's repair
        already restored it exactly, sr_cli answers "Nothing to commit" and leaves
        candidate mode, which is the requested state, stated natively (E3, 2026-09-16:
        a passed remove_ip episode was recorded as failed in restore_destroy for this).
        Outside the block a no-op is reported as no change (NoChangeExecutionError):
        for a subject it means its command changed nothing, and the record must not
        count that as a mutation. Until 2026-09-20 it was reported as indeterminate,
        which blocked the transaction's rollback and moved the node's transaction head
        for a write that never happened (63 such outcomes in E4).
        """
        previous = self._accept_noop_commit
        self._accept_noop_commit = True
        try:
            yield
        finally:
            self._accept_noop_commit = previous

    #: containerlab returns once every node reports ready, and a cEOS switch takes about
    #: 90 s to: campus_large, eleven of them beside three VyOS routers, deployed in
    #: 335 s alone on the host (2026-10-26), past the 300 s this was.
    DEPLOY_TIMEOUT_SECONDS = 900

    def deploy(self) -> subprocess.CompletedProcess[str]:
        return self.runner.run(["containerlab", "deploy", "-t", str(self.topology_path)],
                               timeout_seconds=self.DEPLOY_TIMEOUT_SECONDS)

    def destroy(self) -> subprocess.CompletedProcess[str]:
        return self.runner.run(
            ["containerlab", "destroy", "-t", str(self.topology_path), "--cleanup"],
            timeout_seconds=300,
        )

    def inspect(self) -> subprocess.CompletedProcess[str]:
        return self.runner.run(["containerlab", "inspect", "-t", str(self.topology_path)])

    def run_shell(self, node: LabNode, command: str, timeout_seconds: int | None = None) -> CommandResult:
        safe, reason = check_command_safety(command)
        if not safe:
            return CommandResult(
                target=node.name,
                command=command,
                returncode=126,
                safe=False,
                reason=reason,
                stderr=reason or "unsafe command",
            )

        try:
            command_args = shlex.split(command)
        except ValueError as exc:
            return CommandResult(
                target=node.name,
                command=command,
                returncode=126,
                safe=False,
                reason=str(exc),
                stderr=str(exc),
            )

        completed = self.runner.run(
            ["docker", "exec", node.container_name, *command_args],
            timeout_seconds=timeout_seconds,
        )
        if self._accept_noop_commit and completed.returncode != 0 and _linux_already_in_state(command, completed.stderr):
            # Restoration only (idempotent_writes): the address or route this command
            # removes is already gone, or the one it adds is already there, so the
            # host is in the state the command asks for. E3 2026-09-16: a subject had
            # removed the fault's wrong address itself and the judge's restore then
            # failed on "Address not available", losing the episode's verdict.
            return CommandResult(
                target=node.name,
                command=command,
                returncode=0,
                stdout=f"already in the requested state ({completed.stderr.strip()})",
                stderr=completed.stderr,
            )
        return CommandResult(
            target=node.name,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def run_linux_config(
        self,
        node: LabNode,
        command: str,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        """Execute one positively identified Linux configuration mutation.

        Generic ``run_shell`` is needed for reviewed setup scripts and observations;
        it is not an authorization boundary for caller-authored repair commands.
        """
        try:
            command_args = parse_linux_config_command(command)
        except ValueError as exc:
            reason = str(exc)
            return CommandResult(
                target=node.name,
                command=command,
                returncode=126,
                safe=False,
                reason=reason,
                stderr=reason,
            )
        completed = self.runner.run(
            ["docker", "exec", node.container_name, *command_args],
            timeout_seconds=timeout_seconds,
        )
        return CommandResult(
            target=node.name,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def run_vyos_cli_batch(
        self,
        node: LabNode,
        commands: list[str],
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        """Apply all commands through one discardable VyOS candidate session.

        The previous vbash stream committed valid lines even when a sibling line was
        rejected, and interpolated caller text as shell source. The fixed driver gets
        parsed token arrays over JSON instead: no command is shell code, and it commits
        exactly once only after every set/delete call succeeds. Configuration is not
        saved to disk; these benchmark containers are ephemeral, and a post-commit save
        failure must not misreport an already active commit as a failed/no-op group.
        """
        try:
            tokenized = self.parse_vyos_cli_batch(commands)
        except ValueError as exc:
            reason = str(exc)
            return CommandResult(
                target=node.name,
                command="\n".join(commands),
                returncode=126,
                safe=False,
                reason=reason,
                stderr=reason,
            )

        completed = self.runner.run(
            [
                "docker", "exec", "-i", node.container_name,
                "/usr/bin/python3", "-c", VYOS_CONFIG_SESSION_DRIVER,
            ],
            timeout_seconds=timeout_seconds or 120,
            input_text=json.dumps(tokenized),
        )
        commit_confirmed = VYOS_COMMIT_CONFIRMED in completed.stderr
        discard_confirmed = VYOS_DISCARD_CONFIRMED in completed.stderr
        stderr = _without_protocol_markers(completed.stderr)
        if completed.returncode == 0 and not commit_confirmed:
            raise IndeterminateExecutionError(
                "VyOS writer exited successfully without commit confirmation "
                f"(node {node.name}, {len(commands)} command(s))"
                f"{_output_tail(completed, stderr=stderr)}")
        if completed.returncode != 0 and not discard_confirmed:
            raise IndeterminateExecutionError(
                f"VyOS writer exited {completed.returncode} without discard confirmation "
                f"(node {node.name}, {len(commands)} command(s))"
                f"{_output_tail(completed, stderr=stderr)}")
        return CommandResult(
            target=node.name,
            command="\n".join(commands),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=stderr,
            reason=("VyOS discarded the candidate configuration"
                    if completed.returncode else None),
        )

    @staticmethod
    def parse_vyos_cli_batch(commands: list[str]) -> list[list[str]]:
        """Return safe configuration token vectors or raise before device access."""
        if not commands:
            raise ValueError("a VyOS configuration batch must not be empty")
        tokenized: list[list[str]] = []
        for command in commands:
            if any(character in command for character in ("\x00", "\r", "\n")):
                raise ValueError("VyOS configuration commands must be one line")
            try:
                tokens = shlex.split(command)
            except ValueError as exc:
                raise ValueError(f"cannot parse VyOS command: {exc}") from exc
            if len(tokens) < 2 or tokens[0] not in {"set", "delete"}:
                raise ValueError(
                    "VyOS configuration commands must start with 'set' or 'delete'")
            controls = sorted(set(tokens) & VYOS_SHELL_CONTROL_TOKENS)
            if controls:
                raise ValueError(
                    f"VyOS command contains unsupported control token {controls[0]!r}")
            tokenized.append(tokens)
        return tokenized

    def run_vyos_op(self, node: LabNode, command: str, timeout_seconds: int | None = None) -> CommandResult:
        """Run one VyOS operational-mode command (``show interfaces``, ...).

        Operational mode is not reachable from the configure script template, so
        it goes through the op-mode wrapper instead.
        """
        safe, reason = check_command_safety(command)
        if not safe:
            return CommandResult(
                target=node.name,
                command=command,
                returncode=126,
                safe=False,
                reason=reason,
                stderr=reason or "unsafe command",
            )
        completed = self.runner.run(
            [
                "docker", "exec", node.container_name,
                "/opt/vyatta/bin/vyatta-op-cmd-wrapper", *shlex.split(command),
            ],
            timeout_seconds=timeout_seconds,
        )
        return CommandResult(
            target=node.name,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def run_vyos_show_config(
        self,
        node: LabNode,
        path: str | list[str] = "",
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        """Read the active VyOS configuration, optionally under one path."""
        if isinstance(path, list):
            tokens = path
        else:
            if any(character in path for character in ("\x00", "\r", "\n")):
                reason = "VyOS configuration path must be one line"
                return CommandResult(
                    target=node.name,
                    command=f"show {path}".strip(),
                    returncode=126,
                    safe=False,
                    reason=reason,
                    stderr=reason,
                )
            try:
                tokens = shlex.split(path)
            except ValueError as exc:
                reason = f"cannot parse VyOS configuration path: {exc}"
                return CommandResult(
                    target=node.name,
                    command=f"show {path}".strip(),
                    returncode=126,
                    safe=False,
                    reason=reason,
                    stderr=reason,
                )
        command = shlex.join(["show", *tokens])
        if (any(not isinstance(token, str) for token in tokens)
                or any(any(character in token for character in ("\x00", "\r", "\n"))
                       for token in tokens)):
            reason = "VyOS configuration path must be one line"
            return CommandResult(
                target=node.name,
                command=command,
                returncode=126,
                safe=False,
                reason=reason,
                stderr=reason,
            )
        completed = self.runner.run(
            [
                "docker", "exec", "-i", node.container_name,
                "/usr/bin/python3", "-c", VYOS_SHOW_CONFIG_DRIVER,
            ],
            timeout_seconds=timeout_seconds or 60,
            input_text=json.dumps(tokens),
        )
        return CommandResult(
            target=node.name,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def run_srl_cli(self, node: LabNode, command: str, timeout_seconds: int | None = None) -> CommandResult:
        safe, reason = check_command_safety(command)
        if not safe:
            return CommandResult(
                target=node.name,
                command=command,
                returncode=126,
                safe=False,
                reason=reason,
                stderr=reason or "unsafe command",
            )

        if self._is_config_command(command):
            return self.run_srl_cli_batch(node, [command], timeout_seconds=timeout_seconds)

        completed = self.runner.run(
            ["docker", "exec", "-i", node.container_name, "sr_cli"],
            timeout_seconds=timeout_seconds,
            input_text=f"{command}\n",
        )
        return CommandResult(
            target=node.name,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def run_srl_cli_batch(self, node: LabNode, commands: list[str], timeout_seconds: int | None = None) -> CommandResult:
        try:
            self.parse_srl_cli_batch(commands)
        except ValueError as exc:
            reason = str(exc)
            return CommandResult(
                target=node.name,
                command="\n".join(commands),
                returncode=126,
                safe=False,
                reason=reason,
                stderr=reason,
            )

        # `discard stay` first: a batch whose commit SR Linux refused (a leafref to a
        # group that does not exist, a second filter on a subinterface) stays in the
        # shared candidate, and the next batch on that node, whoever sends it, then
        # fails with the stale error (observed 2026-09-28: the judge's restore of leaf2
        # failed with the subject's rejected route). The discard clears that residue and
        # answers "Nothing to discard" on a clean candidate; private candidates are no
        # remedy, they persist per user across sessions on this platform.
        input_text = "\n".join(["enter candidate", "discard stay", *commands, "commit now", "quit", ""])
        completed = self.runner.run(
            ["docker", "exec", "-i", node.container_name, "sr_cli"],
            timeout_seconds=timeout_seconds or 120,
            input_text=input_text,
        )
        # sr_cli may commit successfully and then return a failure while leaving
        # candidate mode. Only its native acknowledgement plus rc=0 proves that the
        # active datastore changed exactly as requested.
        lines = completed.stdout.splitlines()
        commit_confirmed = SRL_COMMIT_CONFIRMED in lines or (
            self._accept_noop_commit and SRL_COMMIT_NOOP in lines)
        if completed.returncode == 0 and not commit_confirmed and SRL_COMMIT_NOOP in lines:
            raise NoChangeExecutionError(
                f"SR Linux answered '{SRL_COMMIT_NOOP.split('.')[0]}': the node is already in "
                f"the requested state, nothing was written (node {node.name}, "
                f"{len(commands)} command(s))")
        if completed.returncode != 0 or not commit_confirmed:
            acknowledgement = "present" if commit_confirmed else "absent"
            raise IndeterminateExecutionError(
                "SR Linux writer did not prove its commit: "
                f"return code {completed.returncode}, exact native acknowledgement "
                f"{acknowledgement} (node {node.name}, {len(commands)} command(s))"
                f"{_output_tail(completed)}")
        return CommandResult(
            target=node.name,
            command="\n".join(commands),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    @staticmethod
    def parse_srl_cli_batch(commands: list[str]) -> list[list[str]]:
        """Validate configuration-only SR Linux lines before opening a candidate."""
        if not commands:
            raise ValueError("an SR Linux configuration batch must not be empty")
        tokenized: list[list[str]] = []
        for command in commands:
            if any(character in command for character in ("\x00", "\r", "\n", ";")):
                raise ValueError("SR Linux configuration commands must be one line")
            try:
                tokens = shlex.split(command)
            except ValueError as exc:
                raise ValueError(f"cannot parse SR Linux command: {exc}") from exc
            if len(tokens) < 3 or tokens[0] not in {"set", "delete"} or tokens[1] != "/":
                raise ValueError(
                    "SR Linux configuration commands must be 'set / <path>' or "
                    "'delete / <path>'")
            controls = sorted(set(tokens) & SRL_CLI_CONTROL_TOKENS)
            if controls:
                raise ValueError(
                    f"SR Linux command contains CLI control token {controls[0]!r}")
            tokenized.append(tokens)
        return tokenized

    def run_eos_cli(self, node: LabNode, command: str, timeout_seconds: int | None = None) -> CommandResult:
        """Run one EOS exec-mode command (`show ...`) and return what EOS printed."""
        if not command.strip() or any(character in command for character in ("\x00", "\r", "\n")):
            reason = "an EOS command must be one non-empty line"
            return CommandResult(target=node.name, command=command, returncode=126,
                                 safe=False, reason=reason, stderr=reason)
        completed = self.runner.run(
            ["docker", "exec", node.container_name, "Cli", "-p", "15", "-c", command],
            timeout_seconds=timeout_seconds or 60,
        )
        # Cli echoes the command it was handed on a `> ` line before some outputs.
        stdout = "\n".join(line for line in completed.stdout.splitlines()
                           if line.strip() != f"> {command}")
        return CommandResult(
            target=node.name,
            command=command,
            returncode=completed.returncode,
            stdout=stdout,
            stderr=completed.stderr,
        )

    def _eos_session(self, node: LabNode, session: str, lines: list[str],
                     timeout_seconds: int, *, leave: bool = True) -> subprocess.CompletedProcess[str]:
        # `end` leaves the session pending; after `commit` or `abort` the session is
        # already closed and `end` would be refused in exec mode.
        script = "\n".join([f"configure session {session}", *lines, *(["end"] if leave else []), ""])
        return self.runner.run(
            ["docker", "exec", "-i", node.container_name, "Cli", "-p", "15"],
            timeout_seconds=timeout_seconds,
            input_text=script,
        )

    def _abort_eos_session(self, node: LabNode, session: str) -> None:
        # Best effort: EOS keeps five pending sessions at most, and one left behind is
        # one fewer for every later writer on the node.
        try:
            self._eos_session(node, session, ["abort"], 60, leave=False)
        except Exception:  # noqa: BLE001 - the caller reports the failure that led here
            pass

    def run_eos_cli_batch(self, node: LabNode, commands: list[str], timeout_seconds: int | None = None) -> CommandResult:
        """Apply EOS configuration lines in one named session: committed whole or not at all.

        Three steps, because an EOS session survives between two `Cli` runs. The lines
        go into a fresh session with no commit, and EOS reports there every line it
        refused; the session's diff then says whether anything would change; only a
        clean session with a diff is committed. Anything else is aborted, so a failure
        returned here means the running configuration is what it was. The one outcome
        this cannot settle is a commit whose acknowledgement was lost, which is raised
        as indeterminate.
        """
        try:
            self.parse_eos_cli_batch(commands)
        except ValueError as exc:
            reason = str(exc)
            return CommandResult(target=node.name, command="\n".join(commands), returncode=126,
                                 safe=False, reason=reason, stderr=reason)

        session = f"ani-{secrets.token_hex(6)}"
        timeout = timeout_seconds or 120
        try:
            staged = self._eos_session(node, session, [*commands, "show session-config diffs"], timeout)
        except Exception:
            self._abort_eos_session(node, session)
            raise
        errors = _eos_errors(staged.stdout + staged.stderr)
        if staged.returncode != 0 or errors:
            self._abort_eos_session(node, session)
            detail = "; ".join(errors) or f"Cli exited {staged.returncode}"
            return CommandResult(
                target=node.name,
                command="\n".join(commands),
                returncode=staged.returncode or 1,
                stdout=staged.stdout,
                stderr=detail,
                reason=f"EOS refused the batch ({detail}); the session was aborted, nothing was committed",
            )
        diff = staged.stdout.strip()
        if not diff:
            self._abort_eos_session(node, session)
            if self._accept_noop_commit:
                return CommandResult(target=node.name, command="\n".join(commands), returncode=0,
                                     stdout="already in the requested state")
            raise NoChangeExecutionError(
                f"EOS reported no difference from the running configuration: the node is "
                f"already in the requested state, nothing was written (node {node.name}, "
                f"{len(commands)} command(s))")

        try:
            committed = self._eos_session(node, session, ["commit"], timeout, leave=False)
        except Exception as exc:
            raise IndeterminateExecutionError(
                f"EOS commit of session {session} on {node.name} did not answer: {exc}") from exc
        commit_errors = _eos_errors(committed.stdout + committed.stderr)
        if committed.returncode == 0 and not commit_errors:
            return CommandResult(target=node.name, command="\n".join(commands), returncode=0,
                                 stdout=diff, stderr=committed.stderr)
        pending = self.run_eos_cli(node, "show configuration sessions")
        if pending.ok and any(line.split()[:2] == [session, "pending"]
                              for line in pending.stdout.splitlines() if line.split()):
            self._abort_eos_session(node, session)
            detail = "; ".join(commit_errors) or f"Cli exited {committed.returncode}"
            return CommandResult(
                target=node.name, command="\n".join(commands), returncode=committed.returncode or 1,
                stdout=committed.stdout, stderr=detail,
                reason=f"EOS refused the commit ({detail}); the session was aborted, nothing was committed",
            )
        raise IndeterminateExecutionError(
            f"EOS commit of session {session} on {node.name} failed without leaving the session "
            f"pending (return code {committed.returncode}){_output_tail(committed)}")

    @staticmethod
    def parse_eos_cli_batch(commands: list[str]) -> list[list[str]]:
        """Validate EOS configuration lines before a session is opened."""
        if not commands:
            raise ValueError("an EOS configuration batch must not be empty")
        tokenized: list[list[str]] = []
        for command in commands:
            if any(character in command for character in ("\x00", "\r", "\n", ";")):
                raise ValueError("EOS configuration commands must be one line each")
            words = command.split()
            if not words:
                raise ValueError("an EOS configuration line must not be empty")
            controls = sorted(set(words) & EOS_CLI_CONTROL_TOKENS)
            if controls:
                raise ValueError(f"EOS command contains CLI control token {controls[0]!r}")
            verb = words[0].lower()
            if verb in {"no", "default"} and len(words) > 1:
                verb = words[1].lower()
            if verb in EOS_FORBIDDEN_VERBS:
                raise ValueError(
                    f"EOS configuration commands must not start with {verb!r}: the session "
                    "is the ANI's, and that command reaches beyond the switch's configuration")
            tokenized.append(words)
        return tokenized

    @staticmethod
    def _is_config_command(command: str) -> bool:
        normalized = command.strip().lower()
        return normalized.startswith(CONFIG_COMMAND_PREFIXES)
