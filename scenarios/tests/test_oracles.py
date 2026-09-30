from __future__ import annotations

import unittest
from pathlib import Path

from scenarios.oracle_loader import OracleValidationError, load_oracle, resolve_oracle


ROOT = Path(__file__).resolve().parents[2]
ORACLE_ROOT = ROOT / "scenarios" / "oracles"



def _referenced_oracle_versions() -> dict[str, str]:
    """{oracle path: version} as the scenario files declare them, all agreeing."""
    import yaml

    versions: dict[str, str] = {}
    for scenario in sorted((ROOT / "scenarios").glob("*/*.yaml")):
        document = yaml.safe_load(scenario.read_text(encoding="utf-8")) or {}
        for reference in (document.get("oracles") or {}).values():
            if not isinstance(reference, dict) or "path" not in reference:
                continue
            path, version = str(reference["path"]), str(reference.get("version"))
            if path in versions and versions[path] != version:
                raise AssertionError(f"{path} is referenced at two versions: {versions[path]} and {version}")
            versions[path] = version
    return versions

class OracleContractTests(unittest.TestCase):
    def test_all_versioned_oracles_validate(self) -> None:
        paths = sorted(ORACLE_ROOT.rglob("*.yaml"))
        # Every set owes all four phases. A domain whose families are judged
        # differently keeps one set per family in its own directory -- qos does,
        # because the shaped families are measured on a rate and link impairment
        # on loss and latency -- so the unit here is the directory, not the domain.
        self.assertTrue(paths)
        by_set: dict[Path, set[str]] = {}
        for path in paths:
            by_set.setdefault(path.parent, set()).add(path.name)
        for directory, names in sorted(by_set.items()):
            with self.subTest(oracle_set=directory.relative_to(ROOT)):
                self.assertEqual(
                    {"healthy-v1.yaml", "expected-degradation-v1.yaml",
                     "repair-v1.yaml", "preservation-v1.yaml"},
                    names,
                )
        # A file's version is the one the scenarios reference: an oracle that changed
        # what it measures moves its version and every reference with it, so
        # provenance.oracle_versions tells a result judged on the new probes apart.
        referenced = _referenced_oracle_versions()
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                relative = path.relative_to(ROOT).as_posix()
                self.assertIn(relative, referenced, "oracle file no scenario references")
                document = load_oracle(path, expected_version=referenced[relative])
                expected_phase = path.name.replace("-v1.yaml", "").replace("-", "_")
                self.assertEqual(expected_phase, document["phase"])

    def test_exact_placeholders_are_resolved(self) -> None:
        document = load_oracle(ORACLE_ROOT / "connectivity" / "expected-degradation-v1.yaml")
        resolved = resolve_oracle(
            document,
            {"affected_nodes": ["user1", "leaf1"]},
        )
        probe = resolved["probes"][0]
        self.assertEqual(["user1", "leaf1"], probe["affected_nodes"])

    def test_missing_and_interpolated_bindings_are_rejected(self) -> None:
        document = load_oracle(ORACLE_ROOT / "connectivity" / "expected-degradation-v1.yaml")
        with self.assertRaisesRegex(OracleValidationError, "missing binding"):
            resolve_oracle(document, {})
        document["probes"][0]["source"] = "node-${bindings.source}"
        with self.assertRaisesRegex(OracleValidationError, "must be exact"):
            resolve_oracle(document, {"source": "user1", "affected_nodes": ["user1"]})

    def test_version_pin_is_enforced(self) -> None:
        path = ORACLE_ROOT / "qos" / "repair-v1.yaml"
        with self.assertRaisesRegex(OracleValidationError, "version mismatch"):
            load_oracle(path, expected_version="2.0.0")


if __name__ == "__main__":
    unittest.main()
