from __future__ import annotations

import contextlib
import json
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
import uuid

from vyos.configsession import ConfigSession

session = None
exit_status = 0
try:
    commands = json.load(sys.stdin)
    if not isinstance(commands, list) or not commands:
        raise ValueError("commands must be a non-empty list")
    session_id = f"ani-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    session = ConfigSession(session_id, app="ibn-eval-ani")
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
    output = session.commit()
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

    def deploy(self) -> subprocess.CompletedProcess[str]:
        return self.runner.run(["containerlab", "deploy", "-t", str(self.topology_path)], timeout_seconds=300)

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

    @staticmethod
    def _is_config_command(command: str) -> bool:
        normalized = command.strip().lower()
        return normalized.startswith(CONFIG_COMMAND_PREFIXES)
