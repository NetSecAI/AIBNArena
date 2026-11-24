"""Compensation for the update_object steps whose inverse the request cannot give.

`object_writes` derives a step's compensation from the request where it can: a toggle,
an address added or removed. Where it cannot - a route replaced or deleted, an MTU
changed, an ACL entry or a DHCP option removed - the value to put back lives on the
node, not in the request. The ANI reads the node's configuration just before the write
(its preimage) and this module turns that read into the commands that restore it:

* SR Linux: the running configuration (`info from running /`), parsed as the tree it
  prints; each object the change touches is deleted if the change leaves it in place,
  then replayed as `set / ...` lines from the preimage. One candidate, so the order
  within it does not matter to the commit.
* VyOS: `show configuration commands`, already in `set ...` form; the same delete and
  replay, path by path.
* Linux: one command per change, so one command per compensation - the route as
  `ip -o -4 route show` printed it, replayed with `ip route replace`, or deleted when
  there was none; the MTU `ip -o link show` printed. A traffic-control tree takes more
  than one command to rebuild and gets none.

Only where the preimage can be read and replayed exactly. Everything else - a command
shape this module does not know, a read that does not look like the platform's own
output, a leaf-list SR Linux prints over several lines, a Linux route the kernel holds
twice - gets no compensation, as before: a rollback that does not restore the state it
claims to restore is worse than none. The ANI then holds every derived compensation to
the preflight a caller's rollback_commands get, and drops it rather than let it get the
write refused.
"""
from __future__ import annotations

import ipaddress
import re
import shlex
from dataclasses import dataclass, field

# -- SR Linux ---------------------------------------------------------------------


@dataclass
class SrlBlock:
    """One `<header> {` block of SR Linux's `info` tree."""

    tokens: tuple[str, ...]
    header: str
    leaves: list[tuple[tuple[str, ...], str]] = field(default_factory=list)
    children: list["SrlBlock"] = field(default_factory=list)
    #: A leaf-list (`services [`) sits directly in this block. Not replayed: its flat
    #: form is not one this module writes, so a subtree holding one gets no compensation.
    leaf_list: bool = False


class _Unreplayable(ValueError):
    """The preimage holds something this module does not replay exactly."""


def _words(text: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(text))
    except ValueError:
        return tuple(text.split())


def parse_srl_running(text: str) -> SrlBlock:
    """The tree `info from running /` prints, as blocks; raises on an unbalanced one."""
    root = SrlBlock((), "")
    stack = [root]
    in_leaf_list = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("!!!"):
            continue
        if in_leaf_list:
            if line == "]":
                in_leaf_list = False
            continue
        if line.endswith("{"):
            header = line[:-1].rstrip()
            block = SrlBlock(_words(header), header)
            stack[-1].children.append(block)
            stack.append(block)
        elif line == "}":
            if len(stack) == 1:
                raise ValueError("unbalanced SR Linux configuration tree")
            stack.pop()
        elif line.endswith("["):
            stack[-1].leaf_list = True
            in_leaf_list = True
        elif "[" in line and line.endswith("]"):
            stack[-1].leaf_list = True
        else:
            stack[-1].leaves.append((_words(line), line))
    if len(stack) != 1 or in_leaf_list:
        raise ValueError("unbalanced SR Linux configuration tree")
    return root


def _srl_find(root: SrlBlock, path: tuple[str, ...]) -> SrlBlock | None:
    block, rest = root, path
    while rest:
        best = None
        for child in block.children:
            size = len(child.tokens)
            if size and rest[:size] == child.tokens and (best is None or size > len(best.tokens)):
                best = child
        if best is None:
            return None
        block, rest = best, rest[len(best.tokens):]
    return block


def _srl_replay(block: SrlBlock, path: str) -> list[str]:
    if block.leaf_list:
        raise _Unreplayable(f"a leaf-list under {path}")
    lines = [f"set / {path} {raw}" for _, raw in block.leaves]
    for child in block.children:
        lines.extend(_srl_replay(child, f"{path} {child.header}"))
    if not block.leaves and not block.children:
        # An empty list entry or presence container (`address 10.0.0.1/31 { }`,
        # `drop { }`): setting its path is what creates it.
        lines.append(f"set / {path}")
    return lines


#: The SR Linux command shapes `object_writes` emits, and the object each one touches:
#: a leaf (restored to its value, or deleted back to its default) or a list entry
#: (deleted if the change leaves it, then replayed from the preimage).
_SRL_SHAPES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("set", re.compile(r"^(interface \S+) (admin-state|mtu) \S+$"), "leaf"),
    ("set", re.compile(r"^(interface \S+ subinterface \S+) (admin-state) \S+$"), "leaf"),
    ("set", re.compile(r"^(interface \S+ subinterface \S+ ipv4) (admin-state) \S+$"), "leaf"),
    ("set", re.compile(r"^(network-instance \S+) (admin-state) \S+$"), "leaf"),
    ("*", re.compile(r"^(interface \S+ subinterface \S+ ipv4 address \S+)$"), "entry"),
    ("set", re.compile(r"^(network-instance \S+ next-hop-groups group \S+) .+$"), "entry"),
    ("*", re.compile(r"^(network-instance \S+ static-routes route \S+)(?: .+)?$"), "entry"),
    ("delete", re.compile(r"^(acl acl-filter \S+ type \S+ entry \S+)$"), "entry"),
    ("delete", re.compile(r"^(acl interface \S+)$"), "entry"),
    ("delete", re.compile(r"^(acl acl-filter \S+ type \S+)$"), "entry"),
)


def _srl_object(command: str) -> tuple[str, tuple[str, ...], str] | None:
    """(verb, object path, leaf|entry) for a command of a known shape, else None."""
    tokens = _words(command)
    if len(tokens) < 3 or tokens[0] not in {"set", "delete"} or tokens[1] != "/":
        return None
    verb, path = tokens[0], " ".join(tokens[2:])
    for shape_verb, pattern, kind in _SRL_SHAPES:
        if shape_verb not in {"*", verb}:
            continue
        match = pattern.match(path)
        if match:
            groups = [group for group in match.groups() if group]
            return verb, tuple(" ".join(groups).split()), kind
    return None


def srl_compensation(commands: list[str], running: str) -> list[str] | None:
    """Commands that put back what `commands` change, from the running configuration
    read just before them; None when that cannot be done exactly."""
    try:
        root = parse_srl_running(running)
    except ValueError:
        return None
    # Every running configuration has ports and network instances (mgmt0, mgmt at the
    # least): a read without both is not one, and must not pass for an empty node.
    tops = {block.tokens[0] for block in root.children if block.tokens}
    if not {"interface", "network-instance"} <= tops:
        return None
    objects: dict[tuple[str, ...], tuple[str, bool]] = {}
    for command in commands:
        parsed = _srl_object(command)
        if parsed is None:
            return None
        verb, path, kind = parsed
        objects[path] = (kind, verb == "set")  # the last step on an object decides
    compensation: list[str] = []
    try:
        for path, (kind, exists_after) in objects.items():
            if kind == "leaf":
                container, leaf = path[:-1], path[-1]
                block = _srl_find(root, container)
                if block is None:
                    return None  # the port or instance the leaf hangs off is not in the read
                before = next((raw for words, raw in block.leaves if words[:1] == (leaf,)), None)
                if before is not None:
                    compensation.append(f"set / {' '.join(container)} {before}")
                elif exists_after:
                    compensation.append(f"delete / {' '.join(path)}")
                continue
            anchor = _srl_find(root, path[:2])
            if anchor is None and path[0] != "acl":
                return None  # no such port or network instance in the read
            before_block = _srl_find(root, path)
            if exists_after:
                compensation.append(f"delete / {' '.join(path)}")
            if before_block is not None:
                compensation.extend(_srl_replay(before_block, " ".join(path)))
    except _Unreplayable:
        return None
    return compensation or None


# -- VyOS -------------------------------------------------------------------------

#: Leaves `object_writes` sets without a value: the leaf itself is the object.
_VYOS_VALUELESS = frozenset({"disable", "disable-forwarding"})


def vyos_compensation(commands: list[str], shown: str) -> list[str] | None:
    """Commands that put back what `commands` change, from `show configuration
    commands` read just before them; None when that cannot be done exactly."""
    preimage: list[tuple[tuple[str, ...], str]] = []
    for raw in shown.splitlines():
        line = raw.strip()
        if not line.startswith("set "):
            continue
        try:
            words = tuple(shlex.split(line))
        except ValueError:
            return None
        preimage.append((words[1:], line))
    # Every VyOS configuration declares interfaces: a read without one is not the
    # node's configuration, and must not pass for a node holding nothing.
    if not any(words[:1] == ("interfaces",) for words, _ in preimage):
        return None
    objects: dict[tuple[str, ...], bool] = {}
    for command in commands:
        words = _words(command)
        if len(words) < 2 or words[0] not in {"set", "delete"}:
            return None
        verb, path = words[0], words[1:]
        if verb == "set" and path[-1] not in _VYOS_VALUELESS:
            path = path[:-1]  # the last word is the value
        if not path:
            return None
        objects[path] = verb == "set"  # the last step on an object decides
    outermost = [path for path in objects
                 if not any(other != path and path[:len(other)] == other for other in objects)]
    compensation: list[str] = []
    for path in outermost:
        exists_after = objects[path] or any(
            set_after for other, set_after in objects.items() if other[:len(path)] == path and set_after)
        if exists_after:
            compensation.append("delete " + " ".join(path))
        compensation.extend(line for words, line in preimage if words[:len(path)] == path)
    return compensation or None


# -- Linux --------------------------------------------------------------------------

#: Route flags `ip route show` prints for the state of a route, which `ip route`
#: does not take as input.
_LINUX_ROUTE_STATE_FLAGS = frozenset({"linkdown", "dead"})
_LINUX_ROUTE_TYPES = frozenset({"unicast", "local", "broadcast", "multicast", "throw",
                                "unreachable", "prohibit", "blackhole", "nat"})


def _same_destination(word: str, prefix: str) -> bool:
    if word == prefix:
        return True
    if "default" in (word, prefix):
        return False
    try:
        return ipaddress.ip_network(word, strict=False) == ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return False


def linux_route_compensation(command: str, shown: str) -> str | None:
    """The one command that puts back the route `command` replaces or deletes, from
    `ip -o -4 route show <prefix>` read just before it; None when that is not exact."""
    words = _words(command)
    if len(words) < 4 or words[:2] != ("ip", "route") or words[2] not in {"del", "replace"}:
        return None
    operation, prefix = words[2], words[3]
    lines = []
    for raw in shown.splitlines():
        if not raw.strip():
            continue
        # -o prints a multipath route on one line, its legs separated by backslashes.
        route = [word for word in raw.replace("\\", " ").split() if word not in _LINUX_ROUTE_STATE_FLAGS]
        if not route:
            return None
        destination = route[1] if route[0] in _LINUX_ROUTE_TYPES and len(route) > 1 else route[0]
        if not _same_destination(destination, prefix):
            return None  # not a route to that prefix: not a read to trust
        lines.append(" ".join(route))
    if operation == "del":
        # One route to put back; none means the delete has nothing to remove, two mean
        # the kernel picks which one goes.
        return f"ip route replace {lines[0]}" if len(lines) == 1 else None
    if not lines:
        return f"ip route del {prefix}"
    if len(lines) > 1:
        return None
    route = lines[0].split()
    if "metric" in route and route[route.index("metric") + 1:route.index("metric") + 2] != ["0"]:
        # `replace` without a metric writes metric 0 beside this route rather than over it.
        return None
    return f"ip route replace {lines[0]}"


_LINUX_MTU_COMMAND = re.compile(r"^ip link set dev (\S+) mtu \d+$")


def linux_mtu_compensation(command: str, shown: str) -> str | None:
    """`ip link set dev X mtu <before>`, from `ip -o link show dev X` read just before."""
    match = _LINUX_MTU_COMMAND.match(command.strip())
    if match is None:
        return None
    name = match.group(1)
    for line in shown.splitlines():
        head = re.match(r"^\d+:\s+([^:@\s]+)(?:@\S+)?:\s", line)
        mtu = re.search(r"\smtu (\d+)\b", line)
        if head and head.group(1) == name and mtu:
            return f"ip link set dev {name} mtu {mtu.group(1)}"
    return None


def linux_preimage_read(command: str) -> str | None:
    """The read whose output `linux_*_compensation` needs for `command`, or None."""
    route = re.match(r"^ip route (?:del|replace) (\S+)", command.strip())
    if route:
        return f"ip -o -4 route show {route.group(1)}"
    mtu = _LINUX_MTU_COMMAND.match(command.strip())
    if mtu:
        return f"ip -o link show dev {mtu.group(1)}"
    return None


def linux_compensation(command: str, shown: str) -> str | None:
    if command.strip().startswith("ip route "):
        return linux_route_compensation(command, shown)
    return linux_mtu_compensation(command, shown)
