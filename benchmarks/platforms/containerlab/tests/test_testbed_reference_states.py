"""Tracked reference states equal their generators' output.

Every testbed that ships a ``states/generate_healthy_state.py`` treats the
generator as the source of truth and the JSON as its build product. Nothing
guarded that until 2026-09-04: merge commit 93eed8c resolved sme01-fw's
``healthy.json`` to a blob whose leaf1 block mixed another lab's routed lines
with stale layer-2 lines, every reader passed, and the first unified-judge run
died in ``apply_reference_state`` when SR Linux refused the commit.

The generators write to a path derived from their own directory depth, so each
is run from a scratch copy at the same depth with the repository's scenario
descriptors reachable, and its output is compared byte for byte.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
TESTBEDS = REPO / "benchmarks/testbeds/containerlab"
GENERATED = [
    ("sme01-fw", ("healthy.json",)),
    ("sme01-dns", ("healthy.json",)),
    ("sme01-vlan", ("healthy.json",)),
    ("sme01-qos", ("healthy.json", "healthy_greenfield.json")),
]


@pytest.mark.parametrize("testbed,outputs", GENERATED, ids=[t for t, _ in GENERATED])
def test_tracked_reference_state_equals_generator_output(testbed: str, outputs: tuple[str, ...]) -> None:
    states = TESTBEDS / testbed / "states"
    generator = states / "generate_healthy_state.py"
    assert generator.is_file(), generator
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scratch = root / "benchmarks/testbeds/containerlab" / testbed / "states"
        scratch.mkdir(parents=True)
        shutil.copy(generator, scratch / generator.name)
        scenarios = REPO / "scenarios"
        assert scenarios.is_dir(), f"topology descriptors missing at {scenarios}"
        (root / "scenarios").symlink_to(scenarios)
        completed = subprocess.run(
            [sys.executable, str(scratch / generator.name)],
            cwd=scratch, env={**os.environ, "PYTHONPATH": str(REPO)},
            capture_output=True, text=True, timeout=120,
        )
        assert completed.returncode == 0, completed.stderr[-2000:]
        for name in outputs:
            produced = (scratch / name).read_bytes()
            tracked = (states / name).read_bytes()
            assert produced == tracked, (
                f"{testbed}/states/{name} is not what generate_healthy_state.py "
                "produces; regenerate it (or fix the generator) before applying it to a lab")


def test_every_generator_present_is_pinned() -> None:
    """A testbed that gains a generator must be added to GENERATED, not forgotten."""
    present = sorted(p.parent.parent.name for p in TESTBEDS.glob("*/states/generate_healthy_state.py"))
    assert present == sorted(t for t, _ in GENERATED), present
