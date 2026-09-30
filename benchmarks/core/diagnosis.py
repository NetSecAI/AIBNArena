"""Evaluation parameter 13, "LLM found the problem rate": did the subject identify
the fault the scenario injected?

Hugo's sheet defines the parameter as an intermediate step where the LLM explains
the issue, compared with the methods of the scenario. The subject's half of that
step is the `diagnosis` its final response carries, or the one the shared loop asks
for when the episode ends without a conclusion (sut/common/self_execute.py). This
module is the judge's half: the injected fault put into words from the compiled
method and its bindings, and the comparison of the two statements.

The comparison is ParaPLUIE (Lemesle et al., COLING 2025), under the task-specific
prompting of *-PLUIE (Lemesle et al., 2026, arXiv:2602.15778). A language model is
asked a Yes/No question about the two texts and, instead of generating the answer,
its confidence in each answer is read off the next-token distribution:

    score = log p("Yes" | prompt) - log p("No" | prompt)

Positive means the two texts name the same fault, negative that they do not, and
zero is the natural threshold the papers find close to the calibrated one. Nothing
is generated and nothing parsed, so a judged episode costs the judge a forward pass
per answer, and the size of the score says how sure it was. *-PLUIE keeps the score
and rewrites the question for the task; `FAULT_PLUIE` below is that rewrite for
"do these two descriptions name the same network fault", with the few-shot examples
the papers found necessary, written on a network that is not one of ours.

The two log-probabilities are read in one of two ways, chosen by configuration and
named in every record. `prompt_logprobs` is the papers' computation on a vLLM
endpoint: the assistant turn is continued with each answer and the prompt's own
per-token log-probabilities are asked for. `top_logprobs` is the OpenAI API's way
(gpt-4.1, gpt-4o): one token is generated and the endpoint lists the most likely
candidates for that position with their log-probabilities, from which Yes and No
are read; the same numbers as the exact reading whenever both are listed, and a
bound for one that is not, which the record says. Every scoring call is also
appended to an audit log when one is configured, with the raw candidates.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx

from .contracts import ScenarioDefinition

#: Bumped when the question or its examples change: a score is only comparable to
#: another under the same prompt, so every block names the prompt it was read under.
PROMPT_ID = "fault-pluie-v2"

YES = "Yes"
NO = "No"

_INSTRUCTION = (
    "You will receive two descriptions, A and B, of a fault in a computer network. "
    "A is the fault that was actually introduced. B is what an engineer reported "
    "after troubleshooting: the fault they identified, or the repair they made, which "
    "implies the fault it corrected. Do A and B identify the same fault, that is the "
    "same device and the same broken configuration? Judge the device and the cause, "
    "not the wording: B may add symptoms, mention other changes, or leave out values. "
    "Answer with only one word, Yes or No."
)

#: Eight examples on a network that is not one of ours (r1, r2, r3, srv1, pc1, pc2,
#: dhcp1), alternating Yes and No. Each No is a different way of being wrong: another
#: device, the right device with another cause, nothing found, a repair of another
#: object on the right device. Two of the Yes cases are phrased as the repair, because
#: that is how a subject's conclusion usually reads (v2, 2026-09-24: under v1 the
#: judge read "added the default route on web1" as not naming web1's missing default
#: route).
_EXAMPLES: tuple[tuple[str, str, str], ...] = (
    ("On r1, interface eth2 lost its IPv4 address: 192.168.20.1/24 was removed, so "
     "the interface has no address on the LAN-B segment.",
     "The gateway r1 has no IP address configured on the interface facing the "
     "192.168.20.0/24 LAN, so the hosts there cannot reach their gateway.",
     YES),
    ("On r1, interface eth2 lost its IPv4 address: 192.168.20.1/24 was removed, so "
     "the interface has no address on the LAN-B segment.",
     "pc3's default route points to 192.168.20.254, a gateway that does not exist.",
     NO),
    ("On srv1, the default route points to 10.0.5.254, a gateway address that does "
     "not exist on the servers segment; it should point to 10.0.5.1.",
     "srv1 has a wrong default gateway, 10.0.5.254 instead of the router at 10.0.5.1, "
     "so its traffic to other subnets is never forwarded.",
     YES),
    ("On r2, a traffic filter drops all traffic to subnet 172.16.30.0/24 (LAN-C, "
     "interface eth1).",
     "r2's interface eth1 toward 172.16.30.0/24 is administratively down.",
     NO),
    ("On pc1, interface eth0 is configured with 10.1.1.5/32: the prefix length is "
     "wrong, it should be 10.1.1.5/24.",
     "pc1 has a /32 mask on eth0, so it treats every other host as off-link and "
     "cannot reach anything on its own LAN.",
     YES),
    ("On the DHCP server dhcp1, pool lan-users (10.2.0.0/24) no longer advertises a "
     "DNS resolver (10.2.0.53), so clients cannot resolve names.",
     "Connectivity is fine; I could not find any misconfiguration and made no change.",
     NO),
    ("On pc2, interface eth0 (LAN-A segment) is administratively down (shut down).",
     "Brought pc2's eth0 back up and added a default route via 10.1.0.1; pings from "
     "pc2 to the gateway and to srv1 succeed again.",
     YES),
    ("On r3, the static route to 10.20.0.0/16 (toward srv2 in servers) points to the "
     "wrong next hop 10.0.0.9, an address on the wrong egress.",
     "Enabled IPv4 forwarding on r3, which had been switched off; traffic to the "
     "servers passes again.",
     NO),
)


def _pair(reference: str, hypothesis: str) -> str:
    return f'A: "{_quoted(reference)}"; B: "{_quoted(hypothesis)}"'


def _quoted(text: str) -> str:
    """The text inside the double quotes of the template, which it must not close."""
    return " ".join(str(text).replace('"', "'").split())


def prompt_messages(reference: str, hypothesis: str) -> list[dict[str, str]]:
    """The *-PLUIE style conversation: the question, the examples, then the data."""
    messages = [
        {"role": "user", "content": _INSTRUCTION},
        {"role": "assistant", "content": "Please provide the two descriptions for me to evaluate."},
    ]
    for example_reference, example_hypothesis, answer in _EXAMPLES:
        messages.append({"role": "user", "content": _pair(example_reference, example_hypothesis)})
        messages.append({"role": "assistant", "content": answer})
    messages.append({"role": "user", "content": _pair(reference, hypothesis)})
    return messages


def prompt_sha256() -> str:
    """Identity of the template, so two scores can be known to share their question."""
    template = prompt_messages("{reference}", "{hypothesis}")
    return hashlib.sha256(json.dumps(template, sort_keys=True).encode("utf-8")).hexdigest()


# --- the injected fault, in words ---------------------------------------------------

@dataclass(frozen=True)
class FaultReference:
    text: str
    method: str | None
    operation: str | None
    target: str | None


def describe_fault(method: Mapping[str, Any] | None, bindings: Mapping[str, Any]) -> str:
    """The injected fault as a sentence an engineer would write, from the compiler's
    method and the bindings it drew.

    One clause per operation, naming the device, the object and what is wrong with
    it, and the healthy value where the bindings carry one. An operation this table
    does not know is still described, from its name and parameters, rather than
    refused: a plate must never go unjudged for want of a phrasing.
    """
    b = dict(bindings or {})
    operation = dict((method or {}).get("operation") or {})
    name = str(operation.get("name") or "")
    target = str(b.get("target") or "the device")
    interface = b.get("interface")
    segment = b.get("segment")
    where = f"interface {interface}" if interface else "an interface"
    on_segment = f" ({segment} segment)" if segment else ""

    if name == "set_interface_ipv4_presence":
        return (f"On {target}, {where} lost its IPv4 address: {b.get('healthy_ipv4')} was "
                f"removed, so the interface has no address on the {segment or 'attached'} segment.")
    if name == "set_interface_ipv4_address":
        source = str(operation.get("value_source") or "")
        fault_ipv4, healthy_ipv4 = b.get("fault_ipv4"), b.get("healthy_ipv4")
        if source == "wrong_prefix":
            return (f"On {target}, {where} is configured with {fault_ipv4}: the prefix length "
                    f"is wrong, it should be {healthy_ipv4}.")
        if source == "duplicate_other_subnet":
            return (f"On {target}, {where} is configured with {fault_ipv4}, which duplicates "
                    f"the address of {b.get('duplicate_source')} in another subnet; it should "
                    f"be {healthy_ipv4}.")
        return (f"On {target}, {where} is configured with {fault_ipv4}, an address in the "
                f"wrong subnet; it should be {healthy_ipv4}.")
    if name == "set_interface_admin_state":
        return f"On {target}, {where}{on_segment} is administratively down (shut down)."
    if name == "set_interface_mtu":
        return (f"On {target}, {where} has its MTU lowered to {operation.get('mtu')} bytes, far "
                f"below the normal value, so normal-sized packets cannot pass.")
    if name == "set_ipv4_forwarding":
        return f"On {target}, IPv4 forwarding (routing between its interfaces) is disabled."
    if name == "set_default_route_presence":
        return f"On {target}, the default route (0.0.0.0/0) was removed, so it has no route to other networks."
    if name == "set_default_route_gateway":
        return (f"On {target}, the default route points to {b.get('fault_gateway')}, a gateway "
                f"address that does not exist on the {segment or 'attached'} segment; it should "
                f"point to {b.get('healthy_gateway')}.")
    if name == "set_nonlocal_route_lookup":
        return (f"On {target}, the default route was replaced by a prohibit (discard) route: "
                f"every lookup for a non-local destination is refused, so it cannot reach "
                f"other subnets.")
    if name == "set_static_route_next_hop":
        route = f"the static route to {b.get('route_prefix')} (toward {b.get('destination')} in {b.get('destination_segment')})"
        if str(operation.get("value_source") or "") == "unresolvable_address":
            return (f"On {target}, {route} points to next hop {b.get('fault_next_hop')}, which "
                    f"cannot be resolved (no such neighbour), so the route is unusable.")
        return (f"On {target}, {route} points to the wrong next hop {b.get('fault_next_hop')}, "
                f"an address on the wrong egress instead of the path via {b.get('destination_gateway')}.")
    if name == "set_static_route_disposition":
        return (f"On {target}, a more specific static route to {b.get('route_prefix')} "
                f"({b.get('destination')}) is configured as a blackhole (discard), so traffic "
                f"to {b.get('destination')} is dropped.")
    if name == "create_static_route_loop":
        return (f"On {target} and {b.get('loop_peer')}, the static routes to {b.get('route_prefix')} "
                f"({b.get('destination')}) point at each other, forming a routing loop.")
    if name == "set_subnet_traffic_policy":
        direction = {"to": "to", "from": "from", "bidirectional": "to and from"}.get(
            str(operation.get("direction") or ""), str(operation.get("direction") or "to and from"))
        protocol = operation.get("protocol")
        what = f"{str(protocol).upper()} traffic" if protocol else "all traffic"
        return (f"On {target}, a traffic filter drops {what} {direction} subnet "
                f"{b.get('subnet_prefix')}{on_segment}, applied on {where}.")
    if name == "set_forwarded_traffic_policy":
        interfaces = ", ".join(str(item) for item in (b.get("interfaces") or []))
        segments = ", ".join(str(item) for item in (b.get("segments") or []))
        return (f"On {target}, the forwarded-traffic policy drops all transit traffic between its "
                f"interfaces {interfaces} ({segments}).")
    if name == "set_zone_interface_membership":
        return (f"On the firewall {target}, interface {interface} was removed from zone "
                f"{b.get('zone')}, so traffic from {b.get('from_zone')} to {b.get('to_zone')} is no "
                f"longer matched by ruleset {b.get('ruleset')} and is dropped.")
    if name == "set_zone_pair_binding":
        return (f"On the firewall {target}, ruleset {b.get('ruleset')} is detached from the zone "
                f"pair {b.get('from_zone')} to {b.get('to_zone')}, so that traffic falls to the "
                f"default deny.")
    if name == "set_zone_rule_precedence":
        return (f"On the firewall {target}, a deny rule was placed ahead of the allow rules in "
                f"ruleset {b.get('ruleset')} ({b.get('from_zone')} to {b.get('to_zone')}), "
                f"shadowing them so the traffic is dropped.")
    if name == "set_dhcp_pool_presence":
        return (f"On the DHCP server {target}, the pool {b.get('pool')} for subnet {b.get('subnet')} "
                f"was removed, so clients on that subnet ({b.get('client')}) get no lease.")
    if name == "set_dhcp_default_router":
        return (f"On the DHCP server {target}, pool {b.get('pool')} ({b.get('subnet')}) hands out "
                f"{b.get('fault_gateway')} as the default router instead of {b.get('healthy_gateway')}.")
    if name == "set_dhcp_resolver":
        return (f"On the DHCP server {target}, pool {b.get('pool')} ({b.get('subnet')}) hands out "
                f"{b.get('fault_resolver')} as the DNS resolver instead of {b.get('healthy_resolver')}.")
    if name == "set_dhcp_resolver_presence":
        return (f"On the DHCP server {target}, pool {b.get('pool')} ({b.get('subnet')}) no longer "
                f"advertises a DNS resolver ({b.get('healthy_resolver')}), so clients cannot resolve names.")
    if name == "set_link_netem":
        path = f"egress traffic on {where} toward {b.get('destination')} ({b.get('destination_ip')})"
        if str(b.get("impairment") or operation.get("impairment") or "") == "corruption":
            return (f"On {target}, {path} has {b.get('corruption_percent')}% of its packets "
                    f"corrupted by a traffic-control (netem) queueing discipline.")
        return (f"On {target}, {path} is delayed by {b.get('delay_ms')} ms by a traffic-control "
                f"(netem) queueing discipline.")
    if name == "set_traffic_shaping_policy":
        policy = str(operation.get("policy") or b.get("shaping_policy") or "")
        flow = (f"the assured class {b.get('assured_class')} ({b.get('assured_bandwidth_mbps')} Mbit/s "
                f"for {b.get('protected_source')} to {b.get('destination')})")
        edge = f"On the WAN edge {target}, on {b.get('shaped_interface') or interface},"
        if policy == "starve_assured_class":
            return (f"{edge} {flow} is starved: its rate was cut to {b.get('starved_kbit')} kbit/s "
                    f"instead of {b.get('assured_bandwidth_mbps')} Mbit/s.")
        if policy == "remove_classifier":
            return (f"{edge} the classifier that maps {b.get('protected_source')} to "
                    f"{b.get('destination')} traffic into {flow} was removed, so that traffic "
                    f"falls into the default class.")
        if policy == "invert_class_allocation":
            return (f"{edge} the bandwidth allocation of the classes is inverted: {flow} got "
                    f"the small share and the background class the large one.")
        return (f"{edge} the traffic shaping policy was removed, so {flow} is no longer protected "
                f"from the background traffic.")
    if name == "start_background_traffic":
        return (f"Sustained UDP cross traffic of {b.get('background_mbps')} Mbit/s from "
                f"{b.get('background_source')} floods the {b.get('link_mbps')} Mbit/s link through "
                f"{b.get('wan_edge')} ({b.get('shaped_interface')}), and nothing on that edge "
                f"protects the assured class {b.get('assured_class')} for {b.get('protected_source')} "
                f"to {b.get('destination')}.")
    # Not in the table: still a description, from the compiler's own terms.
    parameters = ", ".join(f"{key}={value}" for key, value in operation.items() if key != "name")
    detail = ", ".join(f"{key}={value}" for key, value in b.items()
                       if key not in {"target", "affected_nodes"} and not isinstance(value, (list, dict)))
    method_name = str((method or {}).get("name") or name or "unknown")
    return (f"On {target}, fault {method_name}: operation {name or 'unknown'}"
            f"{' (' + parameters + ')' if parameters else ''}"
            f"{'; ' + detail if detail else ''}.")


def reference_for_scenario(scenario: ScenarioDefinition) -> FaultReference | None:
    """What was injected, for the judge, or None where the scenario names no method."""
    method = scenario.method
    if not isinstance(method, Mapping):
        return None
    return reference_for_method(method, scenario.bindings)


def reference_for_method(method: Mapping[str, Any], bindings: Mapping[str, Any]) -> FaultReference:
    operation = method.get("operation") if isinstance(method.get("operation"), Mapping) else {}
    return FaultReference(
        text=describe_fault(method, bindings),
        method=(str(method["name"]) if method.get("name") else None),
        operation=(str(operation.get("name")) if operation.get("name") else None),
        target=(str(bindings.get("target")) if bindings.get("target") else None),
    )


# --- the subject's statement ----------------------------------------------------------

def hypothesis_of(sut_result: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """The subject's statement of the fault and where it came from.

    The `diagnosis` block the loop writes comes first, whether the subject stated it
    in its conclusion or was asked at termination. A record from before the block
    falls back to the conclusion's `summary`, which describes what was observed and
    changed and so often names the cause; the source says which was read, so a rate
    over mixed records can be split by it.
    """
    block = sut_result.get("diagnosis")
    if isinstance(block, Mapping) and _text(block.get("text")):
        return _text(block.get("text")), str(block.get("source") or "diagnosis")
    final = sut_result.get("final_response")
    if isinstance(final, Mapping):
        if _text(final.get("diagnosis")):
            return _text(final.get("diagnosis")), "final_response"
        if _text(final.get("summary")):
            return _text(final.get("summary")), "summary"
    return None, None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# --- the judge ------------------------------------------------------------------------

class JudgeError(RuntimeError):
    """The judge could not produce a score; the reason is the message."""


#: The two ways the answers' log-probabilities are read (module docstring).
READINGS = ("prompt_logprobs", "top_logprobs")


@dataclass(frozen=True)
class JudgeConfig:
    model: str
    api_base: str
    api_key: str | None = None
    timeout_seconds: float = 120.0
    #: `prompt_logprobs` (vLLM, exact) or `top_logprobs` (OpenAI API, from the
    #: candidates of the generated token).
    reading: str = "prompt_logprobs"
    #: How many candidates to ask for under `top_logprobs`; the OpenAI API allows 20.
    top_candidates: int = 20
    #: Where every scoring call is appended as one JSON line, or None for no log.
    audit_log: str | None = None
    #: Fields merged into every request body, for what a gateway needs beyond the
    #: OpenAI shape: on OpenRouter, `{"provider": {"order": ["Novita"],
    #: "allow_fallbacks": false, "require_parameters": true}, "reasoning":
    #: {"enabled": false}}` pins the provider that returns the candidates and keeps
    #: a reasoning model from thinking before the answer token.
    extra_body: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "JudgeConfig":
        reading = str(value.get("reading") or "prompt_logprobs")
        if reading not in READINGS:
            raise ValueError(f"diagnosis judge reading must be one of {READINGS}, not {reading!r}")
        return cls(model=str(value["model"]), api_base=str(value["api_base"]).rstrip("/"),
                   api_key=(str(value["api_key"]) if value.get("api_key") else None),
                   timeout_seconds=float(value.get("timeout_seconds", 120.0)),
                   reading=reading,
                   top_candidates=int(value.get("top_candidates", 20)),
                   audit_log=(str(value["audit_log"]) if value.get("audit_log") else None),
                   extra_body=(dict(value["extra_body"]) if isinstance(value.get("extra_body"), Mapping) else None))


class PLUIEJudge:
    """ParaPLUIE over an OpenAI-compatible chat endpoint, under one of two readings.

    `prompt_logprobs` (vLLM): the assistant turn is continued with each answer and
    the prompt's own log-probabilities are asked for (`continue_final_message` and
    `prompt_logprobs`); the answer's token is the last one of that prompt, found by
    the token count the same conversation has without the answer. `top_logprobs`
    (OpenAI API): one token is generated with `logprobs` and `top_logprobs`, and Yes
    and No are read from the candidates listed for that first position. Either
    reading raises JudgeError when the endpoint cannot serve it, and the episode is
    recorded as not judged.
    """

    def __init__(self, config: JudgeConfig, *, transport: httpx.BaseTransport | None = None):
        self.config = config
        headers = {"content-type": "application/json"}
        if config.api_key:
            headers["authorization"] = f"Bearer {config.api_key}"
        self._client = httpx.Client(base_url=config.api_base, headers=headers,
                                    timeout=config.timeout_seconds, transport=transport)

    def identity(self) -> dict[str, Any]:
        return {"model": self.config.model, "api_base": self.config.api_base,
                "prompt_id": PROMPT_ID, "prompt_sha256": prompt_sha256(),
                "reading": self.config.reading}

    def score(self, reference: str, hypothesis: str) -> dict[str, Any]:
        """log p(Yes) - log p(No) under the configured reading, audited."""
        messages = prompt_messages(reference, hypothesis)
        if self.config.reading == "top_logprobs":
            scored = self._score_from_candidates(messages)
        else:
            scored = self._score_from_prompt_logprobs(messages)
        self._audit(reference, hypothesis, scored)
        return scored

    def _score_from_prompt_logprobs(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        # The conversation without an answer, for its token count: the answer's
        # tokens are the ones the continued prompt has beyond it.
        prefix_tokens = _prompt_tokens(self._chat({"messages": messages, "max_tokens": 1, "temperature": 0}))
        answers = {}
        for answer in (YES, NO):
            response = self._chat({
                "messages": [*messages, {"role": "assistant", "content": answer}],
                "continue_final_message": True, "add_generation_prompt": False,
                "prompt_logprobs": 0, "max_tokens": 1, "temperature": 0,
            })
            answer_tokens = _prompt_tokens(response) - prefix_tokens
            answers[answer] = _answer_logprob(response, answer_tokens, answer)
        return {
            "score": answers[YES]["logprob"] - answers[NO]["logprob"],
            "log_p_yes": answers[YES]["logprob"], "log_p_no": answers[NO]["logprob"],
            "method": "prompt_logprobs", "bound": False,
            "answer_tokens": {YES: answers[YES]["tokens"], NO: answers[NO]["tokens"]},
            "raw": {"prefix_tokens": prefix_tokens,
                    "answers": {answer: answers[answer] for answer in (YES, NO)}},
        }

    def _score_from_candidates(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """The OpenAI API's reading: the candidates listed for the generated token.

        Yes and No are looked up among them (a spelling variant of an answer counts
        for it, at its highest-probability form). An answer that is not listed is
        below every candidate that is, and below the probability mass left over,
        so its log-probability is bounded by the smaller of the two; the score is
        then a bound, and says so.
        """
        # Exactly one token: the answer's position is the only one whose candidates
        # are wanted, and some gateways report only the last generated position, so
        # a second token (the end-of-turn) would hide the answer's list.
        body = {"messages": messages, "max_tokens": 1, "temperature": 0,
                "logprobs": True, "top_logprobs": self.config.top_candidates}
        try:
            response = self._chat(body)
        except JudgeError as refused:
            # OpenAI's newer models take max_completion_tokens and refuse max_tokens.
            if "max_tokens" not in str(refused) or "max_completion_tokens" not in str(refused):
                raise
            body = {**{key: value for key, value in body.items() if key != "max_tokens"},
                    "max_completion_tokens": 1}
            response = self._chat(body)
        try:
            first = response["choices"][0]["logprobs"]["content"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise JudgeError("the endpoint returned no log-probabilities for the generated "
                             "token (the top_logprobs reading needs `logprobs`)") from exc
        candidates = [{"token": str(item.get("token")), "logprob": float(item["logprob"])}
                      for item in (first.get("top_logprobs") or [])]
        if first.get("token") is not None and not any(
                item["token"] == first["token"] for item in candidates):
            candidates.append({"token": str(first["token"]), "logprob": float(first["logprob"])})
        if not candidates:
            raise JudgeError("the endpoint listed no candidate for the generated token")
        listed_mass = sum(math.exp(item["logprob"]) for item in candidates)
        floor = min(math.log(max(1.0 - listed_mass, 1e-12)), min(item["logprob"] for item in candidates))
        values: dict[str, float] = {}
        tokens: dict[str, list[str]] = {}
        bound = False
        for answer in (YES, NO):
            matches = [item for item in candidates if item["token"].strip().lower() == answer.lower()]
            if matches:
                best = max(matches, key=lambda item: item["logprob"])
                values[answer] = best["logprob"]
                tokens[answer] = [best["token"]]
            else:
                values[answer] = floor
                tokens[answer] = []
                bound = True
        if not tokens[YES] and not tokens[NO]:
            raise JudgeError(f"neither Yes nor No is among the {len(candidates)} candidates listed "
                             f"(first token {first.get('token')!r})")
        return {
            "score": values[YES] - values[NO],
            "log_p_yes": values[YES], "log_p_no": values[NO],
            "method": "top_logprobs", "bound": bound,
            "answer_tokens": tokens,
            "raw": {"first_token": first.get("token"), "candidates": candidates,
                    "listed": len(candidates), "listed_mass": round(listed_mass, 6)},
        }

    def _audit(self, reference: str, hypothesis: str, scored: Mapping[str, Any]) -> None:
        """One JSON line per scoring call, with the raw candidates, in the audit log."""
        if not self.config.audit_log:
            return
        line = {
            "at": datetime.now(timezone.utc).isoformat(),
            "judge": self.identity(),
            "reference": reference, "hypothesis": hypothesis,
            "score": scored["score"], "log_p_yes": scored["log_p_yes"], "log_p_no": scored["log_p_no"],
            "bound": scored.get("bound", False), "answer_tokens": scored["answer_tokens"],
            "raw": scored.get("raw"),
        }
        path = Path(self.config.audit_log)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(line, ensure_ascii=False) + "\n")

    def _chat(self, body: Mapping[str, Any]) -> dict[str, Any]:
        payload = {"model": self.config.model, **(self.config.extra_body or {}), **body}
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise JudgeError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code != 200:
            detail = response.text[:300]
            if any(name in detail for name in ("continue_final_message", "prompt_logprobs", "add_generation_prompt")):
                raise JudgeError(
                    "the endpoint does not return the prompt's per-token log-probabilities "
                    f"(vLLM does; the papers' exact reading needs them): HTTP {response.status_code}: {detail}")
            raise JudgeError(f"HTTP {response.status_code}: {detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise JudgeError(f"the endpoint returned no JSON: {exc}") from exc


def _prompt_tokens(response: Mapping[str, Any]) -> int:
    usage = response.get("usage")
    if not isinstance(usage, Mapping) or not isinstance(usage.get("prompt_tokens"), int):
        raise JudgeError("the endpoint reported no prompt token count")
    return int(usage["prompt_tokens"])


def _answer_logprob(response: Mapping[str, Any], answer_tokens: int, answer: str) -> dict[str, Any]:
    entries = response.get("prompt_logprobs")
    if not isinstance(entries, list):
        raise JudgeError("the endpoint returned no prompt_logprobs: it does not return the prompt's "
                         "per-token log-probabilities, which the papers' exact reading needs")
    if answer_tokens <= 0 or answer_tokens > len(entries):
        raise JudgeError(f"the answer {answer!r} spans {answer_tokens} prompt tokens")
    tail: list[tuple[float, str]] = []
    for entry in entries[-answer_tokens:]:
        if not isinstance(entry, Mapping) or len(entry) != 1:
            raise JudgeError("a prompt_logprobs entry did not carry exactly the chosen token")
        (record,) = entry.values()
        tail.append((float(record["logprob"]), str(record.get("decoded_token", ""))))
    # A chat template may close the continued turn anyway (mistral_common appends
    # </s> after the assistant content; measured on this model behind our relay). The
    # closing token comes after the answer, so the answer's own log-probability is
    # untouched; it is only not the last entry. Trailing special tokens are dropped.
    while tail and _is_special_token(tail[-1][1]):
        tail.pop()
    tokens = [token for _, token in tail]
    if "".join(tokens).strip().lower() != answer.lower():
        raise JudgeError(f"the answer's tokens read {tokens!r}, not {answer!r}")
    return {"logprob": sum(logprob for logprob, _ in tail), "tokens": tokens}


def _is_special_token(token: str) -> bool:
    """`</s>`, `<|eot_id|>`, `<|im_end|>` and their kind: a control token, not text."""
    stripped = token.strip()
    return len(stripped) > 2 and stripped.startswith("<") and stripped.endswith(">")


# --- the block the record carries ------------------------------------------------------

def assess_diagnosis(reference: FaultReference | None, sut_result: Mapping[str, Any],
                     judge: Any | None, *, fault_applicable: bool = True) -> dict[str, Any]:
    """`metrics.diagnosis`: what was injected, what the subject said, and whether the
    judge found them to be the same fault.

    `found` is True or False only when the judge scored the pair, or when the
    subject stated nothing (then False: whatever the fault was, it did not say). It
    is None when the question was not asked, and `reason` says why: no fault to
    find, no method to describe, no judge configured, or a judge that failed.
    """
    hypothesis, source = hypothesis_of(sut_result if isinstance(sut_result, Mapping) else {})
    block: dict[str, Any] = {
        "applicable": bool(fault_applicable),
        "reference": reference.text if reference else None,
        "method": reference.method if reference else None,
        "operation": reference.operation if reference else None,
        "target": reference.target if reference else None,
        "hypothesis": hypothesis,
        "hypothesis_source": source,
        "found": None,
        "score": None, "log_p_yes": None, "log_p_no": None,
        "judge": None,
        "reason": None,
    }
    if not fault_applicable:
        block["reason"] = "no fault was injected, so there was nothing to find"
        return block
    if reference is None:
        block["reason"] = "the scenario names no method, so the fault cannot be described"
        return block
    if hypothesis is None:
        block["found"] = False
        block["reason"] = "the subject stated no diagnosis"
        return block
    if judge is None:
        block["reason"] = "no diagnosis judge configured"
        return block
    try:
        block["judge"] = dict(judge.identity())
        scored = judge.score(reference.text, hypothesis)
    except Exception as exc:  # noqa: BLE001 - the reason is recorded, the episode stands
        block["reason"] = f"judge error: {type(exc).__name__}: {str(exc)[:300]}"
        return block
    block["score"] = round(float(scored["score"]), 4)
    block["log_p_yes"] = round(float(scored["log_p_yes"]), 4)
    block["log_p_no"] = round(float(scored["log_p_no"]), 4)
    block["found"] = block["score"] > 0
    block["judge"].update({key: scored[key] for key in ("method", "bound", "answer_tokens") if key in scored})
    if getattr(judge, "config", None) is not None and getattr(judge.config, "audit_log", None):
        block["judge"]["audit_log"] = str(judge.config.audit_log)
    return block


def judge_from_config(config: Mapping[str, Any] | None) -> PLUIEJudge | None:
    """The judge an experiment names, or None when it names none."""
    if not config:
        return None
    return PLUIEJudge(JudgeConfig.from_mapping(config))
