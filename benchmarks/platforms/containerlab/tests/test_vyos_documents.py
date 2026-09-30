"""The VyOS documents the ANI collects beside the configuration tree."""
from benchmarks.platforms.containerlab.ani import ContainerLabANI


def test_vyos_documents_include_the_arp_table():
    # `show arp` is the only place a host behind the firewall is seen from the network:
    # the firewall is its gateway, so only the firewall's ARP table learns its MAC.
    assert ContainerLabANI._VYOS_DOCUMENTS["arp_data.json"] == "show arp"
    assert ContainerLabANI._VYOS_DOCUMENTS["routes_data.json"] == "show ip route"
    assert ContainerLabANI._VYOS_DOCUMENTS["version_data.json"] == "show version"
