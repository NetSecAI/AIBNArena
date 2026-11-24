"""The out-of-band management plane of a containerlab node, which no ANI write may touch.

containerlab wires every node to a management network of its own -- the
`mgmt.ipv4-subnet` of the topology file -- through one interface whose name the node
kind decides:

* **Linux** (`linux`): `eth0`, the container's first interface. The data ports start
  at `eth1`.
* **VyOS** (`vyos`, containerlab's `vyosnetworks_vyos`): `eth0` as well. VyOS leaves it
  out of its configuration tree altogether -- `show interfaces` lists it with the
  management address, `show configuration` does not -- and containerlab installs the
  kernel default route through it.
* **SR Linux** (`nokia_srlinux`): `mgmt0`, whose subinterface `mgmt0.0` lives in the
  `mgmt` network instance together with the default route to the management gateway
  and the system services bound to it (json-rpc, ssh, gnmi).
* **Arista EOS** (`arista_ceos`): `Management0`, in the default VRF. EOS accepts it
  under any unambiguous abbreviation and with a space before the number
  (`Management 0`, `ma0`), so every spelling is matched, not the name alone.

That plane is the lab's plumbing, not part of the network a scenario breaks: the
scenario topologies never declare those interfaces, so no fault lands there and no
repair needs them. In the graph, though, they read as ordinary interfaces on a subnet
every node shares, and an agent that reaches for them routes its endpoints out of the
lab through the Docker bridge instead of repairing them. A write that names one is
therefore refused before any device sees it.

The in-band management VLAN some topologies carry (VLAN 99, `admin1`, the firewalls'
`MGMT` zone) is a data-plane segment like any other and is not concerned: nothing here
matches on the word "mgmt" alone.
"""
from __future__ import annotations

import ipaddress
import re
import shlex
from pathlib import Path
from typing import Iterable

import yaml

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

#: The interface containerlab attaches to the management network, per node kind.
MANAGEMENT_INTERFACES: dict[str, str] = {
    "linux": "eth0",
    "vyos": "eth0",
    "nokia_srlinux": "mgmt0",
    "arista_ceos": "Management0",
}

#: The routing instance that holds the management interface, where the kind has one.
MANAGEMENT_NETWORK_INSTANCES: dict[str, str] = {
    "nokia_srlinux": "mgmt",
}

#: What containerlab addresses the lab from when the topology file names no subnet.
CONTAINERLAB_DEFAULT_IPV4_SUBNET = "172.20.20.0/24"

#: The rule as the agent reads it, in the description of every tool that writes a
#: device: known before the first write rather than learnt from a refusal. Generic on
#: purpose -- the descriptions are the same for every lab -- so it names the interfaces
#: and leaves the subnet to what the agent reads on them.
MANAGEMENT_PLANE_RULE = (
    "Writes into a node's out-of-band management plane are refused: the interface "
    "containerlab connects to the lab's management network (eth0 on Linux and VyOS "
    "nodes, mgmt0 and its subinterfaces on SR Linux, Management0 on Arista EOS), the SR Linux mgmt "
    "network-instance, and any address in that management network, its gateway "
    "included. That network is the lab's access path, not part of the network under "
    "repair: do not move a data address onto it or route through it; repair through "
    "the data-plane interfaces. A management VLAN or zone the topology itself defines "
    "is data plane and stays writable."
)

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: A token can carry several names: SR Linux also accepts the keyed path form
#: `/interface[name=mgmt0]/subinterface[index=0]`. Interface names are looked for in
#: every path segment; addresses keep their `/` because it separates the prefix length.
_NAME_SEPARATORS = re.compile(r"[\[\]=,/]")
_VALUE_SEPARATORS = re.compile(r"[\[\]=,]")

#: What iproute2 and the NOSes hang off a parent interface: a VLAN (`eth0.10`,
#: `mgmt0.0`), an address label (`eth0:1`), a peer index (`eth0@if12`).
_CHILD_MARKERS = frozenset({".", ":", "@"})

#: The value after these keywords is free text and may mention anything.
_FREE_TEXT_KEYWORDS = frozenset({"description"})

#: `Management0` as EOS reads it: any prefix of "management" from "ma" on, an
#: optional space, the number. "match 1" or "max-routes 2" do not match.
_EOS_MANAGEMENT = re.compile(r"(?i)\bma(?:n(?:a(?:g(?:e(?:m(?:e(?:n(?:t)?)?)?)?)?)?)?)?\s*\d+(?:/\d+)*\b")


def management_networks(topology_path: str | Path | None) -> tuple[IPNetwork, ...]:
    """The subnets containerlab addresses the lab's management interfaces from.

    Read from the `mgmt` block of the containerlab topology file, the one the lab was
    deployed from. A file that declares no IPv4 subnet gets containerlab's default, as
    containerlab itself does; with no file at all (an environment assembled by hand),
    the default is all there is to go on. A file that is named but cannot be read is
    an error, not a default: a guessed subnet would let the real one through.
    """
    if topology_path is None:
        return (ipaddress.ip_network(CONTAINERLAB_DEFAULT_IPV4_SUBNET),)
    path = Path(topology_path)
    if not path.is_absolute() and not path.exists():
        path = _REPO_ROOT / path
    with path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    if not isinstance(document, dict):
        raise ValueError(f"{path}: expected a containerlab topology mapping")
    mgmt = document.get("mgmt") or {}
    if not isinstance(mgmt, dict):
        raise ValueError(f"{path}: 'mgmt' must be a mapping")
    networks = [ipaddress.ip_network(
        str(mgmt.get("ipv4-subnet") or CONTAINERLAB_DEFAULT_IPV4_SUBNET), strict=False)]
    if mgmt.get("ipv6-subnet"):
        networks.append(ipaddress.ip_network(str(mgmt["ipv6-subnet"]), strict=False))
    return tuple(networks)


def management_plane_reference(
    kind: str,
    command: str,
    networks: Iterable[IPNetwork],
) -> str | None:
    """What in one native command belongs to the node's management plane, or None.

    Three things do, whatever the verb (`set`, `delete`, `ip ... add`, `ip ... del`):
    the management interface of the node's kind or anything hung off it, the
    management routing instance where the kind has one, and any address that lies
    in a management network -- a next hop through the management gateway reaches the
    outside world without naming the interface at all. A prefix that merely contains
    the management network (`0.0.0.0/0`, `172.16.0.0/12`) does not count: it loses to
    the management connected route and leaves the plane as it is.
    """
    tokens = _tokens(command)
    interface = MANAGEMENT_INTERFACES.get(kind)
    instance = MANAGEMENT_NETWORK_INSTANCES.get(kind)
    networks = tuple(networks)

    names = [piece for token in tokens for piece in _NAME_SEPARATORS.split(token) if piece]
    if kind == "arista_ceos":
        # An EOS description runs to the end of the line, unquoted.
        words = command.split()
        lowered = [word.lower() for word in words]
        if "description" in lowered:
            words = words[:lowered.index("description")]
        match = _EOS_MANAGEMENT.search(" ".join(words))
        if match:
            return f"it names {match.group(0)}, the management interface"
    elif interface is not None:
        for piece in names:
            if _names_interface(piece, interface):
                return f"it names {piece}, the management interface"
    if instance is not None:
        for index, piece in enumerate(names):
            if piece != "network-instance":
                continue
            following = names[index + 1:index + 3]
            if following[:1] == [instance] or following == ["name", instance]:
                return f"it names the {instance} network instance, which holds the management interface"

    for token in tokens:
        for piece in _VALUE_SEPARATORS.split(token):
            try:
                address = ipaddress.ip_interface(piece).ip
            except ValueError:
                continue
            for network in networks:
                if address.version == network.version and address in network:
                    return f"{piece} lies in the management network {network}"
    return None


def _tokens(command: str) -> list[str]:
    """The command's words, the free text of a description left out."""
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    tokens: list[str] = []
    skip_next = False
    for word in words:
        if skip_next:
            skip_next = False
            continue
        if word in _FREE_TEXT_KEYWORDS:
            skip_next = True
            continue
        tokens.append(word)
    return tokens


def _names_interface(piece: str, interface: str) -> bool:
    if piece == interface:
        return True
    return piece.startswith(interface) and piece[len(interface):len(interface) + 1] in _CHILD_MARKERS
