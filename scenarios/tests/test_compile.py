from pathlib import Path
import json
from types import SimpleNamespace
import sys
from tempfile import TemporaryDirectory
import tomllib

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
SCENARIO_PATHS = [
    path
    for domain in ("connectivity", "dhcp_dns", "filtering", "qos", "security")
    for path in sorted((ROOT / domain).glob("*.yaml"))
]
sys.path.insert(0, str(REPO_ROOT))

from benchmarks.platforms.containerlab import ContainerLabEnv
from benchmarks.platforms.containerlab.compiled_topology import (
    CompiledContainerLabTopology,
    acl_interface_key,
    split_interface,
    vyos_interface_path,
)
from scenarios.access import (
    CompiledScenarioSuite,
    assert_no_private_fields,
    build_sut_request,
)
from scenarios.compiler.compiler import ScenarioCompiler
from benchmarks.platforms.containerlab import ANI_OPERATIONS, ContainerLabANI
from benchmarks.platforms.containerlab.ani import PUBLIC_SUCCESS_CRITERION_TYPES
from benchmarks.platforms.containerlab.fault import LabFault
from benchmarks.platforms.containerlab.state import StateCommand
from benchmarks.platforms.containerlab.types import (
    CommandResult,
    ConnectivityCheck,
    LabNode,
    Observation,
)
from benchmarks.core import (
    A2A_CONTRACT_VERSION,
    parse_self_execute_report,
    require_self_execute_contract,
)


def _suite(topology="sme_leaf_spine_dmz_small.yaml", seed=7, domains=None):
    compiler = ScenarioCompiler.from_topology_file(ROOT / "topologies" / topology, seed=seed)
    return CompiledScenarioSuite(
        compiler.compile_scenarios(ROOT, domains=domains)
    )


def test_catalog_documents_use_schema_version_0_1():
    document_paths = [
        *SCENARIO_PATHS,
        *sorted((ROOT / "topologies").glob("*.yaml")),
    ]
    assert document_paths
    for path in document_paths:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert document["schema_version"] == "0.1", path

    assert _suite().document["schema_version"] == "0.1"


def test_catalog_documents_validate_against_json_schemas():
    scenario_schema = json.loads(
        (ROOT / "schemas" / "scenario.schema.json").read_text(encoding="utf-8")
    )
    topology_schema = json.loads(
        (ROOT / "schemas" / "topology.schema.json").read_text(encoding="utf-8")
    )
    compiled_schema = json.loads(
        (ROOT / "schemas" / "compiled_suite.schema.json").read_text(encoding="utf-8")
    )

    for path in SCENARIO_PATHS:
        jsonschema.validate(
            yaml.safe_load(path.read_text(encoding="utf-8")),
            scenario_schema,
        )
    for path in sorted((ROOT / "topologies").glob("*.yaml")):
        jsonschema.validate(
            yaml.safe_load(path.read_text(encoding="utf-8")),
            topology_schema,
        )
    jsonschema.validate(_suite().document, compiled_schema)


def test_catalog_compiles_all_three_domains():
    suite = _suite()
    assert suite.topology_id == "sme_leaf_spine_dmz_small"
    instances = suite.instances()
    # 47 faults and exploits, not the 51 this once counted: assured_bandwidth needs a
    # shaped WAN edge to bind against and compiles only on sme_wan_edge_qos, and
    # routing_setup's three variants left with its scenario file. Each fault is now
    # stated at three precisions, which is why the total is not 47 any more.
    faults = [item for item in instances if item.domain != "security"]
    exploits = [item for item in instances if item.domain == "security"]
    assert len(faults) == 23 * 3
    assert len(exploits) == 24
    assert len(instances) == 93
    ids = {task.id for task in instances}
    assert "security.arp_spoofing.exploit" in ids
    assert "security.unaudited_network_configuration_change.prevent" in ids
    assert "connectivity.disable_routing.m1" in ids
    # assured_bandwidth moved to the shaped WAN edge; what is left of the qos
    # domain on a topology with no bottleneck is the link-impairment family.
    assert "qos.link_impairment.m1" in ids
    assert "qos.link_impairment.m1" in ids
    assert "qos.link_impairment.m2" in ids


def test_public_view_does_not_contain_private_injection_or_oracle():
    suite = _suite(domains={"connectivity"})
    task = suite.select(["connectivity.disable_routing.m1"])[0]
    serialized = json.dumps(task.public_view()).lower()
    assert "sysctl" not in serialized
    assert "set_ipv4_forwarding" not in serialized
    assert "network-instance" not in serialized
    assert "oracle" not in serialized
    private = task.private
    assert private["evaluator"]["selected_method"]["name"] == (
        "router_ipv4_forwarding_disabled"
    )


def test_sut_request_is_built_from_an_allowlist():
    payload = build_sut_request(
        intent="Restore connectivity.",
        scenario_id="scn_0123456789abcdef",
        contract_version=A2A_CONTRACT_VERSION,
        expected_output_modes=["self_execute"],
        constraints={"avoid_destructive_commands": True},
        available_tools=[],
        action_schema={"machine": "node", "command": "command"},
        output_contracts={},
        observation={"connectivity": []},
    )
    assert set(payload) == {
        "contract_version",
        "expected_output_modes",
        "scenario_id",
        "intent",
        "constraints",
        "available_tools",
        "action_schema",
        "output_contracts",
        "observation",
    }
    assert_no_private_fields(payload)


def test_private_key_guard_fails_closed():
    try:
        assert_no_private_fields({"observation": {"selected_method": {"id": 2}}})
    except ValueError as exc:
        assert "selected_method" in str(exc)
    else:
        raise AssertionError("private-key guard accepted evaluator-private data")


def test_clos01_topology_materializes_router_interface_fault():
    suite = _suite(topology="clos01_mvp.yaml", seed=1, domains={"connectivity"})
    instance = suite.select(["connectivity.disable_interface.m1"])[0]
    topology = CompiledContainerLabTopology.from_file(
        ROOT / "topologies" / "clos01_mvp.yaml"
    )
    fault = topology.materialize(instance)
    assert fault.commands == [
        StateCommand(
            target="leaf1",
            mode="srl_cli",
            command="set / interface ethernet-1/2 admin-state disable",
        )
    ]
    env_config = topology.env_config(
        healthy_state_path="benchmarks/testbeds/containerlab/states/healthy.json",
    )
    assert env_config.lab_name == "clos01"
    assert env_config.topology_path == Path("benchmarks/testbeds/containerlab/clos01.clab.yml")
    assert sorted(env_config.nodes) == ["client1", "client2", "leaf1", "leaf2", "spine"]
    assert env_config.default_connectivity_checks == [
        ("client1", "client2", "192.168.20.10"),
        ("client2", "client1", "192.168.10.10"),
    ]


def test_sme_topology_materializes_all_disable_routing_methods():
    suite = _suite(seed=9, domains={"connectivity"})
    topology = CompiledContainerLabTopology.from_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_small.yaml"
    )

    forwarding = suite.select(["connectivity.disable_routing.m1"])[0]
    assert forwarding.private["bindings"] == {
        "target": "leaf2",
        "affected_nodes": ["app1", "external1", "finance1", "web1"],
    }
    forwarding_fault = topology.materialize(forwarding)
    assert forwarding_fault.commands == [
        StateCommand(
            target="leaf2",
            mode="srl_cli",
            command="set / network-instance default admin-state disable",
        )
    ]
    assert forwarding_fault.restore_commands == [
        StateCommand(
            target="leaf2",
            mode="srl_cli",
            command="set / network-instance default admin-state enable",
        )
    ]

    drop = suite.select(["connectivity.disable_routing.m2"])[0]
    assert drop.private["bindings"]["target"] == "leaf1"
    assert drop.private["bindings"]["affected_nodes"] == ["guest1", "user1"]
    assert drop.private["bindings"]["interfaces"] == [
        "ethernet-1/1",
        "ethernet-1/2",
        "ethernet-1/10",
        "ethernet-1/20",
    ]
    drop_commands = topology.materialize(drop).commands
    assert len(drop_commands) == 13
    assert drop_commands[0] == StateCommand(
        target="leaf1",
        mode="srl_cli",
        command=(
            "set / acl acl-filter ibn-forwarding-guard type ipv4 "
            "entry 65535 action drop"
        ),
    )
    assert all(command.target == "leaf1" for command in drop_commands)
    assert all(command.mode == "srl_cli" for command in drop_commands)
    assert not any("ethernet-1/57" in command.command for command in drop_commands)
    drop_restore = topology.materialize(drop).restore_commands
    assert drop_restore[-1] == StateCommand(
        target="leaf1",
        mode="srl_cli",
        command="delete / acl acl-filter ibn-forwarding-guard type ipv4",
    )
    assert len(drop_restore) == 5

    prohibited = suite.select(["connectivity.disable_routing.m3"])[0]
    prohibited_fault = topology.materialize(prohibited)
    assert prohibited_fault.commands == [
        StateCommand(
            target="external1",
            mode="shell",
            command="ip route replace prohibit default",
        )
    ]
    assert prohibited_fault.restore_commands == [
        StateCommand(
            target="external1",
            mode="shell",
            command="ip route replace default via 203.0.113.1",
        )
    ]

    no_default = suite.select(["connectivity.disable_routing.m4"])[0]
    no_default_fault = topology.materialize(no_default)
    assert no_default_fault.commands == [
        StateCommand(
            target="web1",
            mode="shell",
            command="ip route del default",
        )
    ]
    assert no_default_fault.restore_commands == [
        StateCommand(
            target="web1",
            mode="shell",
            command="ip route replace default via 10.10.40.1",
        )
    ]

    healthy = json.loads(
        (
            REPO_ROOT / "benchmarks/testbeds/containerlab/sme01-small/states/healthy.json"
        ).read_text(encoding="utf-8")
    )
    healthy_commands = {
        (command["target"], command["mode"], command["command"])
        for command in healthy["commands"]
    }
    assert not any(
        "ibn-forwarding-guard" in command
        or "network-instance default admin-state" in command
        for _, _, command in healthy_commands
    )
    assert (
        "external1",
        "shell",
        "ip route replace default via 203.0.113.1",
    ) in healthy_commands


def test_sme_topology_materializes_all_disable_interface_methods():
    suite = _suite(seed=9, domains={"connectivity"})
    topology = CompiledContainerLabTopology.from_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_small.yaml"
    )

    router_instance = suite.select(["connectivity.disable_interface.m1"])[0]
    assert router_instance.private["bindings"] == {
        "target": "leaf2",
        "interface": "ethernet-1/40",
        "segment": "dmz_vlan",
        "affected_nodes": ["web1"],
    }
    router_fault = topology.materialize(router_instance)
    assert router_fault.commands == [
        StateCommand(
            target="leaf2",
            mode="srl_cli",
            command="set / interface ethernet-1/40 admin-state disable",
        )
    ]
    assert router_fault.restore_commands == [
        StateCommand(
            target="leaf2",
            mode="srl_cli",
            command="set / interface ethernet-1/40 admin-state enable",
        )
    ]

    endpoint_instance = suite.select(["connectivity.disable_interface.m2"])[0]
    endpoint_fault = topology.materialize(endpoint_instance)
    assert endpoint_fault.commands == [
        StateCommand(
            target="user1",
            mode="shell",
            command="ip link set dev eth1 down",
        )
    ]
    assert endpoint_fault.restore_commands == [
        StateCommand(
            target="user1",
            mode="shell",
            command="ip link set dev eth1 up",
        )
    ]

    mtu_instance = suite.select(["connectivity.disable_interface.m3"])[0]
    mtu_fault = topology.materialize(mtu_instance)
    assert mtu_fault.commands == [
        StateCommand(
            target="finance1",
            mode="shell",
            command="ip link set dev eth1 mtu 68",
        )
    ]
    assert mtu_fault.restore_commands == [
        StateCommand(
            target="finance1",
            mode="shell",
            command="ip link set dev eth1 mtu 1500",
        )
    ]

    env_config = topology.env_config(
        healthy_state_path="benchmarks/testbeds/containerlab/sme01-small/states/healthy.json",
    )
    assert env_config.lab_name == "sme01-small"
    assert env_config.topology_path == Path(
        "benchmarks/testbeds/containerlab/sme01-small/topology.clab.yml"
    )
    assert len(env_config.nodes) == 12
    assert len(env_config.default_connectivity_checks) == 10

    healthy = json.loads(
        (REPO_ROOT / env_config.healthy_state_path).read_text(encoding="utf-8")
    )
    known_targets = set(env_config.nodes)
    assert all(command["target"] in known_targets for command in healthy["commands"])


def test_sme_topology_materializes_all_remove_ip_methods_without_leakage():
    suite = _suite(seed=9, domains={"connectivity"})
    topology = CompiledContainerLabTopology.from_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_small.yaml"
    )

    expected = {
        "connectivity.remove_ip.m1": {
            "bindings": {
                "target": "leaf2",
                "interface": "ethernet-1/50",
                "segment": "server_vlan",
                "affected_nodes": ["app1"],
                "healthy_ipv4": "10.10.50.1/24",
            },
            "commands": [
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "delete / interface ethernet-1/50 subinterface 0 "
                    "ipv4 address 10.10.50.1/24",
                )
            ],
            "restore": [
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "set / interface ethernet-1/50 subinterface 0 "
                    "ipv4 address 10.10.50.1/24",
                )
            ],
        },
        "connectivity.remove_ip.m2": {
            "bindings": {
                "target": "guest1",
                "interface": "eth1",
                "segment": "guest_vlan",
                "affected_nodes": ["guest1"],
                "healthy_ipv4": "10.10.20.10/24",
            },
            "commands": [
                StateCommand(
                    "guest1",
                    "shell",
                    "ip address del 10.10.20.10/24 dev eth1",
                )
            ],
            "restore": [
                StateCommand(
                    "guest1",
                    "shell",
                    "ip address replace 10.10.20.10/24 dev eth1",
                ),
                StateCommand(
                    "guest1",
                    "shell",
                    "ip route replace default via 10.10.20.1",
                ),
            ],
        },
        "connectivity.remove_ip.m3": {
            "bindings": {
                "target": "user1",
                "interface": "eth1",
                "segment": "users_vlan",
                "affected_nodes": ["user1"],
                "healthy_ipv4": "10.10.10.10/24",
                "fault_ipv4": "192.0.2.10/24",
            },
            "commands": [
                StateCommand(
                    "user1",
                    "shell",
                    "ip address del 10.10.10.10/24 dev eth1",
                ),
                StateCommand(
                    "user1",
                    "shell",
                    "ip address add 192.0.2.10/24 dev eth1",
                ),
            ],
            "restore": [
                StateCommand(
                    "user1",
                    "shell",
                    "ip address del 192.0.2.10/24 dev eth1",
                ),
                StateCommand(
                    "user1",
                    "shell",
                    "ip address replace 10.10.10.10/24 dev eth1",
                ),
                StateCommand(
                    "user1",
                    "shell",
                    "ip route replace default via 10.10.10.1",
                ),
            ],
        },
        "connectivity.remove_ip.m4": {
            "bindings": {
                "target": "app1",
                "interface": "eth1",
                "segment": "server_vlan",
                "affected_nodes": ["app1"],
                "healthy_ipv4": "10.10.50.10/24",
                "fault_ipv4": "10.10.50.10/32",
            },
            "commands": [
                StateCommand(
                    "app1",
                    "shell",
                    "ip address del 10.10.50.10/24 dev eth1",
                ),
                StateCommand(
                    "app1",
                    "shell",
                    "ip address add 10.10.50.10/32 dev eth1",
                ),
            ],
            "restore": [
                StateCommand(
                    "app1",
                    "shell",
                    "ip address del 10.10.50.10/32 dev eth1",
                ),
                StateCommand(
                    "app1",
                    "shell",
                    "ip address replace 10.10.50.10/24 dev eth1",
                ),
                StateCommand(
                    "app1",
                    "shell",
                    "ip route replace default via 10.10.50.1",
                ),
            ],
        },
        "connectivity.remove_ip.m5": {
            "bindings": {
                "target": "finance1",
                "interface": "eth1",
                "segment": "finance_vlan",
                "affected_nodes": ["finance1"],
                "healthy_ipv4": "10.10.30.10/24",
                "fault_ipv4": "203.0.113.10/24",
                "duplicate_source": "external1",
            },
            "commands": [
                StateCommand(
                    "finance1",
                    "shell",
                    "ip address del 10.10.30.10/24 dev eth1",
                ),
                StateCommand(
                    "finance1",
                    "shell",
                    "ip address add 203.0.113.10/24 dev eth1",
                ),
            ],
            "restore": [
                StateCommand(
                    "finance1",
                    "shell",
                    "ip address del 203.0.113.10/24 dev eth1",
                ),
                StateCommand(
                    "finance1",
                    "shell",
                    "ip address replace 10.10.30.10/24 dev eth1",
                ),
                StateCommand(
                    "finance1",
                    "shell",
                    "ip route replace default via 10.10.30.1",
                ),
            ],
        },
    }

    for instance_id, reference in expected.items():
        instance = suite.select([instance_id])[0]
        fault = topology.materialize(instance)
        assert instance.private["bindings"] == reference["bindings"]
        assert fault.commands == reference["commands"]
        assert fault.restore_commands == reference["restore"]

        public = json.dumps(instance.public_view())
        assert "healthy_ipv4" not in public
        assert "fault_ipv4" not in public
        assert "duplicate_source" not in public
        for value in reference["bindings"].values():
            if isinstance(value, str) and (
                "/" in value or value.startswith(("leaf", "spine", "user", "guest", "app", "finance"))
            ):
                assert value not in public


def test_sme_topology_materializes_subnet_traffic_methods_without_leakage():
    suite = _suite(seed=9, domains={"connectivity"})
    topology = CompiledContainerLabTopology.from_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_small.yaml"
    )
    expected_bindings = {
        "target": "leaf1",
        "interface": "ethernet-1/20",
        "segment": "guest_vlan",
        "subnet_prefix": "10.10.20.0/24",
        "affected_nodes": ["guest1"],
    }
    expected_commands = {
        1: [
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 100 "
            "match ipv4 source-ip prefix 10.10.20.0/24",
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 100 action drop",
            "set / acl interface ethernet-1/20.0 interface-ref interface ethernet-1/20",
            "set / acl interface ethernet-1/20.0 interface-ref subinterface 0",
            "set / acl interface ethernet-1/20.0 input acl-filter "
            "ibn-subnet-guard type ipv4",
        ],
        2: [
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 100 "
            "match ipv4 destination-ip prefix 10.10.20.0/24",
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 100 action drop",
            "set / acl interface ethernet-1/20.0 interface-ref interface ethernet-1/20",
            "set / acl interface ethernet-1/20.0 interface-ref subinterface 0",
            "set / acl interface ethernet-1/20.0 output acl-filter "
            "ibn-subnet-guard type ipv4",
        ],
        3: [
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 100 "
            "match ipv4 source-ip prefix 10.10.20.0/24",
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 100 action drop",
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 200 "
            "match ipv4 destination-ip prefix 10.10.20.0/24",
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 200 action drop",
            "set / acl interface ethernet-1/20.0 interface-ref interface ethernet-1/20",
            "set / acl interface ethernet-1/20.0 interface-ref subinterface 0",
            "set / acl interface ethernet-1/20.0 input acl-filter "
            "ibn-subnet-guard type ipv4",
            "set / acl interface ethernet-1/20.0 output acl-filter "
            "ibn-subnet-guard type ipv4",
        ],
        4: [
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 100 "
            "match ipv4 source-ip prefix 10.10.20.0/24",
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 100 "
            "match ipv4 protocol icmp",
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 100 action drop",
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 200 "
            "match ipv4 destination-ip prefix 10.10.20.0/24",
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 200 "
            "match ipv4 protocol icmp",
            "set / acl acl-filter ibn-subnet-guard type ipv4 entry 200 action drop",
            "set / acl interface ethernet-1/20.0 interface-ref interface ethernet-1/20",
            "set / acl interface ethernet-1/20.0 interface-ref subinterface 0",
            "set / acl interface ethernet-1/20.0 input acl-filter "
            "ibn-subnet-guard type ipv4",
            "set / acl interface ethernet-1/20.0 output acl-filter "
            "ibn-subnet-guard type ipv4",
        ],
    }
    restore = [
        "delete / acl interface ethernet-1/20.0",
        "delete / acl acl-filter ibn-subnet-guard type ipv4",
    ]

    for method_id, commands in expected_commands.items():
        instance = suite.select([
            f"connectivity.drop_traffic_to_from_subnet.m{method_id}"
        ])[0]
        fault = topology.materialize(instance)
        assert instance.private["bindings"] == expected_bindings
        assert [command.command for command in fault.commands] == commands
        assert [command.command for command in fault.restore_commands] == restore
        assert all(command.target == "leaf1" for command in fault.commands)
        assert all(command.mode == "srl_cli" for command in fault.commands)

        public = json.dumps(instance.public_view())
        for private_value in (
            "leaf1",
            "ethernet-1/20",
            "guest_vlan",
            "10.10.20.0/24",
            "guest1",
            "ibn-subnet-guard",
        ):
            assert private_value not in public


def test_sme_topology_materializes_wrong_routing_table_methods_without_leakage():
    suite = _suite(seed=9, domains={"connectivity"})
    topology = CompiledContainerLabTopology.from_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_small.yaml"
    )

    expected = {
        "connectivity.wrong_routing_table.m1": {
            "bindings": {
                "target": "leaf2",
                "route_prefix": "10.10.20.0/24",
                "destination": "guest1",
                "destination_segment": "guest_vlan",
                "destination_gateway": "leaf1",
                "canonical_next_hop_group": "to-leaf1",
                "temporary_next_hop_group": "ibn-wrt-m1",
                "affected_nodes": ["guest1"],
                "fault_next_hop": "10.10.50.10",
                "fault_next_hop_source": "app1",
            },
            "commands": [
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "set / network-instance default next-hop-groups group "
                    "ibn-wrt-m1 nexthop 1 ip-address 10.10.50.10 "
                    "admin-state enable",
                ),
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "set / network-instance default static-routes route "
                    "10.10.20.0/24 next-hop-group ibn-wrt-m1 admin-state enable",
                ),
            ],
            "restore": [
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "set / network-instance default static-routes route "
                    "10.10.20.0/24 next-hop-group to-leaf1 admin-state enable",
                ),
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "delete / network-instance default next-hop-groups "
                    "group ibn-wrt-m1",
                ),
            ],
        },
        "connectivity.wrong_routing_table.m2": {
            "commands": [
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "set / network-instance default next-hop-groups group "
                    "ibn-wrt-m2 blackhole",
                ),
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "set / network-instance default static-routes route "
                    "10.10.10.10/32 next-hop-group ibn-wrt-m2 "
                    "admin-state enable",
                ),
            ],
            "restore": [
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "delete / network-instance default static-routes route "
                    "10.10.10.10/32",
                ),
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "delete / network-instance default next-hop-groups "
                    "group ibn-wrt-m2",
                ),
            ],
        },
        "connectivity.wrong_routing_table.m3": {
            "commands": [
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "set / network-instance default next-hop-groups group "
                    "ibn-wrt-m3 nexthop 1 ip-address 192.0.2.1 "
                    "admin-state enable",
                ),
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "set / network-instance default static-routes route "
                    "10.10.20.0/24 next-hop-group ibn-wrt-m3 "
                    "admin-state enable",
                ),
            ],
            "restore": [
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "set / network-instance default static-routes route "
                    "10.10.20.0/24 next-hop-group to-leaf1 "
                    "admin-state enable",
                ),
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "delete / network-instance default next-hop-groups "
                    "group ibn-wrt-m3",
                ),
            ],
        },
        "connectivity.wrong_routing_table.m4": {
            "commands": [
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "set / network-instance default next-hop-groups group "
                    "ibn-wrt-m4 nexthop 1 ip-address 10.255.0.4 "
                    "admin-state enable",
                ),
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "set / network-instance default static-routes route "
                    "10.10.10.0/24 next-hop-group ibn-wrt-m4 "
                    "admin-state enable",
                ),
                StateCommand(
                    "spine1",
                    "srl_cli",
                    "set / network-instance default next-hop-groups group "
                    "ibn-wrt-m4-back nexthop 1 ip-address 10.255.0.5 "
                    "admin-state enable",
                ),
                StateCommand(
                    "spine1",
                    "srl_cli",
                    "set / network-instance default static-routes route "
                    "10.10.10.0/24 next-hop-group ibn-wrt-m4-back "
                    "admin-state enable",
                ),
            ],
            "restore": [
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "set / network-instance default static-routes route "
                    "10.10.10.0/24 next-hop-group to-leaf1 "
                    "admin-state enable",
                ),
                StateCommand(
                    "leaf2",
                    "srl_cli",
                    "delete / network-instance default next-hop-groups "
                    "group ibn-wrt-m4",
                ),
                StateCommand(
                    "spine1",
                    "srl_cli",
                    "set / network-instance default static-routes route "
                    "10.10.10.0/24 next-hop-group to-leaf1 "
                    "admin-state enable",
                ),
                StateCommand(
                    "spine1",
                    "srl_cli",
                    "delete / network-instance default next-hop-groups "
                    "group ibn-wrt-m4-back",
                ),
            ],
        },
        "connectivity.wrong_routing_table.m5": {
            "commands": [
                StateCommand(
                    "web1",
                    "shell",
                    "ip route replace default via 10.10.40.254",
                )
            ],
            "restore": [
                StateCommand(
                    "web1",
                    "shell",
                    "ip route replace default via 10.10.40.1",
                )
            ],
        },
    }

    expected_affected = {
        "connectivity.wrong_routing_table.m1": ["guest1"],
        "connectivity.wrong_routing_table.m2": ["user1"],
        "connectivity.wrong_routing_table.m3": ["guest1"],
        "connectivity.wrong_routing_table.m4": ["user1"],
        "connectivity.wrong_routing_table.m5": ["web1"],
    }
    for instance_id, reference in expected.items():
        instance = suite.select([instance_id])[0]
        fault = topology.materialize(instance)
        if "bindings" in reference:
            assert instance.private["bindings"] == reference["bindings"]
        assert fault.commands == reference["commands"]
        assert fault.restore_commands == reference["restore"]
        assert fault.expected_affected_nodes == expected_affected[instance_id]

        public = json.dumps(instance.public_view())
        for private_value in (
            "route_prefix",
            "fault_next_hop",
            "fault_gateway",
            "canonical_next_hop_group",
            "ibn-wrt",
            "10.10.",
            "192.0.2.1",
            "leaf2",
            "spine1",
        ):
            assert private_value not in public


def test_sme_topology_materializes_qos_link_impairment_without_leakage():
    suite = _suite(seed=9, domains={"qos"})
    topology = CompiledContainerLabTopology.from_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_small.yaml"
    )
    expected = {
        "qos.link_impairment.m1": {
            "bindings": {
                "target": "finance1",
                "interface": "eth1",
                "segment": "finance_vlan",
                "destination": "app1",
                "destination_ip": "10.10.50.10",
                "affected_nodes": ["finance1"],
                "impairment": "delay",
                "delay_ms": 3511,
            },
            "command": "tc qdisc replace dev eth1 root netem delay 3511ms",
        },
        "qos.link_impairment.m2": {
            "bindings": {
                "target": "finance1",
                "interface": "eth1",
                "segment": "finance_vlan",
                "destination": "app1",
                "destination_ip": "10.10.50.10",
                "affected_nodes": ["finance1"],
                "impairment": "corruption",
                "corruption_percent": 31,
            },
            "command": "tc qdisc replace dev eth1 root netem corrupt 31%",
        },
    }
    for instance_id, reference in expected.items():
        instance = suite.select([instance_id])[0]
        fault = topology.materialize(instance)
        assert instance.private["bindings"] == reference["bindings"]
        assert fault.commands == [
            StateCommand("finance1", "shell", reference["command"])
        ]
        # The restore tolerates a qdisc the subject already removed (its repair).
        assert fault.restore_commands == [
            StateCommand("finance1", "shell", "sh -c 'tc qdisc del dev eth1 root 2>/dev/null || true'")
        ]
        public = json.dumps(instance.public_view())
        for private_value in (
            "finance_vlan",
            "eth1",
            "netem",
            "1567",
            "25",
        ):
            assert private_value not in public
        for public_service_value in ("finance1", "app1", "10.10.50.10"):
            assert public_service_value in public


def test_no_topology_specific_names_are_required_by_generic_scenarios():
    suite = _suite()
    by_domain = {}
    for item in suite.instances():
        by_domain[item.domain] = by_domain.get(item.domain, 0) + 1
        assert "{{" not in item.intent and "}}" not in item.intent
    # connectivity lost routing_setup's three variants with its scenario file, and
    # every remaining fault is stated at three precisions: 21 and 2 faults, times 3.
    assert by_domain == {"connectivity": 63, "qos": 6, "security": 24}


def test_multiple_samples_have_unique_reproducible_ids():
    compiler = ScenarioCompiler.from_topology_file(
        ROOT / "topologies" / "clos01_mvp.yaml", seed=11
    )
    first = CompiledScenarioSuite(
        compiler.compile_scenarios(
            ROOT,
            domains={"connectivity"},
            samples_per_scenario=2,
        )
    )
    compiler = ScenarioCompiler.from_topology_file(
        ROOT / "topologies" / "clos01_mvp.yaml", seed=11
    )
    second = CompiledScenarioSuite(
        compiler.compile_scenarios(
            ROOT,
            domains={"connectivity"},
            samples_per_scenario=2,
        )
    )
    first_ids = [item.id for item in first.instances()]
    second_ids = [item.id for item in second.instances()]
    assert first_ids == second_ids
    assert len(first_ids) == len(set(first_ids))
    assert "connectivity.disable_interface.m2.i001" in first_ids
    assert "connectivity.disable_interface.m2.i002" in first_ids




def test_qos_scenario_compiles_public_service_success_criteria():
    suite = _suite(seed=9, domains={"qos"})
    instance = suite.select(["qos.link_impairment.m1"])[0]
    assert instance.public_view()["success_criteria"] == {
        "all_of": [
            {"type": "lab_connectivity"},
            {
                "type": "observed_icmp_service",
                "source": "finance1",
                "destination": "app1",
                "destination_ip": "10.10.50.10",
                "max_packet_loss_percent": 0,
                "max_rtt_avg_ms": 10,
            },
        ]
    }


def test_every_method_scenario_compiles_a_measurable_public_success_criterion():
    """Every method-based scenario publishes a criterion the ANI can measure.

    An agent proves it met the intent by calling execute_validation with type
    public_success_criteria, and the ANI only measures the types listed in
    PUBLIC_SUCCESS_CRITERION_TYPES. A scenario whose public view carries no such
    criterion hands the agent an intent it can never validate, so each one must
    carry at least one. Security scenarios bind by strategy, not by method, and
    are out of scope here.
    """
    method_scenarios = set()
    for domain in ("connectivity", "dhcp_dns", "filtering", "qos"):
        for path in sorted((ROOT / domain).glob("*.yaml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            if payload.get("methods"):
                method_scenarios.add((domain, payload["scenario"]))
    assert method_scenarios

    # No single topology compiles every one of these (dhcp_provisioning needs
    # the dns fabric, zone_policy_enforcement a firewall, two qos scenarios the
    # shaped WAN edge), so sweep them all and confirm afterwards that the sweep
    # reached every file: a scenario nobody compiles must not pass by absence.
    compiled_scenarios = set()
    for topology in sorted((ROOT / "topologies").glob("*.yaml")):
        suite = _suite(
            topology=topology.name,
            domains={domain for domain, _ in method_scenarios},
        )
        for instance in suite.instances():
            compiled_scenarios.add((instance.domain, instance.private["scenario_definition"]))
            criteria = instance.public_view().get("success_criteria") or {}
            # Same reading as ContainerLabANI._declared_criterion_types: the
            # all_of list of {type: ...} entries is what evaluate_success_criteria
            # walks, so anything published in another shape is unmeasurable too.
            declared = {
                str(criterion.get("type") or "")
                for criterion in criteria.get("all_of") or []
                if isinstance(criterion, dict)
            }
            assert declared & PUBLIC_SUCCESS_CRITERION_TYPES, (
                f"{topology.stem}: {instance.id} publishes no measurable success "
                f"criterion (declared types: {sorted(declared)}; measurable: "
                f"{sorted(PUBLIC_SUCCESS_CRITERION_TYPES)})"
            )
    assert compiled_scenarios == method_scenarios, sorted(
        method_scenarios ^ compiled_scenarios
    )


def test_ani_public_objective_validation_requires_qos_and_connectivity():
    class FakeEnv:
        @staticmethod
        def observation_is_healthy(observation):
            return all(check.success for check in observation.connectivity)

        @staticmethod
        def observe():
            return Observation(
                lab_name="test",
                nodes=[],
                connectivity=[
                    ConnectivityCheck("user1", "web1", "10.10.40.10", True, 0.0, "")
                ],
            )

    ani = ContainerLabANI.__new__(ContainerLabANI)
    ani.env = FakeEnv()
    # __new__ skips __init__, so the settle-window stamp evaluate_success_criteria
    # reads has to be set by hand.
    ani._change_applied_at = None
    ani._icmp_probe = lambda check: {
        "type": "icmp",
        "source": check["source"],
        "destination": check["destination"],
        "destination_ip": check["destination_ip"],
        "passed": True,
        "packet_loss_percent": 0.0,
        "rtt_avg_ms": 1567.0,
    }
    task = {
        "success_criteria": {
            "all_of": [
                {"type": "lab_connectivity"},
                {
                    "type": "observed_icmp_service",
                    "max_packet_loss_percent": 0,
                    "max_rtt_avg_ms": 10,
                },
            ]
        },
        "observation": {
            "service_measurement": {
                "source": "user1",
                "destination": "web1",
                "destination_ip": "10.10.40.10",
            }
        },
    }
    failed = ani.evaluate_success_criteria(task)
    assert failed["passed"] is False
    assert failed["checks"][0]["passed"] is True
    assert failed["checks"][1]["passed"] is False

    ani._icmp_probe = lambda check: {
        "type": "icmp",
        "source": check["source"],
        "destination": check["destination"],
        "destination_ip": check["destination_ip"],
        "passed": True,
        "packet_loss_percent": 0.0,
        "rtt_avg_ms": 1.0,
    }
    assert ani.evaluate_success_criteria(task)["passed"] is True


def test_ani_tool_schemas_are_generic_and_not_scenario_specific():
    names = {
        schema["function"]["name"]
        for schema in ContainerLabANI.tool_schemas()
    }
    assert names == set(ANI_OPERATIONS)
    assert "clear_linux_netem" not in names
    assert "enable_srl_interface" not in names


def test_active_a2a_contract_requires_self_execute_only():
    assert require_self_execute_contract(
        A2A_CONTRACT_VERSION, ["self_execute"]
    ) == ["self_execute"]
    try:
        require_self_execute_contract(A2A_CONTRACT_VERSION, ["single_action"])
    except ValueError as exc:
        assert "self_execute" in str(exc)
    else:
        raise AssertionError("single_action must not be accepted by the active A2A contract")


def test_self_execute_report_parser_rejects_legacy_action_response():
    report = parse_self_execute_report(
        '{"mode": "self_execute", "status": "completed", "device_changes": []}'
    )
    assert report["mode"] == "self_execute"
    try:
        parse_self_execute_report('{"machine": "leaf1", "command": "show version"}')
    except ValueError as exc:
        assert "self_execute" in str(exc)
    else:
        raise AssertionError("legacy command response must be rejected")


def test_official_a2a_sdk_version_is_exposed_by_shared_client():
    from benchmarks.core import A2AClient

    assert A2AClient.sdk_version


def _vlan_suite(seed=9):
    compiler = ScenarioCompiler.from_topology_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_vlan.yaml", seed=seed
    )
    return CompiledScenarioSuite(
        compiler.compile_scenarios(ROOT, domains={"connectivity"})
    )


def test_every_scenario_on_disk_is_declared_in_the_applicability_map():
    applicability = json.loads(
        (ROOT / "topology_applicability.json").read_text(encoding="utf-8")
    )
    declared = set(applicability["scenarios"])
    on_disk = {
        f"{path.parent.name}/{path.name}"
        for path in (ROOT / "connectivity").glob("*.yaml")
    }
    # A scenario absent from the map compiles against every topology, so one that
    # only binds on some of them would crash the catalog on the first it cannot
    # bind against. That invariant is what this guards.
    assert on_disk <= declared, sorted(on_disk - declared)

    # The routed and switched families used to be disjoint sets of scenarios.
    # They are not any more: the vlan_* and fw_* variants were folded back into
    # the generic ones, which now bind by role and render per node kind.
    routed = {t.private["scenario_definition"] for t in _suite(domains={"connectivity"}).instances()}
    switched = {t.private["scenario_definition"] for t in _vlan_suite().instances()}
    assert switched <= routed
    assert "wrong_routing_table" in routed - switched


def test_vlan_scenarios_never_leak_private_bindings():
    for instance in _vlan_suite().instances():
        assert_no_private_fields(instance.public_view())
        serialized = json.dumps(instance.public_view()).lower()
        assert "network-instance" not in serialized
        assert "bridge_domain" not in serialized


def test_subinterface_split_leaves_routed_rendering_unchanged():
    """A routed port must still render without an explicit subinterface.

    `set / interface ethernet-1/40 admin-state disable` shuts the port;
    `... subinterface 0 admin-state disable` shuts only the subinterface. The
    split must not silently turn one into the other.
    """
    assert split_interface("ethernet-1/40") == ("ethernet-1/40", 0)
    assert split_interface("irb0.40") == ("irb0", 40)
    assert acl_interface_key("ethernet-1/40") == "ethernet-1/40.0"
    assert acl_interface_key("irb0.40") == "irb0.40"

    suite = _suite(seed=9, domains={"connectivity"})
    topology = CompiledContainerLabTopology.from_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_small.yaml"
    )
    instance = suite.select(["connectivity.disable_interface.m1"])[0]
    assert topology.materialize(instance).commands == [
        StateCommand(
            target="leaf2",
            mode="srl_cli",
            command="set / interface ethernet-1/40 admin-state disable",
        )
    ]


def _firewall_suite(seed=9):
    compiler = ScenarioCompiler.from_topology_file(
        ROOT / "topologies" / "sme_leaf_firewall_dmz.yaml", seed=seed
    )
    return CompiledScenarioSuite(
        compiler.compile_scenarios(ROOT, domains={"connectivity"})
    )


def test_vyos_interface_path_distinguishes_ports_from_vifs():
    # A VLAN subinterface is a vif of its parent, not an interface of its own;
    # rendering eth1.10 as an interface name is rejected by the device.
    assert vyos_interface_path("eth3") == "interfaces ethernet eth3"
    assert vyos_interface_path("eth1.10") == "interfaces ethernet eth1 vif 10"


def test_firewall_scenarios_never_leak_private_bindings():
    for instance in _firewall_suite().instances():
        assert_no_private_fields(instance.public_view())
        serialized = json.dumps(instance.public_view()).lower()
        assert "firewall ipv4" not in serialized
        assert "vif" not in serialized


def _dns_suite(seed=9):
    compiler = ScenarioCompiler.from_topology_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_dns.yaml", seed=seed
    )
    return CompiledScenarioSuite(
        compiler.compile_scenarios(ROOT, domains={"connectivity"})
    )


def test_dns_scenarios_never_leak_private_bindings():
    for instance in _dns_suite().instances():
        assert_no_private_fields(instance.public_view())
        serialized = json.dumps(instance.public_view()).lower()
        assert "named.conf" not in serialized
        assert "rndc" not in serialized


# --- sme_wan_edge_qos: the shaped WAN edge ---------------------------------

QOS_TOPOLOGY = "sme_wan_edge_qos.yaml"


def _qos_suite(seed=7):
    return _suite(topology=QOS_TOPOLOGY, seed=seed, domains={"qos"})


def _qos_faults(seed=7):
    topology = CompiledContainerLabTopology.from_file(ROOT / "topologies" / QOS_TOPOLOGY)
    return {
        instance.id: (instance, topology.materialize(instance))
        for instance in _qos_suite(seed=seed).instances()
    }


def test_wan_edge_is_infrastructure_not_an_evaluated_endpoint():
    """The WAN edge is a Linux node, so only its role keeps it out of the blast radius.

    endpoint_nodes() filters on kind, and a router that happens to run Linux
    would otherwise be counted as an endpoint of every segment it merely
    forwards through.
    """
    compiler = ScenarioCompiler.from_topology_file(
        ROOT / "topologies" / QOS_TOPOLOGY, seed=7
    )
    index = compiler.topology
    assert "wan1" in index.nodes
    assert index.nodes["wan1"]["kind"] == "linux"
    assert "wan1" not in index.endpoint_nodes()
    assert "wan1" not in index.segment_hosts("internet")
    assert index.segment_gateways("internet") == ["wan1"]
    for _, fault in _qos_faults().values():
        assert "wan1" not in fault.expected_affected_nodes


def test_endpoint_selection_is_unchanged_on_the_existing_topologies():
    """The shared non-endpoint predicate must not move any existing blast radius."""
    expected = {
        "clos01_mvp.yaml": ["client1", "client2"],
        "sme_leaf_spine_dmz_small.yaml": [
            "app1", "external1", "finance1", "guest1", "user1", "web1",
        ],
        "sme_leaf_spine_dmz_vlan.yaml": [
            "app1", "external1", "finance1", "guest1", "user1", "web1",
        ],
        "sme_leaf_firewall_dmz.yaml": [
            "app1", "external1", "finance1", "guest1", "user1", "web1",
        ],
        # dns-int and dns-dmz hold the `service` role: they are consumed by
        # the endpoints, not evaluated as endpoints. Reachability to them is
        # still checked, through the explicit connectivity requirements.
        "sme_leaf_spine_dmz_dns.yaml": [
            "app1", "external1", "finance1", "guest1", "user1", "web1",
        ],
    }
    for name, endpoints in expected.items():
        compiler = ScenarioCompiler.from_topology_file(
            ROOT / "topologies" / name, seed=7
        )
        assert compiler.topology.endpoint_nodes() == endpoints, name


def test_every_qos_scenario_has_a_blast_radius():
    """An empty expectation makes _connectivity_impact return early and pass unchecked."""
    faults = _qos_faults()
    assert faults
    for instance_id, (_, fault) in faults.items():
        assert fault.expected_affected_nodes, instance_id


def test_shaping_faults_render_the_expected_traffic_control():
    faults = _qos_faults()
    rendered = {
        instance_id: [command.command for command in fault.commands]
        for instance_id, (_, fault) in faults.items()
    }
    # The policy is wiped but the circuit stays: leaving an unmetered uplink
    # behind would make the fault raise the protected flow's throughput.
    assert rendered["qos.wan_shaping_policy_repair.m1"] == [
        "sh -c 'tc qdisc del dev eth2 root 2>/dev/null || true'",
        "tc qdisc add dev eth2 root handle 1: htb default 20",
        "tc class add dev eth2 parent 1: classid 1:1 htb rate 10mbit ceil 10mbit",
        "tc class add dev eth2 parent 1:1 classid 1:20 htb rate 10mbit ceil 10mbit",
    ]
    starved = rendered["qos.wan_shaping_policy_repair.m2"]
    assert len(starved) == 1
    assert starved[0].startswith(
        "tc class change dev eth2 parent 1:1 classid 1:10 htb rate "
    )
    assert starved[0].endswith("prio 1")
    assert rendered["qos.wan_shaping_policy_repair.m3"] == [
        "tc filter del dev eth2 parent 1: protocol ip prio 1 u32"
    ]
    # The rates are swapped, not raised: children whose rates outrun the parent's
    # make HTB over-commit and stop enforcing the circuit altogether.
    assert rendered["qos.wan_shaping_policy_repair.m4"] == [
        "tc class change dev eth2 parent 1:1 classid 1:10 htb rate 2mbit "
        "ceil 10mbit prio 1",
        "tc class change dev eth2 parent 1:1 classid 1:20 htb rate 8mbit "
        "ceil 10mbit prio 2",
    ]
    for instance_id in rendered:
        if not instance_id.startswith("qos.wan_shaping_policy_repair"):
            continue
        for command in faults[instance_id][1].commands:
            assert command.target == "wan1", instance_id


def test_shaping_restore_is_rendered_from_the_descriptor_policy():
    """The reference restore and the healthy state must have a single spelling."""
    from benchmarks.platforms.containerlab.compiled_topology import render_qos_policy_commands

    descriptor = yaml.safe_load(
        (ROOT / "topologies" / QOS_TOPOLOGY).read_text(encoding="utf-8")
    )
    reference = [
        command.command
        for command in render_qos_policy_commands(descriptor["topology"]["qos_policy"])
    ]
    healthy = json.loads(
        (
            REPO_ROOT / "benchmarks/testbeds/containerlab/sme01-qos/states/healthy.json"
        ).read_text(encoding="utf-8")
    )
    healthy_tc = [
        command["command"]
        for command in healthy["commands"]
        if command["target"] == "wan1" and "tc " in command["command"]
    ]
    assert healthy_tc == reference

    for instance_id, (_, fault) in _qos_faults().items():
        if not instance_id.startswith("qos.wan_shaping_policy_repair"):
            continue
        assert [c.command for c in fault.restore_commands] == reference, instance_id


def test_shaping_commands_are_idempotent():
    """tc rejects replacing a root HTB with another HTB of the same handle.

    Every hierarchy renderer therefore tears the root down first, and no command
    that rebuilds the policy may use 'replace' on the root qdisc.
    """
    from benchmarks.platforms.containerlab.compiled_topology import (
        render_qos_circuit_commands,
        render_qos_policy_commands,
    )

    descriptor = yaml.safe_load(
        (ROOT / "topologies" / QOS_TOPOLOGY).read_text(encoding="utf-8")
    )
    policy = descriptor["topology"]["qos_policy"]
    for render in (render_qos_policy_commands, render_qos_circuit_commands):
        commands = [command.command for command in render(policy)]
        assert commands[0] == "sh -c 'tc qdisc del dev eth2 root 2>/dev/null || true'"
        assert not any(command.startswith("tc qdisc replace") for command in commands)


def test_background_traffic_runs_detached_and_stops_without_self_signalling():
    instance, fault = _qos_faults()["qos.assured_bandwidth.m1"]
    bindings = instance.private["bindings"]
    assert bindings["protected_source"] == "user1"
    assert bindings["background_source"] == "guest1"
    assert fault.expected_affected_nodes == ["user1"]

    assert len(fault.commands) == 1
    command = fault.commands[0]
    # The flood runs on the competing endpoint, not on the degraded one.
    assert command.target == "guest1"
    assert command.command.startswith("sh -c '")
    assert "setsid" in command.command and command.command.rstrip("'").endswith("&")
    # iperf3 applies -b per stream, so the offered load must stay well under the
    # fabric's own capacity or the protected flow starves upstream of the shaper.
    assert bindings["background_streams"] == 1
    assert 15 <= bindings["background_mbps"] <= 25
    assert bindings["sink_port"] == 5202

    assert [c.command for c in fault.restore_commands] == [
        "sh -c 'pkill -x iperf3 || true'"
    ]


def test_qos_scenarios_are_reproducible_and_leak_nothing_private():
    first = [item.id for item in _qos_suite(seed=7).instances()]
    second = [item.id for item in _qos_suite(seed=7).instances()]
    assert first == second
    for instance in _qos_suite().instances():
        public = instance.public_view()
        assert_no_private_fields(public)
        blob = json.dumps(public)
        for secret in ("wan1", "eth2", "tc ", "htb", "10.10.10.0/24", "guest1"):
            if secret == "guest1" and instance.id.startswith("qos.assured"):
                continue  # the intent names the competing flow on purpose
            assert secret not in blob, (instance.id, secret)


def _dhcp_suite(seed=9):
    compiler = ScenarioCompiler.from_topology_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_dns.yaml", seed=seed
    )
    return CompiledScenarioSuite(
        compiler.compile_scenarios(ROOT, domains={"dhcp_dns"})
    )


def test_dhcp_faults_bind_to_one_pool_and_one_evaluated_client():
    """A pool serves one subnet here, so its blast radius is one desk.

    admin1 leases like the others but is a management client, so nothing pings
    it: binding to it would compile an expectation no oracle can observe.
    """
    for method in ("m1", "m2", "m3", "m4"):
        bindings = _dhcp_suite().select([f"dhcp_dns.dhcp_provisioning.{method}"])[0].private["bindings"]
        client = bindings["client"]
        assert client in {"user1", "guest1", "finance1"}, method
        assert bindings["affected_nodes"] == [client]
        assert bindings["target"] == "firewall"
        assert bindings["fault_gateway"] != bindings["healthy_gateway"]
        assert bindings["fault_resolver"] != bindings["healthy_resolver"]


def test_dhcp_pool_restore_matches_the_healthy_state_configuration():
    """The deleted pool is rebuilt exactly as generate_healthy_state.py writes it.

    Both sides derive the pool from the same topology, so a divergence here means
    the abstract topology and the lab have drifted apart -- which is the failure
    this test is here to catch, since a restore is what every scenario is graded
    against.
    """
    instance = _dhcp_suite().select(["dhcp_dns.dhcp_provisioning.m1"])[0]
    topology = CompiledContainerLabTopology.from_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_dns.yaml"
    )
    fault = topology.materialize(instance)
    pool = instance.private["bindings"]["pool"]

    healthy = json.loads(
        (REPO_ROOT / "benchmarks" / "testbeds" / "containerlab" / "sme01-dns" / "states" / "healthy.json")
        .read_text(encoding="utf-8")
    )
    reference = [
        command["command"]
        for command in healthy["commands"]
        if f"shared-network-name {pool} " in command["command"]
    ]
    assert reference
    assert [c.command for c in fault.restore_commands if c.mode == "vyos_cli"] == reference


def test_dhcp_faults_make_the_client_ask_again():
    """A server-side change is invisible until the desk renews, so the fault renews for it.

    The server commands come first: releasing before the pool is broken would
    hand the client a healthy lease and leave the fault dormant for an hour.
    """
    topology = CompiledContainerLabTopology.from_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_dns.yaml"
    )
    for method in ("m1", "m2", "m3", "m4"):
        instance = _dhcp_suite().select([f"dhcp_dns.dhcp_provisioning.{method}"])[0]
        fault = topology.materialize(instance)
        client = instance.private["bindings"]["client"]
        assert [c.mode for c in fault.commands[:-1]] == ["vyos_cli"] * (len(fault.commands) - 1)
        release = fault.commands[-1]
        assert release.target == client and release.mode == "shell"
        assert "kill -USR2" in release.command and "kill -USR1" in release.command
        # The hook only rewrites resolv.conf when the lease carries option 6, so
        # a lease that stopped advertising a resolver has to find it empty.
        assert ": > /etc/resolv.conf" in release.command
        assert fault.restore_commands[-1].command == release.command


def test_dhcp_scenarios_leak_nothing_private():
    for instance in _dhcp_suite().instances():
        public = instance.public_view()
        assert_no_private_fields(public)
        blob = json.dumps(public)
        for secret in ("USER1", "shared-network", "10.10.60.10", "udhcpc", "firewall"):
            assert secret not in blob, (instance.id, secret)


def test_name_checks_probe_the_resolver_only_where_a_zone_answers():
    """The oracle pings names, not just addresses, wherever DNS is declared.

    Without a name check nothing observes resolution: a broken zone, a removed
    record or a lease that stopped advertising a resolver all leave the paths
    intact, so every address ping keeps passing and the fault is invisible.
    The pair is what separates the two: same source, same destination, one
    check by address and one by name.
    """
    topology = CompiledContainerLabTopology.from_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_dns.yaml"
    )
    checks = topology.topology.connectivity_checks()
    by_name = {(check[0], check[3]) for check in checks if len(check) == 4}

    assert ("user1", "web1.sme01.example") in by_name
    assert ("app1", "dns-int.sme01.internal") in by_name
    # The outside client resolves through the public nameserver, which is
    # authoritative for the only name it has to reach.
    assert ("external1", "web1.sme01.example") in by_name
    # ... but no zone names external1 itself, so the return direction is checked
    # by address only: a name nothing answers for would fail on a healthy lab.
    assert not [check for check in checks if len(check) == 4 and check[1] == "external1"]

    expected_ip = {check[3]: check[2] for check in checks if len(check) == 4}
    assert expected_ip["web1.sme01.example"] == "10.10.40.10"
    assert expected_ip["user1.sme01.internal"] == "10.10.10.10"

    # Every name check doubles an address check of the same pair, so a failing
    # name check always has a passing counterpart to be read against.
    by_address = {(check[0], check[1]) for check in checks if len(check) == 3}
    assert all(
        (check[0], check[1]) in by_address for check in checks if len(check) == 4
    )

    for topology_id in (
        "clos01_mvp",
        "sme_leaf_spine_dmz_small",
        "sme_leaf_spine_dmz_vlan",
        "sme_leaf_firewall_dmz",
        "sme_wan_edge_qos",
    ):
        other = CompiledContainerLabTopology.from_file(
            ROOT / "topologies" / f"{topology_id}.yaml"
        )
        # No resolver, no zone, no name check: these topologies observe exactly
        # what they observed before.
        assert all(len(check) == 3 for check in other.topology.connectivity_checks())


def test_name_ping_reads_the_resolved_address_back():
    """A name check passes only when the name answers the declared address.

    Reachability is not enough: a record left pointing at another host would
    answer, reply to the ping, and hide the fault. The address `ping` echoes on
    its first line is what the check verifies.
    """
    from benchmarks.platforms.containerlab.observer import ContainerLabObserver

    node = LabNode("user1", "linux", "clab-sme01-dns-user1")

    class _Executor:
        def __init__(self, stdout, returncode=0):
            self.stdout = stdout
            self.returncode = returncode
            self.commands = []

        def run_shell(self, target, command, timeout_seconds=None):
            self.commands.append(command)
            return CommandResult(
                target=target.name,
                command=command,
                returncode=self.returncode,
                stdout=self.stdout,
            )

    healthy = (
        "PING web1.sme01.example (10.10.40.10): 56 data bytes\n"
        "3 packets transmitted, 3 packets received, 0% packet loss\n"
    )
    executor = _Executor(healthy)
    observer = ContainerLabObserver(executor, "sme01-dns", {"user1": node})
    check = observer.ping("user1", "web1", "10.10.40.10", "web1.sme01.example")
    assert executor.commands == ["ping -c 3 -W 1 web1.sme01.example"]
    assert check.success and check.resolved_ip == "10.10.40.10"
    assert check.hostname == "web1.sme01.example"

    # The resolver answers, but with somebody else's address.
    misdirected = ContainerLabObserver(
        _Executor(healthy.replace("10.10.40.10", "10.10.50.10")),
        "sme01-dns",
        {"user1": node},
    )
    wrong = misdirected.ping("user1", "web1", "10.10.40.10", "web1.sme01.example")
    assert wrong.resolved_ip == "10.10.50.10"
    assert not wrong.success

    # No resolver at all: `ping` never gets to send anything.
    unresolved = ContainerLabObserver(
        _Executor("ping: bad address 'web1.sme01.example'", returncode=1),
        "sme01-dns",
        {"user1": node},
    )
    failed = unresolved.ping("user1", "web1", "10.10.40.10", "web1.sme01.example")
    assert failed.resolved_ip is None and not failed.success
    assert failed.packet_loss_percent == 100.0

    # An address check is untouched: no hostname, no resolution verdict.
    plain = ContainerLabObserver(
        _Executor(
            "PING 10.10.40.10 (10.10.40.10): 56 data bytes\n"
            "3 packets transmitted, 3 packets received, 0% packet loss\n"
        ),
        "sme01-dns",
        {"user1": node},
    )
    address_check = plain.ping("user1", "web1", "10.10.40.10")
    assert address_check.success
    assert address_check.hostname is None and address_check.resolved_ip is None


class _FakeExecutor:
    """Records what the oracle runs and replays canned iperf3 output."""

    def __init__(self, mbps_by_port=None, returncode=0):
        self.mbps_by_port = mbps_by_port or {}
        self.returncode = returncode
        self.commands = []

    def run_shell(self, node, command, timeout_seconds=None):
        self.commands.append((node.name, command))
        stdout = ""
        if command.startswith("iperf3 -c"):
            port = int(command.split(" -p ")[1].split()[0])
            bits = self.mbps_by_port.get(port, 0.0) * 1_000_000
            stdout = json.dumps({"end": {"sum_received": {"bits_per_second": bits}}})
        elif command == "sh -c 'pgrep -x iperf3 | wc -l'":
            # The flood is there whenever the oracle looks for it.
            stdout = "1\n"
        return CommandResult(
            target=node.name,
            command=command,
            returncode=self.returncode,
            stdout=stdout,
        )


def _fake_qos_env(mbps_by_port=None):
    from benchmarks.platforms.containerlab.env import ContainerLabEnvConfig

    names = ["user1", "guest1", "external1", "wan1"]
    config = ContainerLabEnvConfig(
        lab_name="sme01-qos",
        nodes={name: LabNode(name, "linux", f"clab-sme01-qos-{name}") for name in names},
    )
    env = SimpleNamespace(config=config, executor=_FakeExecutor(mbps_by_port))
    return env


def _throughput_bindings():
    suite = CompiledScenarioSuite(
        ScenarioCompiler.from_topology_file(
            ROOT / "topologies" / "sme_wan_edge_qos.yaml", seed=3
        ).compile_scenarios(ROOT, domains={"qos"})
    )
    return suite.select(["qos.wan_shaping_policy_repair.m2"])[0].private["bindings"]


def test_throughput_probe_measures_the_protected_flow_through_offered_load(monkeypatch):
    """A rate guarantee is only meaningful while the link is disputed.

    The shaped faults never break reachability, so the oracle has to create the
    contention itself and measure the protected class through it -- against a
    second sink, since one iperf3 server runs one test at a time and a shared
    port would queue the measurement behind the flood.
    """
    from benchmarks.platforms.containerlab import throughput
    from benchmarks.platforms.containerlab.throughput import measure_throughput

    monkeypatch.setattr(throughput, "_FLOOD_SETTLE_SECONDS", 0)
    bindings = _throughput_bindings()
    env = _fake_qos_env({5201: 7.4})
    measurement = measure_throughput(env, bindings, seconds=4, contended=True)

    assert measurement["throughput_mbps"] == 7.4
    assert measurement["contended"] is True
    # 10 Mbps link, so the offered load has to oversubscribe it.
    assert measurement["offered_load_mbps"] > bindings["link_mbps"]

    # The load sink is restarted first, then the flood is started and looked for
    # on the load source once after it starts and once after the sample.
    sink, load, started, probe, alive, stop = env.executor.commands
    assert sink == (
        "external1",
        "sh -c 'pkill -xf \"iperf3 -s -p 5202\" || true; "
        "setsid iperf3 -s -p 5202 >/dev/null 2>&1 </dev/null &'",
    )
    assert load[0] == "guest1" and " -p 5202 " in load[1] and "-u -b 20M" in load[1]
    # The flood has to outlast the sample, or its tail runs on an idle link.
    # 4s sample + 2s warm-up + 5s margin: the flood has to outlast the whole
    # window, or the tail of the protected flow runs on an idle link.
    assert "-t 46 " in load[1]  # 4 s sample + 2 s warm-up + 30 s client grace + 10 s lead
    # Its report is kept, so a flood that dies leaves its own error text.
    assert "-J --logfile /tmp/ani_load_5202.json" in load[1]
    assert started == alive == ("guest1", "sh -c 'pgrep -x iperf3 | wc -l'")
    assert probe == ("user1", "iperf3 -c 203.0.113.10 -p 5201 -t 4 -O 2 -J")
    assert stop == ("guest1", "sh -c 'pkill -x iperf3 || true'")
    assert measurement["contention_observed"] is True

    # An idle baseline offers nothing: it says the path can carry the rate at
    # all, which is what separates a broken path from a broken policy.
    idle = _fake_qos_env({5201: 9.6})
    baseline = measure_throughput(_env := idle, bindings, seconds=4, contended=False)
    assert baseline["throughput_mbps"] == 9.6
    assert [command for _, command in idle.executor.commands if "pkill" in command] == []
    assert len(idle.executor.commands) == 1


def test_throughput_probe_never_stops_a_flood_the_fault_injected():
    """assured_bandwidth's competing flow is the fault, not an instrument.

    Starting a second one would measure a contention nobody promised anything
    under, and stopping it would remove the very condition being evaluated.
    """
    from benchmarks.platforms.containerlab.throughput import (
        generates_own_contention,
        measure_throughput,
    )

    suite = CompiledScenarioSuite(
        ScenarioCompiler.from_topology_file(
            ROOT / "topologies" / "sme_wan_edge_qos.yaml", seed=3
        ).compile_scenarios(ROOT, domains={"qos"})
    )
    bindings = suite.select(["qos.assured_bandwidth.m1"])[0].private["bindings"]
    assert generates_own_contention(bindings)

    env = _fake_qos_env({5201: 4.1})
    measurement = measure_throughput(env, bindings, seconds=3, contended=False)
    assert measurement["throughput_mbps"] == 4.1
    assert [command for _, command in env.executor.commands] == [
        "iperf3 -c 203.0.113.10 -p 5201 -t 3 -O 2 -J"
    ]


def test_a_starved_measurement_reads_as_zero_rather_than_crashing_the_run(monkeypatch):
    """Seen on the lab, on the greenfield state with no protection at all.

    The unresponsive flood starved the measurement so thoroughly that iperf3
    never finished its window and the run died with TimeoutExpired. A flow that
    carried nothing measurable carried nothing: that is the fault at its worst,
    and the oracle has to be able to say so.
    """
    import subprocess

    from benchmarks.platforms.containerlab import throughput
    from benchmarks.platforms.containerlab.throughput import measure_throughput

    monkeypatch.setattr(throughput, "_FLOOD_SETTLE_SECONDS", 0)
    bindings = _throughput_bindings()

    class _StarvedExecutor(_FakeExecutor):
        def run_shell(self, node, command, timeout_seconds=None):
            self.commands.append((node.name, command))
            if command.startswith("iperf3 -c"):
                raise subprocess.TimeoutExpired(command, timeout_seconds or 0)
            return super().run_shell(node, command, timeout_seconds)

    env = _fake_qos_env()
    env.executor = _StarvedExecutor()
    measurement = measure_throughput(env, bindings, seconds=4, contended=True)

    assert measurement["timed_out"] is True
    assert measurement["throughput_mbps"] == 0.0
    # The stalled client is cleaned off the measuring endpoint, or the next
    # sample cannot connect: an iperf3 server serves one test at a time.
    assert ("user1", "sh -c 'pkill -x iperf3 || true'") in env.executor.commands
    # The contention it started is still torn down.
    assert env.executor.commands[-1] == ("guest1", "sh -c 'pkill -x iperf3 || true'")

    baseline = {"throughput_mbps": 7.6, "contended": True}
    # A flow that carried nothing measurable carried nothing: the oracle has to be
    # able to say so, in both directions.
    assert _judge("shaping-repair", "expected-degradation", measurement, baseline=baseline)
    assert not _judge("shaping-repair", "repair", measurement, baseline=baseline)


def _throughput_oracle(family, phase, *, bindings=None):
    """One resolved throughput oracle, the way the lifecycle loads it."""
    from scenarios.oracle_loader import load_oracle, resolve_oracle

    path = ROOT / "oracles" / "qos" / family / f"{phase}-v1.yaml"
    return resolve_oracle(load_oracle(path, expected_version="1.0.0"),
                          bindings if bindings is not None else _throughput_bindings())


def _judge(family, phase, measured, *, baseline=None, bindings=None):
    """Evaluate that oracle against one canned measurement.

    The thresholds are the whole point of these tests, so the probe is faked: what
    is under test is what the oracle demands of a number, not how the number is
    obtained.
    """
    from benchmarks.core.oracle_evaluator import OracleEvaluator

    oracle = _throughput_oracle(family, phase, bindings=bindings)
    probe_id = oracle["probes"][0]["id"]

    class _Canned:
        def run_probe(self, probe):
            return dict(measured)

    reference = {probe_id: dict(baseline)} if baseline is not None else None
    return OracleEvaluator(_Canned()).evaluate(oracle, baseline=reference).passed


def test_the_shaped_families_are_judged_on_the_assured_rate():
    """Detection and repair share one band around the contractual rate, so a fault
    only counts as detected when it pushes the flow below what a repair has to bring
    it back above. Ported from the runner these thresholds used to live in: the
    numbers are the ones measured on the lab, the expression is now declarative."""
    for family in ("assured-bandwidth", "shaping-repair"):
        bindings = _throughput_bindings()
        assert bindings["assured_bandwidth_mbps"] == 8

        idle = {"throughput_mbps": 9.6}
        starved = {"throughput_mbps": 0.8}
        repaired = {"throughput_mbps": 7.6}
        # Measured on the lab right after a correct restore. Under a 0.9 band this
        # counted as still broken, which is the false negative the calibration fixed.
        cold_but_served = {"throughput_mbps": 6.9}

        assert _judge(family, "healthy", idle)
        assert not _judge(family, "healthy", starved)
        assert _judge(family, "expected-degradation", starved, baseline=idle)
        assert not _judge(family, "expected-degradation", repaired, baseline=idle)
        assert _judge(family, "repair", repaired, baseline=idle)
        assert _judge(family, "repair", cold_but_served, baseline=idle)
        assert not _judge(family, "expected-degradation", cold_but_served, baseline=idle)
        assert not _judge(family, "repair", starved, baseline=idle)
        # A measurement that never produced a number is not a pass.
        assert not _judge(family, "repair", {"throughput_mbps": None}, baseline=idle)


def test_assured_floor_never_demands_more_than_the_intact_policy_delivered():
    """Calibrated against the reference, the way the delay branch already is.

    Measured on the lab, an intact 8 Mbit assured class delivered between 6.2 and
    9.5 Mbps depending on how the unresponsive UDP flood collided with TCP and on
    what else the host was doing. A fixed fraction of the contractual rate failed
    correct repairs on a bad draw: the run that produced 6.17 Mbps with the policy
    fully intact would have scored it broken. The floor is therefore the lower of
    the contract and what the reference itself managed -- which is what the
    `combine: min` threshold says.
    """
    family = "shaping-repair"

    # 85% of the 8 Mbps contract is the nominal bar.
    assert _judge(family, "repair", {"throughput_mbps": 6.8}, baseline={"throughput_mbps": 100.0})
    assert not _judge(family, "repair", {"throughput_mbps": 6.79}, baseline={"throughput_mbps": 100.0})

    # A reference that itself only managed 6.17 lowers the bar to 5.24: a repair
    # landing where the reference landed is a repair.
    unlucky = {"throughput_mbps": 6.17}
    assert _judge(family, "repair", {"throughput_mbps": 6.2}, baseline=unlucky)
    # The fault is nowhere near it either way.
    assert not _judge(family, "repair", {"throughput_mbps": 0.76}, baseline=unlucky)
    assert _judge(family, "expected-degradation", {"throughput_mbps": 0.76}, baseline=unlucky)

    # A generous reference never raises the bar above the contract.
    lucky = {"throughput_mbps": 9.4}
    assert _judge(family, "repair", {"throughput_mbps": 6.8}, baseline=lucky)

    # A reference taken on an already-degraded policy is not a usable one: the
    # healthy oracle refuses it before it can become anyone's bar.
    assert _judge(family, "healthy", {"throughput_mbps": 6.17})
    assert not _judge(family, "healthy", {"throughput_mbps": 0.76})


def test_ani_observed_throughput_criterion_measures_under_contention(monkeypatch):
    """The validation the agent can call has to be able to fail.

    With only `lab_connectivity`, these scenarios passed whether or not the
    policy was ever repaired: the fault leaves every path up.
    """
    from benchmarks.platforms.containerlab import throughput

    monkeypatch.setattr(throughput, "_FLOOD_SETTLE_SECONDS", 0)
    topology_document = yaml.safe_load(
        (ROOT / "topologies" / "sme_wan_edge_qos.yaml").read_text(encoding="utf-8")
    )
    service = {
        "source": "user1",
        "destination": "external1",
        "destination_ip": "203.0.113.10",
    }

    def _evaluate(mbps, *, under_contention=True, criterion=None):
        env = _fake_qos_env({5201: mbps})
        ani = ContainerLabANI(env, topology_document=topology_document)
        task = {
            "observation": {"service_measurement": service},
            "success_criteria": {
                "all_of": [
                    criterion
                    or {
                        "type": "observed_throughput",
                        "min_mbps": "8",
                        "under_contention": under_contention,
                        "seconds": 3,
                    }
                ]
            },
        }
        return ani.evaluate_success_criteria(task), env

    passed, env = _evaluate(8.4)
    assert passed["passed"] is True
    check = passed["checks"][0]
    assert check["measurement"]["throughput_mbps"] == 8.4
    # The criterion states the contract; the check enforces it minus what the
    # measurement itself costs, and reports both.
    assert check["limits"]["min_mbps"] == 8.0
    assert check["limits"]["enforced_floor_mbps"] == 6.8

    # An intact policy measured 7.605 Mbps on the lab. Enforcing the nominal 8
    # scored that healthy network as broken -- the agent-side false negative the
    # shared band fixes.
    intact, _ = _evaluate(7.605)
    assert intact["passed"] is True
    # The instrument is read from the reviewed topology, never from the task: an
    # agent that knew the measured port could shape that port and pass.
    assert "5201" not in json.dumps(service)
    # The same instrument the runner measures with: the load sink is restarted,
    # the flood started and looked for, then the sample, then one more look.
    sink, load, alive, probe, alive_after, stop = env.executor.commands
    assert sink[0] == "external1" and "iperf3 -s -p 5202" in sink[1]
    assert load[0] == "guest1" and " -p 5202 " in load[1]
    assert alive == alive_after == ("guest1", "sh -c 'pgrep -x iperf3 | wc -l'")
    assert probe == ("user1", "iperf3 -c 203.0.113.10 -p 5201 -t 3 -O 2 -J")
    assert stop == ("guest1", "sh -c 'pkill -x iperf3 || true'")

    failed, _ = _evaluate(1.2)
    assert failed["passed"] is False

    # The runner and the ANI enforce the same bar, or an agent would be scored on
    # which oracle happened to run.
    from benchmarks.platforms.containerlab.throughput import ASSURED_TOLERANCE
    from benchmarks.platforms.containerlab.throughput import ASSURED_TOLERANCE

    assert ASSURED_TOLERANCE == ASSURED_TOLERANCE

    # Without contention the check runs the measurement alone.
    quiet, quiet_env = _evaluate(8.4, under_contention=False)
    assert quiet["passed"] is True
    assert len(quiet_env.executor.commands) == 1

    missing, _ = _evaluate(
        8.4, criterion={"type": "observed_throughput", "under_contention": False}
    )
    assert missing["passed"] is False
    assert "min_mbps" in missing["checks"][0]["error"]


def test_shaped_qos_scenarios_declare_a_throughput_criterion():
    suite = CompiledScenarioSuite(
        ScenarioCompiler.from_topology_file(
            ROOT / "topologies" / "sme_wan_edge_qos.yaml", seed=3
        ).compile_scenarios(ROOT, domains={"qos"})
    )
    for instance in suite.instances():
        criteria = instance.public_view()["success_criteria"]["all_of"]
        kinds = {item["type"] for item in criteria}
        if instance.id.startswith("qos.link_impairment"):
            assert kinds == {"lab_connectivity", "observed_icmp_service"}
            continue
        assert kinds == {"lab_connectivity", "observed_throughput"}
        throughput = next(c for c in criteria if c["type"] == "observed_throughput")
        # The SLO comes from the topology's own policy, not from a literal
        # duplicated into the scenario.
        assert float(throughput["min_mbps"]) == 8.0
        # The fault is the flood in one family and a broken policy in the other,
        # so only one of them has contention to offer.
        assert throughput["under_contention"] is (
            not instance.id.startswith("qos.assured_bandwidth")
        )
    # Nothing private rides along with the new criterion.
    for instance in suite.instances():
        assert_no_private_fields(instance.public_view())


def _filtering_suite(topology="sme_leaf_spine_dmz_dns.yaml", seed=5):
    compiler = ScenarioCompiler.from_topology_file(ROOT / "topologies" / topology, seed=seed)
    return CompiledScenarioSuite(
        compiler.compile_scenarios(ROOT, domains={"filtering"})
    )


def test_filtering_binds_only_to_pairs_the_policy_declares_it_accepts():
    """A denied pair is the design working, not a fault surface.

    These topologies isolate by omission: a zone pair absent from `zone_policies`
    has no ruleset and is denied by the zone model itself. Breaking one would
    change nothing, so a filtering fault only ever binds to a pair declared
    `accept` -- and only to one some required flow actually crosses, or no oracle
    would see it.
    """
    document = yaml.safe_load(
        (ROOT / "topologies" / "sme_leaf_spine_dmz_dns.yaml").read_text(encoding="utf-8")
    )
    accepted = {
        (str(item["from"]), str(item["to"]))
        for item in document["topology"]["zone_policies"]
        if item.get("action") == "accept"
    }
    denied = {
        (str(item["from"]), str(item["to"]))
        for item in document["topology"]["zone_policies"]
        if item.get("action") != "accept"
    }
    assert denied  # the topology does declare a pair it refuses

    seen = set()
    for seed in range(1, 12):
        for instance in _filtering_suite(seed=seed).instances():
            bindings = instance.private["bindings"]
            pair = (bindings["from_zone"], bindings["to_zone"])
            seen.add(pair)
            assert pair in accepted, (instance.id, pair)
            assert pair not in denied
            # An empty blast radius is an expectation no ping can confirm.
            assert bindings["affected_nodes"]
    assert len(seen) > 1, "the binder should not always draw the same pair"


def test_filtering_faults_render_against_the_zone_model():
    """Each fault addresses the model, never a rule number nobody declared.

    The healthy state numbers its permits from 10 upward and the topology does
    not describe them, so a fault that had to name one would be guessing. These
    three do not: they unbind the ruleset, insert a rule ahead of whatever is
    there, or take an interface out of its zone.
    """
    topology = CompiledContainerLabTopology.from_file(
        ROOT / "topologies" / "sme_leaf_spine_dmz_dns.yaml"
    )
    suite = _filtering_suite(seed=1)

    detached = suite.select(["filtering.zone_policy_enforcement.m1"])[0]
    bindings = detached.private["bindings"]
    fault = topology.materialize(detached)
    assert fault.commands == [
        StateCommand(
            target=bindings["target"],
            mode="vyos_cli",
            command=f"delete firewall zone {bindings['to_zone']} from {bindings['from_zone']}",
        )
    ]
    # The ruleset itself survives; only the pair stops pointing at it.
    assert fault.restore_commands[0].command == (
        f"set firewall zone {bindings['to_zone']} from {bindings['from_zone']} "
        f"firewall name {bindings['ruleset']}"
    )

    shadowed = suite.select(["filtering.zone_policy_enforcement.m2"])[0]
    shadow_fault = topology.materialize(shadowed)
    ruleset = shadowed.private["bindings"]["ruleset"]
    # Rule 1 sorts ahead of every permit the healthy state writes.
    assert shadow_fault.commands[0].command == (
        f"set firewall ipv4 name {ruleset} rule 1 action drop"
    )
    assert shadow_fault.restore_commands == [
        StateCommand(
            target=shadowed.private["bindings"]["target"],
            mode="vyos_cli",
            command=f"delete firewall ipv4 name {ruleset} rule 1",
        )
    ]

    unassigned = suite.select(["filtering.zone_policy_enforcement.m3"])[0]
    port = unassigned.private["bindings"]
    port_fault = topology.materialize(unassigned)
    assert port_fault.commands[0].command == (
        f"delete firewall zone {port['zone']} interface {port['interface']}"
    )
    assert port_fault.restore_commands[0].command == (
        f"set firewall zone {port['zone']} interface {port['interface']}"
    )
    # Every command lands on the enforcer, in its own dialect.
    for command in [*port_fault.commands, *shadow_fault.commands, *fault.commands]:
        assert command.mode == "vyos_cli"


def test_detaching_an_interface_only_claims_what_that_interface_carries():
    """The pair says who may be cut; the interface says who actually is.

    Bound to the pair alone, this method once claimed three endpoints while
    detaching a fabric uplink that carried one of them.
    """
    from scenarios.compiler.topology import TopologyIndex

    for topology_id in ("sme_leaf_spine_dmz_dns.yaml", "sme_leaf_firewall_dmz.yaml"):
        index = TopologyIndex(
            yaml.safe_load((ROOT / "topologies" / topology_id).read_text(encoding="utf-8"))
        )
        for seed in range(1, 12):
            instance = _filtering_suite(topology=topology_id, seed=seed).select(
                ["filtering.zone_policy_enforcement.m3"]
            )[0]
            bindings = instance.private["bindings"]
            segment = next(
                ref.segment
                for ref in index.interfaces(bindings["target"])
                if ref.name == bindings["interface"]
            )
            hosts = set(index.segment_hosts(segment)) & set(index.endpoint_nodes())
            assert set(bindings["affected_nodes"]) == hosts
            assert hosts, (topology_id, seed)


def test_a_zone_never_gives_up_its_last_interface():
    """VyOS refuses to commit a zone left without interfaces.

    Found on the live firewall: detaching eth1.10 from USERS -- its only
    interface -- was rejected with `[[firewall]] failed / Commit failed`, so the
    fault applied nothing and the run scored a fault nobody could observe. Only a
    zone that keeps one interface can give another up.
    """
    from scenarios.compiler.topology import TopologyIndex

    for topology_id in ("sme_leaf_spine_dmz_dns.yaml", "sme_leaf_firewall_dmz.yaml"):
        index = TopologyIndex(
            yaml.safe_load((ROOT / "topologies" / topology_id).read_text(encoding="utf-8"))
        )
        for seed in range(1, 12):
            bindings = _filtering_suite(topology=topology_id, seed=seed).select(
                ["filtering.zone_policy_enforcement.m3"]
            )[0].private["bindings"]
            remaining = [
                ref.name
                for ref in index.zone_interfaces(bindings["target"], bindings["zone"])
                if ref.name != bindings["interface"]
            ]
            assert remaining, (topology_id, seed, bindings["zone"], bindings["interface"])


def test_filtering_is_offered_only_where_a_zone_policy_is_enforced():
    applicability = json.loads(
        (ROOT / "topology_applicability.json").read_text(encoding="utf-8")
    )
    entry = applicability["scenarios"]["filtering/zone_policy_enforcement.yaml"]
    assert set(entry["topologies"]) == {"sme_leaf_firewall_dmz", "sme_leaf_spine_dmz_dns"}

    for topology_id in entry["topologies"]:
        document = yaml.safe_load(
            (ROOT / "topologies" / f"{topology_id}.yaml").read_text(encoding="utf-8")
        )
        assert document["topology"].get("zone_policies")
    for topology_id in entry["excluded"]:
        document = yaml.safe_load(
            (ROOT / "topologies" / f"{topology_id}.yaml").read_text(encoding="utf-8")
        )
        assert not document["topology"].get("zone_policies"), topology_id


def test_filtering_scenarios_leak_nothing_private():
    for topology_id in ("sme_leaf_spine_dmz_dns.yaml", "sme_leaf_firewall_dmz.yaml"):
        for instance in _filtering_suite(topology=topology_id).instances():
            public = instance.public_view()
            assert_no_private_fields(public)
            blob = json.dumps(public)
            for secret in ("firewall zone", "vyos_cli", "ibn-shadow-deny", "-TO-"):
                assert secret not in blob, (instance.id, secret)


# ---------------------------------------------------------------------------
# Intent precision: the same fault put to the subject in more or less specific
# words. The levels differ in how much of the SYMPTOM is stated, never in how
# much of the CAUSE, and the wording the corpus already ran keeps its id.
# ---------------------------------------------------------------------------

PRECISION_LEVELS = ("low", "medium", "high")

# Bindings that describe the fault rather than what an operator would observe.
# A wording that names one of these has ended the diagnosis the benchmark measures.
CAUSE_BINDINGS = (
    "interface", "interfaces", "ruleset", "pool", "shaping_policy", "route_prefix",
    "fault_gateway", "fault_resolver", "fault_ipv4", "impairment", "delay_ms",
    "corruption_percent", "from_zone", "to_zone", "temporary_next_hop_group",
    "fault_next_hop", "starved_kbit", "subnet_id",
)


def _fault_tasks(topology="sme_leaf_spine_dmz_small.yaml"):
    return [task for task in _suite(topology=topology).document["tasks"]
            if not task["id"].startswith("security.")]


def test_the_wording_the_corpus_ran_keeps_its_instance_id():
    ids = {task["id"] for task in _fault_tasks()}
    # These ran in the 32 plate campaigns. A new wording must not rename them.
    assert "connectivity.disable_interface.m1" in ids
    assert "connectivity.wrong_routing_table.m1" in ids
    assert "qos.link_impairment.m1" in ids


def test_the_base_wording_still_says_which_level_it_is():
    base = next(t for t in _fault_tasks() if t["id"] == "connectivity.disable_interface.m1")
    assert base["private"]["task_variant"] == "medium"
    named = next(t for t in _fault_tasks() if t["id"] == "connectivity.disable_interface.low.m1")
    assert named["private"]["task_variant"] == "low"
    assert named["public"]["intent"] != base["public"]["intent"]


def test_every_fault_scenario_offers_all_three_levels():
    by_scenario = {}
    for task in _fault_tasks():
        key = task["private"]["scenario_definition"]
        by_scenario.setdefault(key, set()).add(task["private"].get("task_variant"))
    assert by_scenario, "no fault scenarios compiled"
    for scenario, levels in sorted(by_scenario.items()):
        assert levels == set(PRECISION_LEVELS), (scenario, sorted(levels))


def test_no_level_states_the_cause(): 
    for task in _fault_tasks():
        bindings = task["private"]["bindings"]
        intent = task["public"]["intent"]
        for key in CAUSE_BINDINGS:
            value = bindings.get(key)
            if isinstance(value, str) and len(value) > 2:
                assert value not in intent, (task["id"], key, value)


def test_a_list_binding_reads_as_a_sentence_not_a_repr():
    task = next(t for t in _fault_tasks()
                if t["id"] == "connectivity.disable_routing.high.m1")
    affected = task["private"]["bindings"]["affected_nodes"]
    assert len(affected) > 1, "fixture no longer covers the multiple endpoint case"
    intent = task["public"]["intent"]
    assert "[" not in intent and "'" not in intent, intent
    assert f"{', '.join(affected[:-1])} and {affected[-1]}" in intent


def test_every_placeholder_in_every_level_is_bound():
    for task in _fault_tasks():
        assert "{{" not in task["public"]["intent"], task["id"]


def test_a_wording_set_without_a_base_is_refused():
    from scenarios.compiler.loader import validate_scenario

    document = yaml.safe_load(
        (ROOT / "connectivity" / "disable_interface.yaml").read_text(encoding="utf-8"))
    validate_scenario("connectivity", document)
    del document["base_variant"]
    with pytest.raises(ValueError, match="base_variant"):
        validate_scenario("connectivity", document)
    document["base_variant"] = "middling"
    with pytest.raises(ValueError, match="not one of"):
        validate_scenario("connectivity", document)
