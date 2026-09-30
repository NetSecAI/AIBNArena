"""Regression tests for the adapter's defence-in-depth command screen."""
from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

import pytest

from benchmarks.platforms.containerlab.safety import (  # noqa: E402
    check_command_safety,
    parse_linux_config_command,
)


def test_forbidden_commands_cannot_hide_inside_quoted_shell_payload() -> None:
    for command in (
        "sh -c 'rm -rf /tmp/x'",
        "sh -c 'docker ps'",
        'bash -c "systemctl stop frr"',
        "/bin/sh -lc '/usr/bin/rm -rf /tmp/x'",
    ):
        safe, reason = check_command_safety(command)
        assert safe is False, command
        assert "forbidden token" in (reason or ""), (command, reason)


def test_reviewed_shell_payload_and_native_command_still_pass() -> None:
    assert check_command_safety(
        "sh -c 'tc qdisc del dev eth2 root 2>/dev/null || true'")[0] is True
    assert check_command_safety(
        "ip route replace default via 10.0.0.1")[0] is True


def test_forbidden_executables_cannot_hide_behind_shell_punctuation_or_wrappers() -> None:
    for command in (
        "sh -c -- 'rm -rf /tmp/x'",
        "sh -c 'true;rm -rf /tmp/x'",
        "sh -c 'true&&rm -rf /tmp/x'",
        "sh -c 'true|docker ps'",
        "env /bin/rm -rf /tmp/x",
        "nice /bin/rm -rf /tmp/x",
    ):
        safe, reason = check_command_safety(command)
        assert safe is False, command
        assert "forbidden" in (reason or ""), (command, reason)


def test_linux_address_exact_compensation_options_are_narrowly_accepted() -> None:
    command = (
        "ip address replace 10.0.0.1/24 peer 10.0.0.2/32 "
        "broadcast 10.0.0.255 dev eth1 label eth1:svc scope global "
        "metric 10 valid_lft 300 preferred_lft 200 secondary noprefixroute"
    )
    assert parse_linux_config_command(command) == command.split()
    assert parse_linux_config_command(
        "ip address replace 2001:db8::1/64 dev eth1 scope global "
        "valid_lft forever preferred_lft forever")


@pytest.mark.parametrize("command", [
    "ip address replace 10.0.0.1/24 dev eth1 description injected",
    "ip address replace 10.0.0.1/24 dev eth1 dev eth2",
    "ip address replace 10.0.0.1/24 scope universe dev eth1",
    "ip address delete 10.0.0.1/24 dev eth1 label eth1:svc",
    "ip address replace 10.0.0.1/24 peer not-an-ip dev eth1",
])
def test_linux_address_compensation_refuses_unmodelled_or_ambiguous_tail(
        command: str) -> None:
    with pytest.raises(ValueError, match="unsupported Linux configuration grammar"):
        parse_linux_config_command(command)


@pytest.mark.parametrize("command", [
    "ip route del 10.0.0.0/24",
    # A rollback names the route it removes: the next hop and the device the subject
    # gave it, as `ip route del` accepts them.
    "ip route del default via 10.0.0.1 dev eth0",
    "ip route del 10.0.0.0/24 dev eth1",
    "ip route del default via 10.0.0.1 metric 100 proto static",
    "ip route replace blackhole 10.0.0.0/24 proto static metric 7 table main",
    "ip route replace unreachable default proto boot scope global metric 0",
    (
        "ip route replace unicast 10.0.0.0/24 tos 0x10 table 100 proto static "
        "scope global metric 50 src 192.0.2.10 mtu 1500 rtt 20ms quickack 1 "
        "congctl cubic pref high via inet 192.0.2.1 dev eth1 weight 2 onlink"
    ),
    (
        "ip route replace 10.0.0.0/24 proto static metric 70 scope global "
        "src 192.0.2.10 nexthop via inet 192.0.2.1 dev eth1 weight 1 onlink "
        "nexthop via 192.0.2.2 dev eth2 weight 256 pervasive realm blue"
    ),
])
def test_linux_route_exact_replay_forms_are_authorized(command: str) -> None:
    assert parse_linux_config_command(command)


@pytest.mark.parametrize("command", [
    "ip route replace 10.0.0.0/24 via 192.0.2.1 linkdown",
    "ip route replace 10.0.0.0/24 cache via 192.0.2.1",
    "ip route replace 10.0.0.0/24 error -101 via 192.0.2.1",
    "ip route replace 10.0.0.0/24 proto dhcp via 192.0.2.1",
    "ip route replace 10.0.0.0/24 metric 4294967296 via 192.0.2.1",
    "ip route replace 10.0.0.0/24 table 0 via 192.0.2.1",
    "ip route replace 10.0.0.0/24 scope universe via 192.0.2.1",
    "ip route replace 10.0.0.0/24 dev eth1 via 192.0.2.1",
    "ip route replace 10.0.0.0/24 onlink via 192.0.2.1",
    "ip route replace 10.0.0.0/24 via inet6 192.0.2.1",
    "ip route replace 10.0.0.0/24 via 192.0.2.1 weight 0",
    "ip route replace 10.0.0.0/24 via 192.0.2.1 weight 257",
    "ip route replace 10.0.0.0/24 metric 1 metric 2 via 192.0.2.1",
    "ip route replace 10.0.0.0/24 nexthop dev eth1",
    "ip route replace 10.0.0.0/24 nexthop via 192.0.2.1 metric 1",
    "ip route replace 2001:db8::/64 via 192.0.2.1",
    "ip route replace 10.0.0.0/24 via 192.0.2.1 dev 'eth1;rm'",
    "ip route replace 10.0.0.0/24 congctl 'cubic$(id)' via 192.0.2.1",
])
def test_linux_route_refuses_output_only_malformed_or_shell_tokens(
        command: str) -> None:
    with pytest.raises(ValueError, match="unsupported Linux configuration grammar"):
        parse_linux_config_command(command)


@pytest.mark.parametrize("command", [
    "ip route del 10.0.0.0/24 10.0.1.0/24",
    "ip route del default via 10.0.0.1 via 10.0.0.2",
    "ip route del default dev",
    "ip route del blackhole 10.0.0.0/24",
    "ip route del default onlink",
])
def test_route_delete_rejects_what_is_not_a_route_selector(command):
    with pytest.raises(ValueError, match="unsupported Linux configuration grammar"):
        parse_linux_config_command(command)
