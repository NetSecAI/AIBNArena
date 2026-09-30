"""update_object: change one named object on one node through attributes, not commands.

The write-side counterpart of get_object (ANI v0.2). The agent names a node, the kind
of object, its name and the attributes to change; this module turns that into the
native commands of the node's platform, so the model does not need to know SR Linux,
VyOS or Linux syntax. The vocabulary is bounded on purpose: it covers what the
benchmark's faults break (interfaces, addresses, static routes, the routing instance,
ACLs, DHCP pools, firewall rule sets, zones, traffic shaping) and refuses the rest by
name, so that a request the ANI cannot form is answered before a device sees it.

Every step carries its own compensating command where one can be derived without
reading the device (a toggle, an address added or removed); where it cannot (a route
that replaced an unknown one, a deleted pool), the step has none and the transaction
reports rollback_available false, exactly as a raw update_config without
rollback_commands would.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .compiled_topology import split_interface, vyos_interface_path

OBJECT_WRITE_KINDS = ("interface", "route", "routing", "acl", "dhcp_pool", "firewall_rule", "zone", "qos")

#: What each kind accepts, per platform: shown to the model and enforced here.
ATTRIBUTES: dict[str, dict[str, str]] = {
    "interface": {
        "admin_state": "enable | disable (a port name sets the port; a subinterface name such as ethernet-1/3.0 "
                       "or a VyOS vif such as eth1.10 sets that subinterface)",
        "ipv4_admin_state": "enable | disable: IPv4 on an SR Linux subinterface (a port name means subinterface 0)",
        "mtu": "integer (ports only)",
        "address": "IPv4 address with prefix length, e.g. 10.10.40.1/24 (added unless address_action is delete)",
        "address_action": "add | delete (with address); there is no replace: delete the old address, add the new one",
    },
    "route": {
        "next_hop": "IPv4 next-hop address (SR Linux: a next-hop group is created for it)",
        "next_hop_group": "SR Linux only: an existing next-hop group name",
        "delete": "true removes the static route",
    },
    "routing": {"admin_state": "enable | disable (SR Linux: the network-instance, name = instance, e.g. default; "
                               "VyOS: IPv4 forwarding, name = default)"},
    "acl": {
        "delete": "true removes the ACL filter (SR Linux, name = acl-filter name)",
        "unbind_interfaces": "list of interfaces to unbind first, e.g. [\"ethernet-1/20\"] (with delete)",
        "delete_entry": "entry id, or list of ids, removed from a filter that stays",
    },
    "dhcp_pool": {
        "subnet": "the pool's subnet, e.g. 10.10.20.0/24 (required with any option below)",
        "subnet_id": "integer", "default_router": "IPv4", "name_server": "IPv4 or list",
        "domain_name": "string", "domain_search": "string or list", "lease": "seconds (integer)",
        "static_mapping": "list of {host, mac, ip}",
        "delete_option": "list of option names to delete, e.g. [\"name-server\"]",
        "delete": "true removes the whole pool",
    },
    "firewall_rule": {
        "rule": "rule number (with action or delete), in the rule set the name gives, or in the base "
                "forward filter when the name is forward",
        "action": "accept | drop | reject",
        "delete": "true with rule removes that rule",
        "default_action": "accept | drop | reject (the forward filter: accept | drop)",
    },
    "zone": {
        "from_zone": "source zone (with ruleset)", "ruleset": "firewall rule set name to apply from from_zone",
        "delete_from": "source zone whose policy is removed",
        "add_interface": "interface to put in the zone", "delete_interface": "interface to take out of the zone",
    },
    "qos": {
        "delete_root": "true removes the root qdisc (Linux, name = interface)",
        "htb": "{default: class number, rate, ceil, classes: [{id, rate, ceil, prio}], filters: [{src|dst: prefix, flowid, prio}]} "
               "builds the whole hierarchy on an interface without an HTB root; send delete_root: true in the same set to rebuild one",
        "class": "{id, rate, ceil, prio, parent} changes one HTB class",
        "filter": "{src|dst: prefix, flowid, prio} adds one classifier under the root 1:",
        "delete_filter": "{prio} removes the classifiers of that priority under the root 1:",
    },
}

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,79}$")
_RATE = re.compile(r"^\d+(k|m|g)?bit$", re.I)
_MAC = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


class UnsupportedUpdate(ValueError):
    """A request update_object cannot form: unknown attribute, bad value, wrong platform."""


@dataclass(frozen=True)
class Probe:
    """How to tell, before writing, that a step would change nothing: read `kind`
    `name` with get_object and look for `marker` (case-insensitive substring)."""
    kind: str
    name: str
    marker: str
    present_means_noop: bool = True


@dataclass(frozen=True)
class Step:
    command: str
    rollback: str | None
    #: A step whose compensation is derived from the request alone is only right when
    #: the request changed something: the probe says whether it would. Steps without a
    #: derivable compensation carry no probe and are always sent.
    probe: Probe | None = None


@dataclass(frozen=True)
class CompiledUpdate:
    steps: tuple[Step, ...]
    summary: str

    @property
    def commands(self) -> list[str]:
        return [step.command for step in self.steps]

    @property
    def rollback_commands(self) -> list[str]:
        """The compensation, last step first; empty when any step has none."""
        if any(step.rollback is None for step in self.steps):
            return []
        return [step.rollback for step in reversed(self.steps) if step.rollback]


def compile_update(platform: str, kind: str, name: str, changes: Mapping[str, Any]) -> CompiledUpdate:
    if kind not in OBJECT_WRITE_KINDS:
        raise UnsupportedUpdate(f"kind must be one of {', '.join(OBJECT_WRITE_KINDS)}; got {kind!r}")
    if not _NAME.match(str(name or "")):
        raise UnsupportedUpdate("name must be the object's name as the node writes it")
    if not isinstance(changes, Mapping) or not changes:
        raise UnsupportedUpdate("set must be a non-empty object of attributes to change")
    unknown = sorted(set(changes) - set(ATTRIBUTES[kind]))
    if unknown:
        raise UnsupportedUpdate(
            f"{kind} has no attribute {', '.join(unknown)}; it has {', '.join(ATTRIBUTES[kind])}")
    handler = _HANDLERS.get((kind, platform))
    if handler is None:
        raise UnsupportedUpdate(f"a {platform} node has no {kind} objects to change")
    steps = handler(name, dict(changes))
    if not steps:
        raise UnsupportedUpdate(f"set names no change for {kind} {name}")
    return CompiledUpdate(tuple(steps), f"{kind} {name}: " + ", ".join(f"{k}={_short(v)}" for k, v in changes.items()))


# -- values ---------------------------------------------------------------------

def _short(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _enum(changes: Mapping[str, Any], key: str, allowed: tuple[str, ...]) -> str:
    value = str(changes[key]).strip().lower()
    if value not in allowed:
        raise UnsupportedUpdate(f"{key} must be one of {', '.join(allowed)}; got {changes[key]!r}")
    return value


def _int(changes: Mapping[str, Any], key: str, low: int = 1, high: int = 10 ** 9) -> int:
    try:
        value = int(changes[key])
    except (TypeError, ValueError) as exc:
        raise UnsupportedUpdate(f"{key} must be an integer") from exc
    if not low <= value <= high:
        raise UnsupportedUpdate(f"{key} must be between {low} and {high}")
    return value


def _ip(value: Any, key: str) -> str:
    try:
        return str(ipaddress.IPv4Address(str(value).strip()))
    except ValueError as exc:
        raise UnsupportedUpdate(f"{key} must be an IPv4 address") from exc


def _cidr(value: Any, key: str, *, host: bool) -> str:
    """An address with prefix (host=True keeps the host bits) or a network prefix."""
    text = str(value).strip()
    try:
        if host:
            interface = ipaddress.IPv4Interface(text)
            if "/" not in text:
                raise ValueError("prefix length required")
            return str(interface)
        return str(ipaddress.IPv4Network(text, strict=True))
    except ValueError as exc:
        raise UnsupportedUpdate(f"{key} must be an IPv4 {'address with prefix length' if host else 'prefix'} such as 10.10.40.0/24") from exc


def _prefix_name(name: str) -> str:
    if name.strip().lower() == "default":
        return "0.0.0.0/0"
    return _cidr(name, "route name", host=False)


def _token(value: Any, key: str) -> str:
    text = str(value).strip()
    if not _NAME.match(text):
        raise UnsupportedUpdate(f"{key} must be a plain name (letters, digits, . _ : / -)")
    return text


def _flag(changes: Mapping[str, Any], key: str) -> bool:
    value = changes.get(key)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return bool(value)


def _rate(value: Any, key: str) -> str:
    text = str(value).strip().lower()
    if not _RATE.match(text):
        raise UnsupportedUpdate(f"{key} must be a tc rate such as 8mbit or 794kbit")
    return text


def _listed(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _number(value: Any, key: str, low: int = 1, high: int = 10 ** 9) -> int:
    """An integer attribute nested inside an object attribute (htb.default, a class prio).

    A bare int() would let a wrongly shaped value (an object where a number belongs)
    escape as a Python TypeError; the agent must get the refusal by name instead.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise UnsupportedUpdate(f"{key} must be an integer")
    try:
        number = int(str(value).strip())
    except ValueError as exc:
        raise UnsupportedUpdate(f"{key} must be an integer") from exc
    if not low <= number <= high:
        raise UnsupportedUpdate(f"{key} must be between {low} and {high}")
    return number


# -- interface ------------------------------------------------------------------

def _interface_srl(name: str, changes: dict[str, Any]) -> list[Step]:
    """A port name (ethernet-1/3) or a subinterface name (ethernet-1/3.0, irb0.40).

    Addresses live on subinterfaces, so a bare port name means its subinterface 0
    there; admin_state belongs to whichever of the two the name designates.
    """
    port, index = split_interface(name)
    subinterface = f"/ interface {port} subinterface {index}"
    steps: list[Step] = []
    if "admin_state" in changes:
        state = _enum(changes, "admin_state", ("enable", "disable"))
        other = "disable" if state == "enable" else "enable"
        if name == port:
            # `show interface X` says "is down, reason port-admin-disabled" for a disabled port
            steps.append(Step(f"set / interface {name} admin-state {state}", f"set / interface {name} admin-state {other}",
                              Probe("interface", name, "port-admin-disabled", present_means_noop=(state == "disable"))))
        else:
            # No report states a subinterface's admin state as plainly as the port's, so
            # there is no probe, and without one no compensation can be trusted.
            steps.append(Step(f"set {subinterface} admin-state {state}", None))
    if "ipv4_admin_state" in changes:
        state = _enum(changes, "ipv4_admin_state", ("enable", "disable"))
        steps.append(Step(f"set {subinterface} ipv4 admin-state {state}", None))
    if "mtu" in changes:
        if name != port:
            raise UnsupportedUpdate(f"mtu is set on the port: name {port}, not {name}")
        steps.append(Step(f"set / interface {name} mtu {_int(changes, 'mtu', 1280, 9500)}", None))
    if "address" in changes:
        address = _cidr(changes["address"], "address", host=True)
        action = _enum(changes, "address_action", ("add", "delete")) if "address_action" in changes else "add"
        verb, undo = ("set", "delete") if action == "add" else ("delete", "set")
        steps.append(Step(f"{verb} {subinterface} ipv4 address {address}",
                          f"{undo} {subinterface} ipv4 address {address}",
                          # the port's report lists the addresses of all its subinterfaces
                          Probe("interface", port, address, present_means_noop=(action == "add"))))
    elif "address_action" in changes:
        raise UnsupportedUpdate("address_action needs an address")
    return steps


def _srl_only(changes: Mapping[str, Any], platform: str) -> None:
    if "ipv4_admin_state" in changes:
        raise UnsupportedUpdate(f"ipv4_admin_state is an SR Linux subinterface setting; a {platform} node has none")


def _interface_linux(name: str, changes: dict[str, Any]) -> list[Step]:
    _srl_only(changes, "linux")
    steps: list[Step] = []
    if "admin_state" in changes:
        state = _enum(changes, "admin_state", ("enable", "disable"))
        up, down = ("up", "down") if state == "enable" else ("down", "up")
        # `ip address show dev X` prints the flags: <...,UP,...> when administratively up
        steps.append(Step(f"ip link set dev {name} {up}", f"ip link set dev {name} {down}",
                          Probe("interface", name, ",UP", present_means_noop=(state == "enable"))))
    if "mtu" in changes:
        steps.append(Step(f"ip link set dev {name} mtu {_int(changes, 'mtu', 68, 9500)}", None))
    if "address" in changes:
        address = _cidr(changes["address"], "address", host=True)
        action = _enum(changes, "address_action", ("add", "delete")) if "address_action" in changes else "add"
        probe = Probe("interface", name, f"inet {address}", present_means_noop=(action == "add"))
        if action == "add":
            steps.append(Step(f"ip address replace {address} dev {name}", f"ip address del {address} dev {name}", probe))
        else:
            steps.append(Step(f"ip address del {address} dev {name}", f"ip address replace {address} dev {name}", probe))
    elif "address_action" in changes:
        raise UnsupportedUpdate("address_action needs an address")
    return steps


def _interface_vyos(name: str, changes: dict[str, Any]) -> list[Step]:
    """A plain interface (eth3) or a VLAN subinterface (eth1.10: vif 10 of eth1)."""
    _srl_only(changes, "vyos")
    path = vyos_interface_path(name)
    steps: list[Step] = []
    if "admin_state" in changes:
        state = _enum(changes, "admin_state", ("enable", "disable"))
        # `show interfaces ethernet X` prints the flags: <...,UP,...> when administratively up
        probe = Probe("interface", name, ",UP", present_means_noop=(state == "enable"))
        if state == "enable":
            steps.append(Step(f"delete {path} disable", f"set {path} disable", probe))
        else:
            steps.append(Step(f"set {path} disable", f"delete {path} disable", probe))
    if "mtu" in changes:
        steps.append(Step(f"set {path} mtu {_int(changes, 'mtu', 68, 9500)}", None))
    if "address" in changes:
        address = _cidr(changes["address"], "address", host=True)
        action = _enum(changes, "address_action", ("add", "delete")) if "address_action" in changes else "add"
        verb, undo = ("set", "delete") if action == "add" else ("delete", "set")
        steps.append(Step(f"{verb} {path} address {address}",
                          f"{undo} {path} address {address}",
                          Probe("interface", name, f"inet {address}", present_means_noop=(action == "add"))))
    elif "address_action" in changes:
        raise UnsupportedUpdate("address_action needs an address")
    return steps


# -- route ----------------------------------------------------------------------

def _route_srl(name: str, changes: dict[str, Any]) -> list[Step]:
    prefix = _prefix_name(name)
    steps: list[Step] = []
    if _flag(changes, "delete"):
        if "next_hop" in changes or "next_hop_group" in changes:
            raise UnsupportedUpdate("delete does not combine with a next hop")
        return [Step(f"delete / network-instance default static-routes route {prefix}", None)]
    if "next_hop" in changes and "next_hop_group" in changes:
        raise UnsupportedUpdate("give next_hop or next_hop_group, not both")
    if "next_hop" in changes:
        next_hop = _ip(changes["next_hop"], "next_hop")
        group = "nhg-" + re.sub(r"[^0-9A-Za-z]", "-", prefix)
        steps.append(Step(f"set / network-instance default next-hop-groups group {group} nexthop 1 ip-address {next_hop} admin-state enable", None))
        steps.append(Step(f"set / network-instance default static-routes route {prefix} next-hop-group {group} admin-state enable", None))
    if "next_hop_group" in changes:
        group = _token(changes["next_hop_group"], "next_hop_group")
        steps.append(Step(f"set / network-instance default static-routes route {prefix} next-hop-group {group} admin-state enable", None))
    return steps


def _route_linux(name: str, changes: dict[str, Any]) -> list[Step]:
    prefix = "default" if name.strip().lower() == "default" else _cidr(name, "route name", host=False)
    if _flag(changes, "delete"):
        if "next_hop" in changes:
            raise UnsupportedUpdate("delete does not combine with a next hop")
        return [Step(f"ip route del {prefix}", None)]
    if "next_hop_group" in changes:
        raise UnsupportedUpdate("next_hop_group is SR Linux only; give next_hop")
    if "next_hop" not in changes:
        raise UnsupportedUpdate("route needs next_hop or delete")
    return [Step(f"ip route replace {prefix} via {_ip(changes['next_hop'], 'next_hop')}", None)]


def _route_vyos(name: str, changes: dict[str, Any]) -> list[Step]:
    prefix = _prefix_name(name)
    if _flag(changes, "delete"):
        if "next_hop" in changes:
            raise UnsupportedUpdate("delete does not combine with a next hop")
        return [Step(f"delete protocols static route {prefix}", None)]
    if "next_hop_group" in changes:
        raise UnsupportedUpdate("next_hop_group is SR Linux only; give next_hop")
    if "next_hop" not in changes:
        raise UnsupportedUpdate("route needs next_hop or delete")
    next_hop = _ip(changes["next_hop"], "next_hop")
    return [Step(f"set protocols static route {prefix} next-hop {next_hop}", f"delete protocols static route {prefix} next-hop {next_hop}",
                 Probe("route", prefix, next_hop))]


# -- routing instance, acl (SR Linux) ---------------------------------------------

def _routing_srl(name: str, changes: dict[str, Any]) -> list[Step]:
    state = _enum(changes, "admin_state", ("enable", "disable"))
    other = "disable" if state == "enable" else "enable"
    return [Step(f"set / network-instance {name} admin-state {state}", f"set / network-instance {name} admin-state {other}")]


def _acl_srl(name: str, changes: dict[str, Any]) -> list[Step]:
    if "delete_entry" in changes:
        # The filter stays: removing its binding or the filter itself is the other request.
        if _flag(changes, "delete") or "unbind_interfaces" in changes:
            raise UnsupportedUpdate("delete_entry removes entries from a filter that stays; "
                                    "it does not combine with delete or unbind_interfaces")
        return [Step(f"delete / acl acl-filter {name} type ipv4 entry {_number(entry, 'delete_entry', 0, 65535)}", None)
                for entry in _listed(changes["delete_entry"])]
    if not _flag(changes, "delete"):
        raise UnsupportedUpdate("acl supports delete: true (with unbind_interfaces), or delete_entry")
    steps = []
    for iface in _listed(changes.get("unbind_interfaces") or []):
        # the binding is keyed <port>.<subinterface>; a port name means subinterface 0
        port, index = split_interface(_token(iface, "unbind_interfaces"))
        steps.append(Step(f"delete / acl interface {port}.{index}", None))
    steps.append(Step(f"delete / acl acl-filter {name} type ipv4", None))
    return steps


# -- routing (VyOS) ---------------------------------------------------------------

def _routing_vyos(name: str, changes: dict[str, Any]) -> list[Step]:
    """IPv4 forwarding: VyOS routes unless `system ip disable-forwarding` is set.

    No compensation: nothing reads the setting back as a probe would, and a derived
    inverse of a change that changed nothing would switch forwarding off.
    """
    if name != "default":
        raise UnsupportedUpdate("a VyOS node has one routing instance: name default")
    state = _enum(changes, "admin_state", ("enable", "disable"))
    verb = "delete" if state == "enable" else "set"
    return [Step(f"{verb} system ip disable-forwarding", None)]


# -- VyOS services --------------------------------------------------------------

def _dhcp_pool_vyos(name: str, changes: dict[str, Any]) -> list[Step]:
    base = f"service dhcp-server shared-network-name {name}"
    if _flag(changes, "delete"):
        if len(changes) > 1:
            raise UnsupportedUpdate("delete does not combine with other attributes")
        return [Step(f"delete {base}", None)]
    if "subnet" not in changes:
        raise UnsupportedUpdate("dhcp_pool needs subnet with any option")
    subnet = f"{base} subnet {_cidr(changes['subnet'], 'subnet', host=False)}"
    steps: list[Step] = []
    for option in _listed(changes.get("delete_option") or []):
        steps.append(Step(f"delete {subnet} option {_token(option, 'delete_option')}", None))
    if "subnet_id" in changes:
        steps.append(Step(f"set {subnet} subnet-id {_int(changes, 'subnet_id', 1, 4294967295)}", None))
    if "default_router" in changes:
        steps.append(Step(f"set {subnet} option default-router {_ip(changes['default_router'], 'default_router')}", None))
    if "name_server" in changes:
        servers = [_ip(v, "name_server") for v in _listed(changes["name_server"])]
        steps.append(Step(f"delete {subnet} option name-server", None))
        steps.extend(Step(f"set {subnet} option name-server {server}", None) for server in servers)
    if "domain_name" in changes:
        steps.append(Step(f"set {subnet} option domain-name {_token(changes['domain_name'], 'domain_name')}", None))
    if "domain_search" in changes:
        steps.extend(Step(f"set {subnet} option domain-search {_token(v, 'domain_search')}", None) for v in _listed(changes["domain_search"]))
    if "lease" in changes:
        steps.append(Step(f"set {subnet} lease {_int(changes, 'lease', 60, 31536000)}", None))
    for mapping in _listed(changes.get("static_mapping") or []):
        if not isinstance(mapping, Mapping) or not {"host", "mac", "ip"} <= set(mapping):
            raise UnsupportedUpdate("static_mapping entries need host, mac and ip")
        host = _token(mapping["host"], "static_mapping.host")
        mac = str(mapping["mac"]).strip().lower()
        if not _MAC.match(mac):
            raise UnsupportedUpdate("static_mapping.mac must be a MAC address")
        steps.append(Step(f"set {subnet} static-mapping {host} mac {mac}", None))
        steps.append(Step(f"set {subnet} static-mapping {host} ip-address {_ip(mapping['ip'], 'static_mapping.ip')}", None))
    return steps


#: The base chain a firewall_rule named `forward` designates, rather than a rule set.
FORWARD_FILTER = "forward"


def _firewall_rule_vyos(name: str, changes: dict[str, Any]) -> list[Step]:
    base = name == FORWARD_FILTER
    chain = "firewall ipv4 forward filter" if base else f"firewall ipv4 name {name}"
    steps: list[Step] = []
    if "default_action" in changes:
        # A base chain accepts or drops; reject is a rule set's.
        allowed = ("accept", "drop") if base else ("accept", "drop", "reject")
        steps.append(Step(f"set {chain} default-action {_enum(changes, 'default_action', allowed)}", None))
    if "rule" in changes:
        rule = _int(changes, "rule", 1, 999999)
        if _flag(changes, "delete"):
            steps.append(Step(f"delete {chain} rule {rule}", None))
        elif "action" in changes:
            steps.append(Step(f"set {chain} rule {rule} action {_enum(changes, 'action', ('accept', 'drop', 'reject'))}", None))
        else:
            raise UnsupportedUpdate("rule needs action or delete: true")
    elif "action" in changes or _flag(changes, "delete"):
        raise UnsupportedUpdate("action and delete need a rule number")
    return steps


def _zone_vyos(name: str, changes: dict[str, Any]) -> list[Step]:
    steps: list[Step] = []
    if "from_zone" in changes or "ruleset" in changes:
        if not ("from_zone" in changes and "ruleset" in changes):
            raise UnsupportedUpdate("from_zone and ruleset go together")
        source, ruleset = _token(changes["from_zone"], "from_zone"), _token(changes["ruleset"], "ruleset")
        steps.append(Step(f"set firewall zone {name} from {source} firewall name {ruleset}", f"delete firewall zone {name} from {source}",
                          Probe("zone", name, f"set firewall zone {name} from {source} firewall name '{ruleset}'")))
    if "delete_from" in changes:
        steps.append(Step(f"delete firewall zone {name} from {_token(changes['delete_from'], 'delete_from')}", None))
    if "add_interface" in changes:
        iface = _token(changes["add_interface"], "add_interface")
        steps.append(Step(f"set firewall zone {name} interface {iface}", f"delete firewall zone {name} interface {iface}",
                          Probe("zone", name, f"set firewall zone {name} interface '{iface}'")))
    if "delete_interface" in changes:
        iface = _token(changes["delete_interface"], "delete_interface")
        steps.append(Step(f"delete firewall zone {name} interface {iface}", f"set firewall zone {name} interface {iface}",
                          Probe("zone", name, f"set firewall zone {name} interface '{iface}'", present_means_noop=False)))
    return steps


# -- qos (Linux tc) -------------------------------------------------------------

def _qos_linux(name: str, changes: dict[str, Any]) -> list[Step]:
    steps: list[Step] = []
    if _flag(changes, "delete_root"):
        steps.append(Step(f"tc qdisc del dev {name} root", None))
    if "htb" in changes:
        htb = changes["htb"]
        if not isinstance(htb, Mapping):
            raise UnsupportedUpdate("htb must be an object")
        default = _number(htb.get("default") or 20, "htb.default", 1, 65535)
        steps.append(Step(f"tc qdisc replace dev {name} root handle 1: htb default {default}", None))
        if "rate" in htb:
            rate = _rate(htb["rate"], "htb.rate")
            ceil = _rate(htb.get("ceil") or htb["rate"], "htb.ceil")
            steps.append(Step(f"tc class replace dev {name} parent 1: classid 1:1 htb rate {rate} ceil {ceil}", None))
        for klass in _listed(htb.get("classes") or []):
            steps.append(Step(_class_command(name, klass, "replace"), None))
        for index, flt in enumerate(_listed(htb.get("filters") or []), start=1):
            steps.append(Step(_filter_command(name, flt, index, "filters"), None))
    if "class" in changes:
        steps.append(Step(_class_command(name, changes["class"], "change"), None))
    if "delete_filter" in changes:
        spec = changes["delete_filter"]
        if not isinstance(spec, Mapping) or "prio" not in spec:
            raise UnsupportedUpdate("delete_filter needs the prio of the classifiers to remove")
        steps.append(Step(f"tc filter del dev {name} parent 1: protocol ip prio {_number(spec['prio'], 'delete_filter.prio', 1, 65535)} u32", None))
    if "filter" in changes:
        steps.append(Step(_filter_command(name, changes["filter"], 1, "filter"), None))
    return steps


def _filter_command(name: str, flt: Any, default_prio: int, key: str) -> str:
    """One u32 classifier under the root, in the only form the Linux grammar accepts.

    `tc filter add`, never `replace`: a u32 filter given no handle is identified by
    nothing a replace could match, and the grammar refuses the replace outright.
    """
    if not isinstance(flt, Mapping) or "flowid" not in flt or not ({"src", "dst"} & set(flt)):
        raise UnsupportedUpdate(f"{key} entries need src or dst and flowid")
    if {"src", "dst"} <= set(flt):
        raise UnsupportedUpdate(f"{key} matches src or dst, not both")
    side = "src" if "src" in flt else "dst"
    prefix = _cidr(flt[side], f"{key}.{side}", host=False)
    prio = _number(flt.get("prio") or default_prio, f"{key}.prio", 1, 65535)
    return (f"tc filter add dev {name} parent 1: protocol ip prio {prio} u32 "
            f"match ip {side} {prefix} flowid {_token(flt['flowid'], f'{key}.flowid')}")


def _class_command(name: str, klass: Any, verb: str) -> str:
    if not isinstance(klass, Mapping) or "id" not in klass or "rate" not in klass:
        raise UnsupportedUpdate("a class needs id and rate (ceil, prio, parent optional)")
    rate = _rate(klass["rate"], "class.rate")
    ceil = _rate(klass.get("ceil") or klass["rate"], "class.ceil")
    parent = _token(klass.get("parent") or "1:1", "class.parent")
    command = f"tc class {verb} dev {name} parent {parent} classid {_token(klass['id'], 'class.id')} htb rate {rate} ceil {ceil}"
    if "prio" in klass:
        command += f" prio {_number(klass['prio'], 'class.prio', 0, 7)}"
    return command


_HANDLERS = {
    ("interface", "nokia_srlinux"): _interface_srl,
    ("interface", "linux"): _interface_linux,
    ("interface", "vyos"): _interface_vyos,
    ("route", "nokia_srlinux"): _route_srl,
    ("route", "linux"): _route_linux,
    ("route", "vyos"): _route_vyos,
    ("routing", "nokia_srlinux"): _routing_srl,
    ("routing", "vyos"): _routing_vyos,
    ("acl", "nokia_srlinux"): _acl_srl,
    ("dhcp_pool", "vyos"): _dhcp_pool_vyos,
    ("firewall_rule", "vyos"): _firewall_rule_vyos,
    ("zone", "vyos"): _zone_vyos,
    ("qos", "linux"): _qos_linux,
}
