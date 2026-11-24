"""The VyOS documents the ANI collects are the ones the graph generator's parsers read."""
from benchmarks.platforms.containerlab.ani import ContainerLabANI


def test_vyos_documents_include_the_arp_table():
    # parser/arp/firewall_vyos reads arp_data.json (`show arp`): without it no host behind
    # the firewall gets a cable in the graph, since the firewall is the neighbour that
    # learnt its MAC address.
    assert ContainerLabANI._VYOS_DOCUMENTS["arp_data.json"] == "show arp"
    assert ContainerLabANI._VYOS_DOCUMENTS["routes_data.json"] == "show ip route"
    assert ContainerLabANI._VYOS_DOCUMENTS["version_data.json"] == "show version"
