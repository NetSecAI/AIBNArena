from __future__ import annotations

import ipaddress
import re
import shlex


FORBIDDEN_TOKENS = {
    "sudo",
    "systemctl",
    "tcpdump",
    "rm",
    "mkfs",
    "dd",
    "reboot",
    "shutdown",
    "poweroff",
    "halt",
    "docker",
    "containerlab",
}

FORBIDDEN_SUBSTRINGS = {
    ":(){",
    "/dev/sd",
    "/dev/nvme",
}

SHELL_INTERPRETERS = frozenset({"sh", "bash", "dash", "ash", "zsh", "ksh"})
EXECUTION_WRAPPERS = frozenset({"command", "env", "nice", "nohup", "setsid"})

_INTERFACE = re.compile(r"[A-Za-z0-9_.:@-]{1,32}\Z")
_TC_HANDLE = re.compile(r"[0-9A-Fa-f]+(?::[0-9A-Fa-f]*)?\Z")
_TC_RATE = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:bit|kbit|mbit|gbit)\Z", re.I)
_TC_SIZE = re.compile(
    r"[0-9]+(?:\.[0-9]+)?(?:[kmgt]?(?:bit|b))?(?:/[0-9]+)?\Z", re.I)
_TC_DELAY = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:us|ms|s)\Z", re.I)
_TC_PERCENT = re.compile(r"[0-9]+(?:\.[0-9]+)?%\Z")
_ROUTE_TIME = re.compile(r"[0-9]+(?:ms|s)?\Z")
_ROUTE_NAME = re.compile(r"[A-Za-z0-9_.:@-]{1,64}\Z")

# The Linux route grammar the adapter authorizes, token by token. Widening it is a
# reviewed change: every word added here is a word a subject may send to a device.
_LINUX_ROUTE_TYPES = frozenset({
    "blackhole", "unreachable", "prohibit", "throw", "unicast", "local",
    "broadcast", "multicast", "nat",
})
_LINUX_ROUTE_NODE_OPTIONS = frozenset({
    "tos", "table", "proto", "scope", "metric", "ttl-propagate",
})
_LINUX_ROUTE_INFO_OPTIONS = frozenset({
    "mtu", "advmss", "as", "rtt", "rttvar", "reordering", "window", "cwnd",
    "initcwnd", "ssthresh", "realms", "src", "rto_min", "hoplimit",
    "initrwnd", "features", "quickack", "congctl", "pref", "expires",
    "fastopen_no_cookie",
})
_LINUX_ROUTE_NH_OPTIONS = frozenset({"dev", "weight", "realm"})
_LINUX_ROUTE_FLAGS = frozenset({"onlink", "pervasive"})
_LINUX_ROUTE_VIA_FAMILIES = frozenset({"inet"})


def _basename(token: str) -> str:
    """Executable name without trusting a path spelling such as ``/bin/rm``."""
    return token.rsplit("/", 1)[-1].lower()


def _effective_executable(tokens: list[str]) -> tuple[int, str] | None:
    """Resolve harmless argv wrappers without trying to emulate a shell."""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if "=" in token and not token.startswith(("/", "./")):
            index += 1
            continue
        executable = _basename(token)
        if executable not in EXECUTION_WRAPPERS:
            return index, executable
        index += 1
        if executable == "env":
            while index < len(tokens) and (
                    tokens[index].startswith("-") or "=" in tokens[index]):
                index += 1
        elif executable == "nice":
            if index < len(tokens) and tokens[index] == "-n":
                index += 2
            else:
                while index < len(tokens) and tokens[index].startswith("-"):
                    index += 1
        else:
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
    return None


def _shell_command_segments(payload: str) -> list[list[str]]:
    """Tokenize command positions so punctuation cannot glue a hidden executable."""
    lexer = shlex.shlex(payload, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    segments: list[list[str]] = []
    current: list[str] = []
    for token in lexer:
        if token and all(character in ";&|()" for character in token):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _forbidden_executable(tokens: list[str]) -> str | None:
    resolved = _effective_executable(tokens)
    if resolved is None:
        return None
    _, executable = resolved
    return executable if executable in FORBIDDEN_TOKENS else None


def check_command_safety(command: str | None) -> tuple[bool, str | None]:
    """Return whether an agent command is allowed for the MVP adapter.

    The platform keeps a safety gate before executing model-proposed actions.
    This function preserves that idea, but the deny-list is adapted to a host-backed
    ContainerLab environment where Docker and lab lifecycle commands must not be
    delegated to the SUT.
    """
    if command is None or not command.strip():
        return False, "empty command"

    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return False, f"cannot parse command: {exc}"

    blocked = _forbidden_executable(tokens)
    if blocked:
        return False, f"forbidden token (executable): {blocked}"

    # `docker exec ... sh -c <payload>` really does invoke a second parser in the
    # node.  Inspect that payload as a command too; otherwise quoting turns an entire
    # forbidden command into one harmless-looking outer token.  This remains a coarse
    # defence in depth, not the authorization boundary: untrusted configuration uses
    # the backend-positive parsers below rather than generic `run_shell`.
    resolved = _effective_executable(tokens)
    if resolved is None:
        return False, "command has no executable"
    executable_index, executable = resolved
    if executable in SHELL_INTERPRETERS:
        command_option = next(
            (index for index, token in enumerate(
                tokens[executable_index + 1:], start=executable_index + 1)
             if token.startswith("-") and "c" in token[1:]),
            None,
        )
        payload_index = command_option + 1 if command_option is not None else len(tokens)
        if payload_index < len(tokens) and tokens[payload_index] == "--":
            payload_index += 1
        if command_option is None or payload_index >= len(tokens):
            return False, "shell interpreter requires an inspected -c payload"
        payload = tokens[payload_index]
        try:
            segments = _shell_command_segments(payload)
        except ValueError as exc:
            return False, f"cannot parse shell payload: {exc}"
        for segment in segments:
            nested_blocked = _forbidden_executable(segment)
            if nested_blocked:
                return False, ("forbidden token (executable) in shell payload: "
                               f"{nested_blocked}")

    lowered_command = command.lower()
    for substring in FORBIDDEN_SUBSTRINGS:
        if substring in lowered_command:
            return False, f"forbidden substring: {substring}"

    return True, None


def validate_linux_config_command(command: str) -> None:
    """Accept only the exact iproute2 forms emitted by the current compiler."""
    parse_linux_config_command(command)


def parse_linux_config_command(command: str) -> list[str]:
    """Return an argv vector for a modelled Linux mutation, or fail closed."""
    if any(character in command for character in ("\x00", "\r", "\n")):
        _linux_error("commands must be one line")
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"cannot parse Linux command: {exc}") from exc
    if len(tokens) < 3:
        _linux_error("command is incomplete")
    if tokens[0] == "ip":
        _validate_iproute2(tokens)
    elif tokens[0] == "tc":
        _validate_tc(tokens)
    else:
        _linux_error("expected ip or tc")
    return tokens


def _linux_error(detail: str) -> None:
    raise ValueError(f"unsupported Linux configuration grammar; {detail}")


def _interface(value: str) -> None:
    if not _INTERFACE.fullmatch(value):
        _linux_error(f"invalid interface name {value!r}")


def _integer(value: str, label: str) -> None:
    if not value.isdecimal():
        _linux_error(f"{label} must be an unsigned integer")


def _bounded_integer(value: str, label: str, minimum: int, maximum: int) -> None:
    _integer(value, label)
    if not minimum <= int(value) <= maximum:
        _linux_error(f"{label} must be between {minimum} and {maximum}")


def _network(value: str) -> None:
    if value == "default":
        return
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        _linux_error(f"invalid route prefix {value!r}")


def _ipv4_network(value: str) -> None:
    if value == "default":
        return
    try:
        parsed = ipaddress.ip_network(value, strict=False)
    except ValueError:
        _linux_error(f"invalid route prefix {value!r}")
    if not isinstance(parsed, ipaddress.IPv4Network):
        _linux_error("Linux route evidence supports IPv4 prefixes only")


def _ipv4_address(value: str, label: str = "route address") -> None:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError:
        _linux_error(f"invalid {label} {value!r}")
    if not isinstance(parsed, ipaddress.IPv4Address):
        _linux_error(f"{label} must be IPv4")


def _address(value: str, *, interface: bool = False) -> None:
    try:
        parser = ipaddress.ip_interface if interface else ipaddress.ip_address
        parser(value)
    except ValueError:
        _linux_error(f"invalid IP value {value!r}")


def _handle(value: str) -> None:
    if not _TC_HANDLE.fullmatch(value):
        _linux_error(f"invalid tc handle {value!r}")


def _validate_iproute2(tokens: list[str]) -> None:
    family, operation = tokens[1], tokens[2]
    if family == "link" and operation == "set":
        rest = tokens[3:]
        if rest[:1] == ["dev"]:
            rest = rest[1:]
        if len(rest) == 2 and rest[1] in {"up", "down"}:
            _interface(rest[0])
            return
        if len(rest) == 3 and rest[1] == "mtu":
            _interface(rest[0])
            _integer(rest[2], "MTU")
            return
        _linux_error("ip link set permits only admin state or MTU")

    if family in {"address", "addr"} and operation in {
            "add", "replace", "del", "delete"}:
        _validate_address(tokens[3:], operation)
        return

    if family == "route" and operation in {
            "add", "replace", "change", "append", "del", "delete"}:
        _validate_route(tokens[3:], operation)
        return
    _linux_error("expected a modelled ip link/address/route mutation")


_ADDRESS_VALUE_OPTIONS = frozenset({"peer", "broadcast", "anycast"})
_ADDRESS_LIFETIME_OPTIONS = frozenset({"valid_lft", "preferred_lft"})
_ADDRESS_FLAGS = frozenset({
    "secondary", "temporary", "nodad", "optimistic", "home",
    "mngtmpaddr", "noprefixroute", "autojoin",
})
_ADDRESS_SCOPES = frozenset({"global", "site", "link", "host", "nowhere"})


def _validate_address(rest: list[str], operation: str) -> None:
    """Validate the exact effective address rows the compiler can compensate.

    Deletes stay minimal. Adds/replacements may carry only options emitted from the
    successful family-specific ``ip -o addr show`` evidence parser; no free-form
    iproute2 tail crosses this authorization boundary.
    """
    if not rest:
        _linux_error("address prefix is missing")
    _address(rest[0], interface=True)
    if operation in {"del", "delete"}:
        if len(rest) != 3 or rest[1] != "dev":
            _linux_error("ip address delete requires '<prefix> dev <interface>'")
        _interface(rest[2])
        return

    seen: set[str] = set()
    index = 1
    while index < len(rest):
        option = rest[index]
        if option in seen:
            _linux_error(f"duplicate address option {option!r}")
        if option in _ADDRESS_FLAGS:
            seen.add(option)
            index += 1
            continue
        if index + 1 >= len(rest):
            _linux_error(f"address option {option!r} has no value")
        value = rest[index + 1]
        if option in _ADDRESS_VALUE_OPTIONS:
            _address(value, interface=(option == "peer"))
        elif option == "dev":
            _interface(value)
        elif option == "label":
            _interface(value)
        elif option == "scope":
            if value not in _ADDRESS_SCOPES:
                if not value.isdecimal() or not 0 <= int(value) <= 255:
                    _linux_error(f"invalid address scope {value!r}")
        elif option == "metric":
            _integer(value, "address metric")
        elif option in _ADDRESS_LIFETIME_OPTIONS:
            if value != "forever" and not value.isdecimal():
                _linux_error(f"{option} must be 'forever' or seconds")
        else:
            _linux_error(f"unsupported address option {option!r}")
        seen.add(option)
        index += 2
    if "dev" not in seen:
        _linux_error("ip address requires a dev interface")


def _validate_route(rest: list[str], operation: str) -> None:
    """Validate the replayable subset of ``ip -o -4 route show`` exactly."""
    if not rest:
        _linux_error("route destination is missing")
    route_type = None
    if rest[0] in _LINUX_ROUTE_TYPES:
        route_type, rest = rest[0], rest[1:]
    if not rest:
        _linux_error("route destination is missing")
    _ipv4_network(rest[0])
    tail = rest[1:]
    if operation in {"del", "delete"}:
        if route_type is not None:
            _linux_error("route delete takes no route type")
        _validate_route_delete_tail(tail)
        return
    if not tail:
        if route_type in {"blackhole", "unreachable", "prohibit", "throw"}:
            return
        _linux_error("route replacement requires a disposition")

    if "nexthop" in tail:
        first = tail.index("nexthop")
        _validate_route_head(tail[:first], route_type, multipath=True)
        _validate_route_legs(tail[first:])
        return
    _validate_route_head(tail, route_type, multipath=False)


def _validate_route_value(option: str, value: str) -> None:
    if option == "tos":
        try:
            number = int(value, 0)
        except ValueError:
            _linux_error(f"invalid route tos {value!r}")
        if not 0 <= number <= 255:
            _linux_error("route tos must be between 0 and 255")
    elif option == "table":
        if value not in {"local", "main", "default", "all"}:
            _bounded_integer(value, "route table", 1, 2 ** 32 - 1)
    elif option == "proto":
        if value not in {"kernel", "boot", "static"}:
            _bounded_integer(value, "route protocol", 0, 255)
    elif option == "scope":
        if value not in {"host", "link", "global"}:
            _bounded_integer(value, "route scope", 0, 255)
    elif option == "metric":
        _bounded_integer(value, "route metric", 0, 2 ** 32 - 1)
    elif option == "ttl-propagate":
        if value not in {"enabled", "disabled"}:
            _linux_error("ttl-propagate must be enabled or disabled")
    elif option in {"src", "as"}:
        _ipv4_address(value, option)
    elif option in {"mtu", "advmss", "reordering", "window", "cwnd",
                    "initcwnd", "ssthresh", "hoplimit", "initrwnd"}:
        _bounded_integer(value, option, 0, 2 ** 32 - 1)
    elif option in {"rtt", "rttvar", "rto_min", "expires"}:
        if not _ROUTE_TIME.fullmatch(value):
            _linux_error(f"invalid {option} time {value!r}")
    elif option == "features":
        if value != "ecn":
            _linux_error("route features supports only ecn")
    elif option in {"quickack", "fastopen_no_cookie"}:
        if value not in {"0", "1"}:
            _linux_error(f"{option} must be 0 or 1")
    elif option == "pref":
        if value not in {"low", "medium", "high"}:
            _linux_error("route pref must be low, medium or high")
    elif option in {"realms", "congctl", "realm"}:
        if not _ROUTE_NAME.fullmatch(value):
            _linux_error(f"invalid {option} value {value!r}")
    else:
        _linux_error(f"unsupported route option {option!r}")


def _consume_via(tokens: list[str], index: int) -> int:
    if index >= len(tokens) or tokens[index] != "via":
        _linux_error("route next hop must start with via")
    index += 1
    if index < len(tokens) and tokens[index] in _LINUX_ROUTE_VIA_FAMILIES:
        index += 1
    if index >= len(tokens):
        _linux_error("route via form is incomplete")
    _ipv4_address(tokens[index], "next-hop address")
    return index + 1


#: What `ip route del <prefix> ...` may add to select one route among those to the
#: prefix: the next hop, the device, and the node options `ip route show` prints.
_LINUX_ROUTE_DELETE_OPTIONS = frozenset({"via", "dev"} | _LINUX_ROUTE_NODE_OPTIONS)


def _validate_route_delete_tail(tokens: list[str]) -> None:
    """`ip route del <prefix> [via <gateway>] [dev <interface>] [metric|table|proto|scope|tos <value>]`.

    The bare prefix deletes the first matching route; the qualifiers name one of
    several (the compensating rollback of a default route the subject added is
    exactly `del default via <gw> dev <if>`, which was refused before as if the
    qualifiers were extra prefixes). Each qualifier at most once, values checked as
    for a replacement.
    """
    seen: set[str] = set()
    index = 0
    while index < len(tokens):
        option = tokens[index]
        if option not in _LINUX_ROUTE_DELETE_OPTIONS:
            _linux_error(f"unsupported route delete option {option!r}")
        if option in seen:
            _linux_error(f"duplicate route option {option!r}")
        seen.add(option)
        if option == "via":
            index = _consume_via(tokens, index)
            continue
        if index + 1 >= len(tokens):
            _linux_error(f"route option {option!r} has no value")
        value = tokens[index + 1]
        if option == "dev":
            _interface(value)
        else:
            _validate_route_value(option, value)
        index += 2


def _validate_route_head(tokens: list[str], route_type: str | None, *,
                         multipath: bool) -> None:
    seen: set[str] = set()
    via_seen = False
    index = 0
    while index < len(tokens):
        option = tokens[index]
        if option in seen:
            _linux_error(f"duplicate route option {option!r}")
        if option == "via":
            if multipath or route_type in {
                    "blackhole", "unreachable", "prohibit", "throw"}:
                _linux_error("this route form cannot carry a compact next hop")
            index = _consume_via(tokens, index)
            via_seen = True
            seen.add(option)
            continue
        if option in _LINUX_ROUTE_FLAGS:
            if multipath or not via_seen:
                _linux_error(f"route flag {option!r} requires a compact next hop")
            seen.add(option)
            index += 1
            continue
        if option in _LINUX_ROUTE_NH_OPTIONS:
            if multipath or not via_seen:
                _linux_error(f"route option {option!r} requires a compact next hop")
            if index + 1 >= len(tokens):
                _linux_error(f"route option {option!r} has no value")
            value = tokens[index + 1]
            if option == "dev":
                _interface(value)
            elif option == "weight":
                _bounded_integer(value, "route weight", 1, 256)
            else:
                _validate_route_value(option, value)
            seen.add(option)
            index += 2
            continue
        if option in _LINUX_ROUTE_NODE_OPTIONS | _LINUX_ROUTE_INFO_OPTIONS:
            if index + 1 >= len(tokens):
                _linux_error(f"route option {option!r} has no value")
            if option == "as" and tokens[index + 1] == "to":
                if index + 2 >= len(tokens):
                    _linux_error("route as-to option has no address")
                value, width = tokens[index + 2], 3
            else:
                value, width = tokens[index + 1], 2
            _validate_route_value(option, value)
            seen.add(option)
            index += width
            continue
        _linux_error(f"unsupported route option {option!r}")
    discard = route_type in {"blackhole", "unreachable", "prohibit", "throw"}
    if not multipath and not discard and not via_seen:
        _linux_error("route replacement requires one compact next hop")


def _validate_route_legs(tokens: list[str]) -> None:
    index = 0
    while index < len(tokens):
        if tokens[index] != "nexthop":
            _linux_error("invalid multipath next-hop sequence")
        index = _consume_via(tokens, index + 1)
        seen: set[str] = set()
        while index < len(tokens) and tokens[index] != "nexthop":
            option = tokens[index]
            if option in seen:
                _linux_error(f"duplicate multipath option {option!r}")
            if option in _LINUX_ROUTE_FLAGS:
                seen.add(option)
                index += 1
                continue
            if option not in _LINUX_ROUTE_NH_OPTIONS:
                _linux_error(f"unsupported multipath option {option!r}")
            if index + 1 >= len(tokens):
                _linux_error(f"multipath option {option!r} has no value")
            value = tokens[index + 1]
            if option == "dev":
                _interface(value)
            elif option == "weight":
                _bounded_integer(value, "multipath weight", 1, 256)
            else:
                _validate_route_value(option, value)
            seen.add(option)
            index += 2


def _validate_tc(tokens: list[str]) -> None:
    family, operation = tokens[1], tokens[2]
    if operation not in {"add", "replace", "change", "del", "delete"}:
        _linux_error("unsupported tc operation")
    rest = tokens[3:]
    if len(rest) < 2 or rest[0] != "dev":
        _linux_error("tc command requires 'dev <interface>'")
    _interface(rest[1])
    rest = rest[2:]
    if family == "qdisc":
        _validate_qdisc(rest, operation)
        return
    if family == "class":
        _validate_class(rest, operation)
        return
    if family == "filter":
        _validate_filter(rest, operation)
        return
    _linux_error("expected tc qdisc/class/filter")


def _validate_qdisc(rest: list[str], operation: str) -> None:
    if not rest or rest[0] != "root":
        _linux_error("qdisc must address the root")
    tail = rest[1:]
    if operation in {"del", "delete"} and not tail:
        return
    if operation in {"del", "delete"}:
        _linux_error("qdisc delete has unsupported selectors")
    if len(tail) == 5 and tail[0] == "handle" and tail[2:4] == ["htb", "default"]:
        _handle(tail[1])
        _integer(tail[4], "default class")
        return
    if len(tail) == 3 and tail[:2] == ["netem", "delay"] and _TC_DELAY.fullmatch(tail[2]):
        return
    if len(tail) == 3 and tail[:2] == ["netem", "corrupt"] and _TC_PERCENT.fullmatch(tail[2]):
        return
    _linux_error("unsupported qdisc hierarchy or netem form")


def _validate_class(rest: list[str], operation: str) -> None:
    if operation in {"del", "delete"}:
        if len(rest) == 2 and rest[0] == "classid":
            _handle(rest[1])
            return
        _linux_error("class delete requires one classid")
    if len(rest) < 7 or rest[0] != "parent" or rest[2] != "classid":
        _linux_error("class mutation is missing parent or classid")
    _handle(rest[1])
    _handle(rest[3])
    if rest[4:6] != ["htb", "rate"] or not _TC_RATE.fullmatch(rest[6]):
        _linux_error("class mutation requires an HTB rate")
    tail = rest[7:]
    seen: set[str] = set()
    options = {
        "ceil", "prio", "burst", "cburst", "quantum", "mtu", "mpu",
        "overhead", "linklayer",
    }
    while tail:
        if len(tail) < 2 or tail[0] not in options or tail[0] in seen:
            _linux_error("unsupported or duplicate class option")
        key, value = tail[0], tail[1]
        if key == "ceil" and not _TC_RATE.fullmatch(value):
            _linux_error("invalid HTB ceiling")
        if key == "prio":
            _integer(value, "class priority")
        if key in {"burst", "cburst", "quantum", "mtu", "mpu"} \
                and not _TC_SIZE.fullmatch(value):
            _linux_error(f"invalid HTB {key}")
        if key == "overhead" and not re.fullmatch(r"-?[0-9]+", value):
            _linux_error("invalid HTB overhead")
        if key == "linklayer" and value.lower() not in {
                "ethernet", "atm", "adsl"}:
            _linux_error("invalid HTB linklayer")
        seen.add(key)
        tail = tail[2:]


def _validate_filter(rest: list[str], operation: str) -> None:
    prefix = ["parent"]
    if rest[:1] != prefix or len(rest) < 7:
        _linux_error("filter is missing its parent")
    _handle(rest[1])
    if rest[2:7] != ["protocol", "ip", "prio", rest[5], "u32"]:
        _linux_error("only IPv4 u32 filters are modelled")
    _integer(rest[5], "filter priority")
    tail = rest[7:]
    if operation in {"del", "delete"} and not tail:
        return
    # A classifier on the source or the destination prefix: the one field the u32
    # match reads differs, the command and its checks do not.
    if operation != "add" or len(tail) != 6 or tail[:2] != ["match", "ip"] \
            or tail[2] not in {"src", "dst"} or tail[4] != "flowid":
        _linux_error("unsupported u32 filter matcher")
    _network(tail[3])
    _handle(tail[5])
