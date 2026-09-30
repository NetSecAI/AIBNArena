"""The register: what a seed stood for, so a seed alone can bring it back.

A seed is not a context on its own. The compiler draws from one stream seeded
with it, so the instance it lands on depends on the topology, on which scenarios
were compiled and in what order -- seed 9 is one fault under `connectivity-smoke`
and another under `qos-smoke`. What makes a seed sufficient is writing down,
once, what it stood for.

Two kinds of entry, because there are two things worth replaying:

* an **episode** seed is the scenario seed itself, the number the compiler drew
  with. It recalls one experiment, one scenario, one method and the exact fault
  that was injected;
* an **campaign** seed recalls a whole matrix: its subjects, its models, its
  scenario rows and the episode seed of every cell. Every record a campaign
  writes carries it in `provenance.seed_campaign`, so any one record leads
  back to the campaign it belonged to.

Each entry also carries a fingerprint of the resolved instance. Replaying
recompiles and compares: if the scenario catalogue has moved under a seed, the
episode is refused by name rather than run as if it were the same one.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from webui.config import REPOSITORY

#: Tracked by git, unlike the run directories beside it: a seed quoted in a
#: report is only resolvable if whoever reads the report has the register.
REGISTER = "reports/seed-registry.json"

SCHEMA_VERSION = "1.0"

#: Seeds are drawn from a range wide enough that a collision is a surprise
#: rather than a matter of time, and are still ordinary integers the compiler
#: takes unchanged.
SEED_RANGE = (100_000, 999_999_999)


def path() -> Path:
    """Where the register lives. `IBN_SEED_REGISTRY` moves it.

    The override exists so a test never writes to the register the repository
    ships: these entries are shared records, not scratch.
    """
    override = os.getenv("IBN_SEED_REGISTRY")
    return Path(override) if override else REPOSITORY / REGISTER


def load() -> dict[str, Any]:
    """The register as it stands, or an empty one."""
    try:
        document = json.loads(path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": SCHEMA_VERSION, "episodes": {}, "campaigns": {}}
    document.setdefault("episodes", {})
    document.setdefault("campaigns", {})
    return document


def save(document: Mapping[str, Any]) -> None:
    """Replace the register in one step.

    Written to a temporary file and moved into place: a register half-written by
    an interrupted run is the one file that must not be lost, because the
    records pointing at it cannot be resolved without it.
    """
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".seed-registry.", suffix=".tmp",
                                             dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(target)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def fingerprint(bindings: Mapping[str, Any], fault: Mapping[str, Any]) -> str:
    """What the seed actually resolved to, in one comparable string.

    The bindings and the fault commands together are the instance: which device,
    which interface, and what was run on it. The description and the intent are
    left out -- they are wording, and rewording a scenario must not read as the
    fault having moved.
    """
    material = {
        "bindings": _plain(bindings),
        "commands": _plain(fault.get("commands")),
        "restore_commands": _plain(fault.get("restore_commands")),
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def reserve(document: Mapping[str, Any], kind: str) -> int:
    """A seed no entry of either kind holds yet.

    Unique across both registers rather than within one: a seed is quoted on its
    own, and a number that is an episode here and a campaign there would need
    to be said which.
    """
    taken = set(document.get("episodes", {})) | set(document.get("campaigns", {}))
    generator = random.SystemRandom()
    for _ in range(1000):
        candidate = generator.randint(*SEED_RANGE)
        if str(candidate) not in taken:
            return candidate
    raise RuntimeError("no free seed found; the register may be exhausted")


def record_episode(
    seed: int,
    *,
    experiment: str,
    scenario_id: str,
    domain: str,
    topology: str,
    testbed: str | None,
    no_fault: bool,
    bindings: Mapping[str, Any],
    fault: Mapping[str, Any],
    seed_campaign: int | None = None,
    run: str | None = None,
) -> dict[str, Any]:
    """Write down what this seed stands for, or note that it ran again.

    An entry is never rewritten: a seed means one context, and a second run of
    it adds itself to `runs` rather than replacing what the first recorded. A
    context that no longer matches is the caller's to refuse, not this module's
    to overwrite.
    """
    document = load()
    key = str(seed)
    existing = document["episodes"].get(key)
    if existing is None:
        existing = {
            "seed": seed,
            "recorded_at": _now(),
            "experiment": experiment,
            "scenario_id": scenario_id,
            "domain": domain,
            "topology": _relative(topology),
            "testbed": testbed,
            "no_fault": no_fault,
            "bindings": _plain(bindings),
            "fault": {
                "id": fault.get("id"),
                "commands": _plain(fault.get("commands")),
                "restore_commands": _plain(fault.get("restore_commands")),
                "expected_affected_nodes": _plain(fault.get("expected_affected_nodes")),
            },
            "fingerprint": fingerprint(bindings, fault),
            "seed_campaign": seed_campaign,
            "runs": [],
        }
        document["episodes"][key] = existing
    if run and run not in existing["runs"]:
        existing["runs"].append(run)
    save(document)
    return existing


def record_campaign(
    seed: int,
    *,
    campaign_id: str,
    request: Mapping[str, Any],
    episode_seeds: list[int],
    result_dir: str,
) -> dict[str, Any]:
    """Write down a whole matrix: what it was, and the episode seed of each cell."""
    document = load()
    key = str(seed)
    entry = document["campaigns"].get(key)
    if entry is None:
        entry = {
            "seed": seed,
            "recorded_at": _now(),
            "campaign_id": campaign_id,
            "result_dir": result_dir,
            "request": _plain(request),
            "episode_seeds": list(episode_seeds),
            "runs": [],
        }
        document["campaigns"][key] = entry
    entry.setdefault("runs", [])
    if campaign_id not in entry["runs"]:
        entry["runs"].append(campaign_id)
    save(document)
    return entry


def summaries() -> dict[str, list[dict[str, Any]]]:
    """The register as a page offers it, newest first.

    Only what a menu needs to label an entry and fill a form from it. The fault
    commands and the fingerprint stay out: they are what a replay is checked
    against, not what it is chosen by, and they would be the bulk of the page.
    """
    document = load()
    episodes = [
        {
            "seed": entry["seed"],
            "experiment": entry.get("experiment"),
            "scenario_id": entry.get("scenario_id"),
            "no_fault": entry.get("no_fault", False),
            "recorded_at": entry.get("recorded_at"),
            "seed_campaign": entry.get("seed_campaign"),
            "runs": len(entry.get("runs") or []),
            "instance": _instance_label(entry.get("bindings") or {}),
        }
        for entry in document["episodes"].values()
        # A seed held while a matrix is being drawn has no context yet; it is
        # not something anyone can pick until the run has written it down.
        if entry.get("scenario_id")
    ]
    campaigns = [
        {
            "seed": entry["seed"],
            "campaign_id": entry.get("campaign_id"),
            "recorded_at": entry.get("recorded_at"),
            "result_dir": entry.get("result_dir"),
            "episode_seeds": entry.get("episode_seeds") or [],
            "runs": len(entry.get("runs") or []),
            "request": entry.get("request") or {},
        }
        for entry in document["campaigns"].values()
    ]
    newest = lambda entry: entry.get("recorded_at") or ""  # noqa: E731
    return {
        "episodes": sorted(episodes, key=newest, reverse=True),
        "campaigns": sorted(campaigns, key=newest, reverse=True),
    }


def _instance_label(bindings: Mapping[str, Any]) -> str:
    """The fault in a few words, so a menu entry says what it will run."""
    named = [str(bindings[field]) for field in ("target", "interface", "destination")
             if bindings.get(field)]
    return " ".join(named)


def lookup(seed: int) -> tuple[str, dict[str, Any]] | None:
    """The entry this seed names, and which register it came from."""
    document = load()
    key = str(seed)
    for kind in ("episodes", "campaigns"):
        entry = document[kind].get(key)
        if entry is not None:
            return kind[:-1], entry
    return None


def differences(entry: Mapping[str, Any], bindings: Mapping[str, Any],
                fault: Mapping[str, Any]) -> list[str]:
    """What moved between the recorded instance and the one just recompiled.

    Empty when the seed still resolves to what it was written down as. A
    non-empty list means the catalogue changed under the seed: the episode would
    run a different fault under the same name, which is the one thing a register
    exists to prevent.
    """
    if entry.get("fingerprint") == fingerprint(bindings, fault):
        return []
    found: list[str] = []
    recorded = entry.get("bindings") or {}
    current = _plain(bindings)
    for field in sorted(set(recorded) | set(current)):
        if recorded.get(field) != current.get(field):
            found.append(f"{field}: recorded {recorded.get(field)!r}, "
                         f"now {current.get(field)!r}")
    recorded_commands = (entry.get("fault") or {}).get("commands")
    current_commands = _plain(fault.get("commands"))
    if recorded_commands != current_commands:
        found.append(f"fault commands: recorded {recorded_commands!r}, "
                     f"now {current_commands!r}")
    # The fingerprint covers restore commands too, so a difference can be real
    # without either list above showing it.
    return found or ["the resolved instance no longer matches its fingerprint"]


def _relative(value: str) -> str:
    try:
        return str(Path(value).relative_to(REPOSITORY))
    except ValueError:
        return str(value)
