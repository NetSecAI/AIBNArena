#!/usr/bin/env python
"""The evaluation parameters a campaign reports, as rates over its episodes.

`summarize_campaign.py` answers "how did this subject do on this scenario under this
condition" and deliberately does the grouping and nothing else clever. This answers a
different question -- of the episodes that failed, what failed about them -- and every
one of its numbers needs something clever: a bucketing over the subject's own tool
surface, a filter for the reads the SUT issues to itself, an error-versus-verdict
distinction, and denominators that are sometimes genuinely zero.

Everything is derived from the recorded episode. Nothing is added to the per-episode
contract, so every campaign already on disk becomes measurable the moment this lands,
and a definition that turns out to be wrong is fixed by re-running rather than by
another field frozen into two hundred result files.

The four statuses matter more than the numbers. A rate whose denominator does not
exist is not zero, and a benchmark that reports it as zero is worse than one that
reports nothing: `interaction_limit_rate` over a campaign that set no limit, or
`error_submission_rate` over one where the subject never claimed completion, would
both read as "the agent never hit this" when the truth is "we never asked".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for _path in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import summarize_campaign  # noqa: E402

from benchmarks.core.reporting import device_changes_of  # noqa: E402

#: Bumped when a consumer would have to change to keep reading these reports.
PARAMETERS_VERSION = "1.0"
REPORT_KIND = "evaluation_parameters"

#: Device state, as the ANI reads it.
DEVICE_READS = ("get_topology", "get_state", "get_running_config", "get_object")
#: A device changes only through these three.
CONFIG_WRITES = ("update_config", "update_object", "rollback_config")
VALIDATE_OPERATIONS = ("execute_validation",)
#: No search and no wait, sleep or pause exist on the ANI. The two buckets are kept
#: because the parameter list names them, and reported as structurally zero so a
#: reader sees the absence of the capability rather than an agent that never used it.
SEARCH_OPERATIONS: tuple[str, ...] = ()
WAIT_OPERATIONS: tuple[str, ...] = ()

BUCKET_ORDER = ("search", "check_config", "apply_config", "wait", "validate", "unknown")

#: The clock ran out. `completion_rejected_until_budget` is the same wall clock
#: reached by a model that spent its last turns claiming a completion the gate kept
#: refusing (sut/common/self_execute.py).
BUDGET_CAUSES = frozenset({"budget", "completion_rejected_until_budget"})


def action_bucket(operation: str) -> str:
    """Which of the six kinds of action this operation is, by name alone: a refused
    write is still the agent trying to configure the network."""
    if operation in CONFIG_WRITES:
        return "apply_config"
    if operation in VALIDATE_OPERATIONS:
        return "validate"
    if operation in WAIT_OPERATIONS:
        return "wait"
    if operation in DEVICE_READS:
        return "check_config"
    if operation in SEARCH_OPERATIONS:
        return "search"
    return "unknown"


def is_model_action(operation: Mapping[str, Any]) -> bool:
    """Whether the model asked for this, rather than the SUT issuing it to itself.

    A subject tool that performs ANI operations on its own account tags each row it
    files with `source`. Counting those would make the repeat rate a measure of the
    subject's plumbing rather than of the model's choices.
    """
    return "source" not in operation


#: Operations whose `ok` answers a question rather than reporting the call's health.
#: `execute_validation` sets it from whether the criteria passed, so an honest failing
#: measurement carries ok False with no error and is still a call the surface took.
VERDICT_OPERATIONS = frozenset({"execute_validation"})


def call_accepted(operation: Mapping[str, Any]) -> bool:
    """Whether the surface accepted the call, which is not whether it said yes.

    Two shapes, and reading either one alone gets the other backwards.
    `execute_validation` puts its verdict in `ok`, so a failing measurement is an
    accepted call; `update_config` puts a device's refusal in `ok` and leaves `error`
    empty, so a push the device turned down is not one. Take `error` as the answer
    except where `ok` is a verdict.
    """
    if operation.get("error"):
        return False
    if str(operation.get("operation", "")) in VERDICT_OPERATIONS:
        return True
    return operation.get("ok") is not False


def action_key(operation: Mapping[str, Any]) -> str:
    """Identity of a repeated call: the operation and the arguments the model sent."""
    arguments = operation.get("arguments")
    return json.dumps([operation.get("operation"), arguments],
                      sort_keys=True, separators=(",", ":"), default=str)


# --- where the fault was -----------------------------------------------------------

#: Replaying a fault binding needs the scenario compiler. Imported once, here, because
#: an environment that cannot import it cannot answer the question for ANY episode --
#: a different fact from an episode whose own binding will not replay, and one that
#: has to reach the report. Folded into the per-episode failure it reads as a
#: confident zero: a checkout without the compiler's dependencies turns 15/138 into
#: `measured 0.000 0/99` with every other field of the report looking healthy.
try:  # pragma: no cover - exercised by whichever branch the environment allows
    from scenarios.compiler.compiler import ScenarioCompiler as _ScenarioCompiler
    REPLAY_IMPORTED = True
    REPLAY_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - the import is the thing under test
    _ScenarioCompiler = None
    REPLAY_IMPORTED = False
    REPLAY_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

#: Re-binding a scenario costs a compile; the same (topology, seed, scenario) repeats
#: across every episode of a cell.
_FAULT_TARGETS: dict[tuple[str, int, str, str], dict[str, Any] | None] = {}
#: Keyed by root: the cache is a map from digest to a path UNDER that root, and one
#: shared dict served a second root's lookups from the first root's files.
_TOPOLOGY_BY_DIGEST: dict[Path, dict[str, Path]] = {}


def topology_by_digest(root: Path = REPO_ROOT) -> dict[str, Path]:
    """Which descriptor each recorded `topology_sha256` names."""
    cached = _TOPOLOGY_BY_DIGEST.get(root)
    if cached is None:
        cached = {}
        for path in sorted((root / "scenarios" / "topologies").glob("*.yaml")):
            cached[hashlib.sha256(path.read_bytes()).hexdigest()] = path
        _TOPOLOGY_BY_DIGEST[root] = cached
    return cached


def fault_target(document: Mapping[str, Any], root: Path = REPO_ROOT) -> str | None:
    """The device the fault was injected on, or None when it cannot be recovered.

    The result file does not name it. What it does carry is the scenario id, the
    seed, and the digest of the topology the episode ran against, and the compiler
    binds a method's selector to a concrete device deterministically from those
    three (`random.Random(seed)`). So the binding is replayed rather than guessed.

    The digest is the guard. A descriptor edited since the episode ran would bind
    a different device and the mismatch is silent, so a digest this checkout cannot
    produce refuses instead: the parameter says `unavailable` for that episode
    rather than scoring the agent against the wrong device.
    """
    instance = compiled_instance(document, root)
    if instance is None:
        return None
    bindings = (instance.get("private") or {}).get("bindings") or {}
    target = bindings.get("target")
    return str(target) if target else None


def compiled_instance(document: Mapping[str, Any], root: Path = REPO_ROOT) -> dict[str, Any] | None:
    """The compiled scenario instance an episode ran, replayed from its record, or
    None when it cannot be: its `private.bindings` name the device and the values
    drawn, and `private.evaluator.selected_method` the fault that was injected.
    `fault_target` and the offline diagnosis scorer both read it from here."""
    scenario = document.get("scenario")
    provenance = document.get("provenance")
    if not isinstance(scenario, Mapping) or not isinstance(provenance, Mapping):
        return None
    scenario_id = str(scenario.get("id") or "")
    digest = str(provenance.get("topology_sha256") or "")
    seed = provenance.get("scenario_seed")
    if not scenario_id or not digest or not isinstance(seed, int):
        return None
    topology = topology_by_digest(root).get(digest)
    if topology is None:
        return None

    if not REPLAY_IMPORTED:
        return None

    # The digest of every scenario file the replay will read, not just the topology.
    # One `random.Random(seed)` stream feeds every binding in a domain and each
    # earlier-sorted file advances it, so editing any of them moves a later file's
    # answer. Keying the cache on all of them means a checkout whose scenarios have
    # changed recomputes rather than serving a binding from the shape they used to
    # have; it does not prove the episode ran against these files, which is why the
    # replay is only ever trusted to the extent the topology digest matches.
    domain = scenario_id.split(".")[0]
    domain_digest = hashlib.sha256()
    for path in sorted((root / "scenarios" / domain).glob("*.yaml")):
        domain_digest.update(hashlib.sha256(path.read_bytes()).digest())
    key = (str(topology), seed, scenario_id, domain_digest.hexdigest())
    if key in _FAULT_TARGETS:
        return _FAULT_TARGETS[key]

    instance: dict[str, Any] | None = None
    try:
        # The production entry point, not a reimplementation of its loop. It walks the
        # domain in the same order, consults the topology-applicability map, and skips
        # what cannot bind against this topology -- all of which move the one seeded
        # stream every binding is drawn from. Compiling the domain by hand here meant
        # a scenario file this topology never compiles could raise, and the bare
        # `except` below then dropped every episode of the domain out of the parameter
        # instead of the one file that could not bind.
        compiler = _ScenarioCompiler.from_topology_file(topology, seed=seed)
        compiled = compiler.compile_scenarios(root / "scenarios", domains={domain})
        for candidate in compiled.get("tasks") or []:
            if candidate.get("id") == scenario_id:
                instance = candidate
                break
    except Exception:  # noqa: BLE001 - this episode's binding is `unavailable`
        instance = None
    _FAULT_TARGETS[key] = instance
    return instance


def edited_targets(report: Mapping[str, Any]) -> set[str]:
    """Every device the agent asked to change, whether or not the change landed."""
    targets: set[str] = set()
    for item in report.get("ani_operations") or []:
        if not isinstance(item, Mapping):
            continue
        arguments = item.get("arguments") or {}
        if item.get("operation") == "update_config":
            for change in arguments.get("changes") or []:
                if isinstance(change, Mapping) and change.get("target"):
                    targets.add(str(change["target"]))
        elif item.get("operation") == "update_object" and arguments.get("node"):
            # ANI v0.2 writes one named object on one node and carries no changes list,
            # so a device edited through it was invisible to this loop.
            targets.add(str(arguments["node"]))
    for action in device_changes_of(report) or []:
        if not isinstance(action, Mapping):
            continue
        # `machine`, not `target`: an applied action is recorded by the name the
        # command was dispatched to. There is no `target` key here and reading one
        # made this whole loop dead code.
        machine = (action.get("action") or {}).get("machine")
        if machine:
            targets.add(str(machine))
    return targets





# --- one rate ----------------------------------------------------------------------

def measured(numerator: int, denominator: int, definition: str) -> dict[str, Any]:
    return {"status": "measured", "value": (numerator / denominator) if denominator else 0.0,
            "numerator": numerator, "denominator": denominator, "definition": definition}


def no_denominator(reason: str, definition: str) -> dict[str, Any]:
    """The question was never asked of this campaign. Not a zero."""
    return {"status": "no_denominator", "value": None, "numerator": 0, "denominator": 0,
            "reason": reason, "definition": definition}


def structurally_zero(denominator: int, reason: str, definition: str) -> dict[str, Any]:
    """The numerator cannot exist, because the surface has no such operation."""
    return {"status": "structurally_zero", "value": 0.0, "numerator": 0,
            "denominator": denominator, "reason": reason, "definition": definition}


def unavailable(reason: str, definition: str, see: Sequence[str] = ()) -> dict[str, Any]:
    return {"status": "unavailable", "value": None, "numerator": None,
            "denominator": None, "reason": reason, "definition": definition,
            "see": list(see)}


def rate(numerator: int, denominator: int, definition: str, *,
         empty_reason: str) -> dict[str, Any]:
    """A measured rate, or an honest statement that its denominator is empty."""
    if denominator <= 0:
        return no_denominator(empty_reason, definition)
    return measured(numerator, denominator, definition)


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval, so a rate over three episodes does not read like one over sixty."""
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, centre - spread), min(1.0, centre + spread))


# --- per-episode facts -------------------------------------------------------------

@dataclass
class EpisodeFacts:
    """Everything the parameters need from one episode, derived once."""

    scored: bool = False
    repair_passed: bool | None = None
    #: Whether the repair oracle reached a verdict at all. An episode it never judged
    #: is not an episode the subject failed, and belongs in no denominator that asks
    #: what the oracle said.
    repair_judged: bool = False
    actions: int = 0
    accepted: int = 0
    repeats: int = 0
    buckets: Counter = field(default_factory=Counter)
    #: Whether the episode recorded an operation log at all. Its absence is not
    #: evidence about what the subject did; every count derived from it is void.
    has_operation_log: bool = False
    validations: int = 0
    hit_time_limit: bool = False
    capped: bool = False
    hit_interaction_limit: bool = False
    declared_completed: bool = False
    submitted: bool = False
    early_submission: bool = False
    #: None when the fault's device could not be recovered for this episode.
    fault_located: bool | None = None
    #: The narrower reading of the same question: a change to the faulty device went
    #: through. Kept beside the parameter so the stricter number stays readable.
    fault_device_written: bool = False
    edited_any_device: bool = False
    #: False on a no-fault episode -- the healthy lab the false-positive scenarios
    #: run on. None where the record predates the field.
    fault_applicable: bool | None = None
    #: On a no-fault episode: did the subject successfully change a lab that needed
    #: no change. The harness answers this; it is not re-derived here.
    made_false_positive: bool | None = None
    #: Parameter 13 proper: whether the diagnosis judge found the subject's stated
    #: diagnosis to name the injected fault (`metrics.diagnosis.found`). False also
    #: when the subject stated none. None where the judge did not answer: a record
    #: from before the step, no judge configured, a judge that failed, or no fault.
    diagnosis_found: bool | None = None
    #: Where the judged statement came from: `final_response`, `termination_turn`,
    #: or `summary` for a record that predates the step and was scored offline.
    diagnosis_source: str | None = None


def episode_facts(document: Mapping[str, Any]) -> EpisodeFacts:
    facts = EpisodeFacts()
    report = document.get("sut_result")
    evaluations = document.get("evaluations")
    if isinstance(evaluations, Mapping):
        repair = evaluations.get("repair")
        if isinstance(repair, Mapping) and isinstance(repair.get("passed"), bool):
            facts.repair_passed = bool(repair["passed"])
            facts.repair_judged = True
    metrics = document.get("metrics")
    if isinstance(metrics, Mapping):
        applicable = metrics.get("fault_applicable")
        if isinstance(applicable, bool):
            facts.fault_applicable = applicable
        positive = metrics.get("false_positive")
        if isinstance(positive, bool):
            facts.made_false_positive = positive
        diagnosis = metrics.get("diagnosis")
        if isinstance(diagnosis, Mapping) and isinstance(diagnosis.get("found"), bool):
            facts.diagnosis_found = bool(diagnosis["found"])
            source = diagnosis.get("hypothesis_source")
            facts.diagnosis_source = str(source) if source else None
    if not isinstance(report, Mapping) or not report:
        # A judge-side failure: the testbed never came up, so the subject never ran.
        # The record keeps `sut_result` as an empty object rather than dropping the
        # key, and an empty object is not a subject result -- counting it would put
        # thirty zeros into every rate and read as thirty failures by the agent.
        return facts
    facts.scored = True

    operations = report.get("ani_operations")
    facts.has_operation_log = isinstance(operations, Sequence) and not isinstance(
        operations, (str, bytes))
    seen: set[str] = set()
    for item in operations or []:
        if not isinstance(item, Mapping) or not is_model_action(item):
            continue
        facts.actions += 1
        facts.accepted += int(call_accepted(item))
        key = action_key(item)
        if key in seen:
            facts.repeats += 1
        seen.add(key)
        bucket = action_bucket(str(item.get("operation", "")))
        facts.buckets[bucket] += 1
    facts.validations = facts.buckets.get("validate", 0)

    execution = report.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    cause = summarize_campaign.termination_cause(document)
    facts.hit_time_limit = cause in BUDGET_CAUSES

    limits = execution.get("interaction_limits")
    if isinstance(limits, Mapping):
        surfaces = [value for value in limits.values() if isinstance(value, Mapping)]
        facts.capped = any(surface.get("limit") is not None for surface in surfaces)
        facts.hit_interaction_limit = any(
            surface.get("reached") is True for surface in surfaces)

    facts.declared_completed = report.get("status") == "completed"
    # A submission is a terminal verdict the subject stated itself. A run the clock
    # ended never submitted, and must not sit in the denominator of a rate about
    # what submissions are like.
    facts.submitted = cause == "own_conclusion" or facts.declared_completed
    # `has_operation_log`, because the denominator does not come from the log. The
    # cause and the status are read from elsewhere, so a subject whose report carries
    # no `ani_operations` at all kept a full-size denominator while every validation
    # count read zero, and the rate asserted `measured 1.000` -- every submission
    # early -- from a record that says nothing about validation either way.
    facts.early_submission = (facts.submitted and facts.has_operation_log
                              and facts.validations == 0)

    edited = edited_targets(report)
    facts.edited_any_device = bool(edited)
    reached = edited
    if not reached:
        # It asked to change nothing, so it located nothing. A miss, not an episode
        # this cannot measure: whatever the fault was, the agent did not go to it.
        facts.fault_located = False
    else:
        target = fault_target(document)
        # None only where the binding could not be replayed -- our inability to
        # measure, which must not be scored against the agent either way.
        facts.fault_located = (target in reached) if target else None
        facts.fault_device_written = bool(target and target in edited)

    return facts


# --- the parameters ------------------------------------------------------------------

def parameters_for(facts: Sequence[EpisodeFacts]) -> dict[str, Any]:
    scored = [item for item in facts if item.scored]
    episodes = len(scored)
    actions = sum(item.actions for item in scored)
    submissions = sum(1 for item in scored if item.submitted)
    capped = sum(1 for item in scored if item.capped)
    # An episode the repair oracle never judged is not an episode the subject failed.
    # The oracle is assigned after the subject returns, so a judge-side error between
    # the two leaves a full `sut_result` with no repair verdict; counting it kept the
    # episode in every denominator and put it in the failure numerator of three rates.
    judged = sum(1 for item in scored if item.repair_judged)
    submissions_judged = sum(1 for item in scored if item.submitted and item.repair_judged)
    submissions_logged = sum(1 for item in scored if item.submitted and item.has_operation_log)
    # The negative class. `Injecting false positive scenarios` (test parameter A8) is
    # what creates it: the same plate on a healthy lab, where any successful change is
    # the failure. Until such an episode is run there is nothing to be wrong about.
    no_fault_judged = sum(1 for item in scored
                          if item.fault_applicable is False
                          and item.made_false_positive is not None)

    passed = sum(1 for item in scored if item.repair_passed is True)
    pass_rate = rate(
        passed, judged, "episodes whose repair oracle passed, over episodes it judged",
        empty_reason="the repair oracle reached a verdict on no episode of this cell")

    buckets: Counter = Counter()
    for item in scored:
        buckets.update(item.buckets)
    histogram = {}
    for name in BUCKET_ORDER:
        count = buckets.get(name, 0)
        if name == "wait":
            histogram[name] = structurally_zero(
                actions, "no wait, sleep or pause operation exists on the ANI, so the "
                "agent cannot wait and this can never be nonzero",
                "share of the subject's calls that waited")
        elif name == "search":
            histogram[name] = structurally_zero(
                actions, "no search operation exists on the ANI, so this can never "
                "be nonzero", "share of the subject's calls that searched")
        else:
            histogram[name] = rate(
                count, actions, f"share of the subject's calls in the {name} bucket",
                empty_reason="the cell recorded no call by the subject")

    return {
        "pass_rate": pass_rate,
        "tool_call_success_rate": rate(
            sum(item.accepted for item in scored), actions,
            "calls the surface accepted (no error), over calls the subject made; a "
            "validation that honestly failed is an accepted call",
            empty_reason="the cell recorded no call by the subject"),
        "interaction_limit_rate": rate(
            sum(1 for item in scored if item.hit_interaction_limit), capped,
            "episodes that reached an interaction cap, over episodes that ran under one",
            empty_reason="no episode ran under an interaction cap"),
        "time_limit_rate": rate(
            sum(1 for item in scored if item.hit_time_limit), episodes,
            "episodes the wall clock ended",
            empty_reason="no episode of this cell produced a subject result to score"),
        "repeat_action_rate": rate(
            sum(item.repeats for item in scored), actions,
            "calls repeating an earlier operation with identical arguments; a call, not a device change",
            empty_reason="the cell recorded no call by the subject"),
        "early_submission_rate": rate(
            sum(1 for item in scored if item.early_submission), submissions_logged,
            "submissions made without the subject ever running a validation, over "
            "submissions whose episode recorded an operation log",
            # The denominator comes from the termination cause and the status, neither
            # of which is read from the log, so a report carrying no `ani_operations`
            # kept its place here while its validation count read zero -- and the rate
            # asserted every submission early from a record that says nothing at all.
            empty_reason="no submission of this cell recorded an operation log to "
                         "judge whether the subject validated before submitting"),
        "error_submission_rate": rate(
            # Literally that: a submission that turned out to be wrong. Not "claimed
            # completion and was wrong" -- that reading made this the same expression
            # as false_positive_rate, which is a different parameter about a different
            # population (see below). Over submissions the oracle actually judged.
            sum(1 for item in scored
                if item.submitted and item.repair_judged and item.repair_passed is not True),
            submissions_judged,
            "submissions the repair oracle did not pass, over submissions it judged",
            empty_reason="the repair oracle judged no episode this cell's subject "
                         "ended on its own conclusion"),
        "ani_call_type_ratio": histogram,
        # Faults repaired over faults injected. A method binds exactly one fault and
        # every scenario states one method, so the denominator is one and this equals
        # pass_rate by arithmetic rather than for want of an implementation. Partial
        # credit needs a per-fault verdict; the repair oracle answers once for the
        # whole episode and neither it nor its probes carry a fault index, so a
        # scenario stating two faults would score an episode that fixed one of them
        # as zero. Give the oracle that verdict before giving this its own axis --
        # and not the probe count, which is checks and not faults: dhcp m3 and m4
        # break DNS while leaving ICMP intact, so their path probe passes without
        # anything having been repaired.
        "score": dict(pass_rate, same_as="pass_rate"),
        # The two detection errors are about the scenario classes, not about a
        # submission's wording: a false positive needs an episode with nothing wrong
        # to be wrong about, and a false negative needs the subject to state that it
        # found nothing. Read as claims about submissions instead, they collapsed onto
        # each other and onto error_submission_rate, and reported a confident 0.000
        # over a population that cannot contain either error.
        "false_negative_rate": unavailable(
            "no subject states a detection verdict: the record carries whether the "
            "repair passed, never whether the subject reported finding a problem. The "
            "intermediate step that would say so is the one evaluation parameter 13 "
            "is waiting on",
            "fault episodes where the subject reported finding no problem",
            see=["llm_found_problem_rate"]),
        "false_positive_rate": rate(
            # The harness answers this on the episode itself: on a healthy lab a
            # successful change is the false positive, and a refused or failed attempt
            # is not, because nothing reached the device (benchmarks/core/metrics.py).
            sum(1 for item in scored if item.made_false_positive is True),
            no_fault_judged,
            "no-fault episodes where the subject successfully changed a lab that "
            "needed no change",
            empty_reason="no false-positive episode was run: every episode of this "
                         "cell injected a fault, so nothing here could be a false "
                         "alarm. `benchmarks/run.py --no-fault` is what creates the "
                         "population this rate is over"),
        "llm_found_problem_rate": llm_found_problem_rate(scored),
    }


def llm_found_problem_rate(scored: Sequence[EpisodeFacts]) -> dict[str, Any]:
    """Parameter 13: whether the LLM found the problem.

    The excel says how: an intermediate step where the LLM explains the issue,
    compared against the scenario's methods. That step exists now -- the subject's
    `diagnosis`, judged by ParaPLUIE against the injected fault put into words
    (benchmarks/core/diagnosis.py) -- and where the record carries its verdict the
    rate is over those verdicts: found, over fault episodes the judge assessed, an
    episode that stated no diagnosis counting as not found. The proxy the parameter
    stood in with before, which device the agent went to, stays beside it as
    `proxy` so the two readings can be compared, and is still the whole answer for a
    campaign recorded before the step or run without a judge.
    """
    proxy = dict(
        rate(sum(1 for item in scored if item.fault_located is True),
             sum(1 for item in scored if item.fault_located is not None),
             "episodes where the agent went to the device the fault was injected "
             "on -- in the graph or on the device itself -- over scored episodes; "
             "one that changed nothing located nothing and counts against, while "
             "one whose fault binding could not be replayed leaves the denominator "
             "because that is our blindness, not its",
             empty_reason=(
                 "the scenario compiler could not be imported, so no fault "
                 f"binding could be replayed at all ({REPLAY_IMPORT_ERROR})"
                 if not REPLAY_IMPORTED else
                 "no episode had its fault binding recoverable from the recorded "
                 "topology digest and seed")),
        proxy_for="the LLM's own explanation of the fault, compared against the "
                  "scenario's methods",
        # The stricter reading, kept beside it: of the same denominator, the
        # episodes that got a push to the faulty device through rather than only
        # naming it in the graph. The gap between the two is the compiler's
        # refusal rate, which is not what this parameter is about.
        device_write_numerator=sum(1 for item in scored if item.fault_device_written),
        compiler_imported=REPLAY_IMPORTED)
    judged = [item for item in scored if item.diagnosis_found is not None]
    if not judged:
        return proxy
    sources: Counter = Counter(item.diagnosis_source or "none" for item in judged)
    return dict(
        rate(sum(1 for item in judged if item.diagnosis_found is True), len(judged),
             "episodes whose stated diagnosis the judge found to name the injected "
             "fault (ParaPLUIE score above zero), over fault episodes the judge "
             "assessed; an episode that stated no diagnosis is assessed as not "
             "having found it",
             empty_reason="the diagnosis judge assessed no episode of this cell"),
        method="ParaPLUIE judge over the subject's stated diagnosis and the injected "
               "fault in words (benchmarks/core/diagnosis.py)",
        hypothesis_sources=dict(sources),
        unjudged=len(scored) - len(judged),
        proxy=proxy)


# --- assembly ----------------------------------------------------------------------

def source_digest(runs_dir: Path, results: Sequence[Any]) -> str:
    """Identity of the corpus this report read, so a number can be matched back to it."""
    entries = []
    for result in results:
        path = Path(result.path) if getattr(result, "path", None) else None
        if path is None or not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            name = str(path.relative_to(runs_dir))
        except ValueError:
            name = str(path)
        entries.append((name, digest))
    payload = json.dumps(sorted(entries), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_parameters(runs_dir: Path, by: Sequence[str],
                     paths: Sequence[Path] | None = None) -> dict[str, Any]:
    results, broken = summarize_campaign.load_results(runs_dir, paths)
    grouped: dict[tuple[str, ...], list[Any]] = {}
    for result in results:
        grouped.setdefault(summarize_campaign.group_key(result, by), []).append(result)

    cells = []
    for key in sorted(grouped):
        members = grouped[key]
        facts = [episode_facts(member.document) for member in members]
        cell = dict(zip(by, key))
        cell["n"] = len(members)
        cell["scored"] = sum(1 for item in facts if item.scored)
        cell["parameters"] = parameters_for(facts)
        cells.append(cell)

    every = [episode_facts(result.document) for result in results]
    return {
        "parameters_version": PARAMETERS_VERSION,
        "kind": REPORT_KIND,
        "runs_dir": str(runs_dir),
        "by": list(by),
        "source": {
            "files": len(results) + len(broken),
            "scored": sum(1 for item in every if item.scored),
            "broken": len(broken),
            "sha256": source_digest(runs_dir, results),
            # Whether this checkout could replay a fault binding at all. Without it
            # parameter 13 is unanswerable, and the report has to say so rather than
            # let a per-episode failure path average the absence into a rate.
            "compiler_imported": REPLAY_IMPORTED,
            "compiler_import_error": REPLAY_IMPORT_ERROR or None,
        },
        "cells": cells,
        "totals": {"n": len(results), "parameters": parameters_for(every)},
        "broken": broken,
    }


# --- rendering ---------------------------------------------------------------------

ORDER = ("pass_rate", "tool_call_success_rate", "interaction_limit_rate",
         "time_limit_rate", "repeat_action_rate", "early_submission_rate",
         "error_submission_rate", "ani_call_type_ratio", "score",
         "false_negative_rate",
         "false_positive_rate", "llm_found_problem_rate")


def format_rate(entry: Mapping[str, Any]) -> str:
    status = entry.get("status")
    if status == "measured":
        return (f"{entry['value']:.3f}  {entry['numerator']}/{entry['denominator']}")
    if status == "structurally_zero":
        return f"0.000* 0/{entry['denominator']}"
    if status == "no_denominator":
        return "  -/0"
    return "  n/a"


def render_text(report: Mapping[str, Any]) -> str:
    lines: list[str] = []
    source = report["source"]
    lines.append(f"runs_dir : {report['runs_dir']}")
    lines.append(f"source   : {source['files']} files, {source['scored']} scored, "
                 f"{source['broken']} broken  sha256 {source['sha256'][:12]}")
    lines.append(f"by       : {', '.join(report['by'])}")
    for cell in report["cells"]:
        name = " | ".join(str(cell[field]) for field in report["by"])
        lines.append("")
        lines.append(f"== {name}   n={cell['n']} scored={cell['scored']}")
        for key in ORDER:
            entry = cell["parameters"][key]
            if key == "ani_call_type_ratio":
                parts = []
                for bucket in BUCKET_ORDER:
                    item = entry[bucket]
                    star = "*" if item["status"] == "structurally_zero" else ""
                    value = "n/a" if item["value"] is None else f"{item['value']:.3f}"
                    parts.append(f"{bucket} {value}{star}")
                lines.append(f"   {key:32s} {'  '.join(parts)}")
                continue
            text = format_rate(entry)
            if entry.get("status") == "measured" and entry["denominator"]:
                low, high = wilson_interval(entry["numerator"], entry["denominator"])
                text += f"   [{low:.3f}, {high:.3f}]"
            # A number that is not its own measurement says so on its own line, so a
            # reader counting distinct results is not misled by the duplicates.
            if entry.get("same_as"):
                text += f"   (= {entry['same_as']})"
            if entry.get("proxy_for"):
                text += "   (proxy)"
            lines.append(f"   {key:32s} {text}")
    lines.append("")
    lines.append("*  structurally zero: the surface has no such operation")
    lines.append("-/0  no denominator: this campaign never asked the question")
    lines.append("n/a  unavailable: the record carries no signal for it")
    lines.append("(proxy)  stands in for a parameter the record cannot yet answer")
    if report["broken"]:
        lines.append("")
        for item in report["broken"]:
            lines.append(f"broken   {item['path']}: {item['error']}")
    return "\n".join(lines) + "\n"


def write_report(report: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent))
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False, default=str)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    Path(temporary).replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs_dir")
    parser.add_argument("--by", default="subject,condition",
                        help="cell fields, comma separated (default: subject,condition; "
                             "the full cell key leaves about three episodes per cell)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default=None, help="write the JSON report to this path")
    args = parser.parse_args(argv)
    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_dir():
        # Otherwise a mistyped or moved campaign path reports "0 files, 0 scored" and
        # exits 0, and nothing distinguishes a wrong path from an empty campaign.
        parser.error(f"{runs_dir} is not a directory")
    report = build_parameters(runs_dir, summarize_campaign.parse_by(args.by))
    if args.out:
        write_report(report, Path(args.out))
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_text(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
