# Scenario generator

`combine_scenarios.py` adds **combined scenarios** to the catalog: the faults of 2 to 4
existing scenarios, injected together in one task. It writes
`scenarios/combined/<n>_scenarios/<a>__<b>[__<c>[__<d>]].yaml` -- one folder per number of
combined scenarios (`2_scenarios`, `3_scenarios`, `4_scenarios`) -- and one entry per file in
`scenarios/topology_applicability.json`.

```bash
python -m scenarios.generator.combine_scenarios                     # 5 combinations of 2, 3 and 4 faults
python -m scenarios.generator.combine_scenarios --dry-run           # print the plan only
python -m scenarios.generator.combine_scenarios --sizes 2 --per-size all --topology campus_small
python -m scenarios.generator.combine_scenarios --combination disable_interface,vlan_misconfiguration --methods 4
python -m scenarios.generator.combine_scenarios --clean             # also drop combinations this run did not write
```

The compiled tasks are `combined.<name>[.<low|high>].m<k>`, run like any other plate
(`benchmarks/configs/experiments/campus_small-combined.toml`).

## What a combined scenario is

The file names its components (catalog scenarios, pinned at their version) and a few
method tuples: one method id per component. Nothing else is written down; at compile time
(`ScenarioCompiler._compile_combination`):

- **Binding.** Each component is bound exactly as its own family binds it, on a stream
  of its own (seed, combination, tuple, sample, attempt). Combined tasks therefore move
  no other scenario's draw, and bind the same compiled alone or in a whole suite. A draw
  where two components cut a common endpoint is discarded and redrawn: the blast radii
  are **disjoint**, so each fault is observable on its own endpoints and one cannot mask
  another.
- **Task.** Each wording (low, medium, high) is composed from the components' own,
  rendered with each component's draw. Wordings that differ only by their endpoint lists
  are merged, and repeated sentences are said once. The number of faults is not
  announced.
- **Fault.** Every component renders as it would alone. The restore runs the
  components in reverse order.
- **Oracles** (`scenarios/oracles/combined/`). Each composed oracle takes, for each
  component, that component's own oracle resolved with that component's bindings, and
  keeps it as one group with its own success rule. The phase passes when every group
  does, so every fault must show at degradation and every fault must be repaired.
  Preservation is resolved with the union of the blast radii: a path another fault cut
  belongs to the repair oracle, not to a regression. Probes that are identical after
  resolution are measured once.

## What can be combined

A component must inject a fault (a method) and be judged on reachability alone
(`combination_blocker` in `scenarios/compiler/loader.py`). The run lists what it leaves
out and why:

- **security**: bind by strategy and inject nothing.
- **qos/wan_shaping_policy_repair**: judged on a rate measured under contention between
  two named hosts across one circuit. A second fault on that path voids the measurement
  instead of adding a fault, and its public criterion carries the measured flow, which a
  combined task could state only once.

## What the generator checks

A combination is declared on a topology only if every component applies there and each
method tuple kept compiles with disjoint blast radii **and** materializes for every seed
`0..--check-seeds-1` (10 by default). A topology on which none of the tuples tried
(`--max-tuples`) can be kept is dropped and recorded under `excluded` with the first
refusal. Choices are seeded (`--seed`): the same arguments write the same files.
Rewriting a file with different content moves it to the next minor version.

If a component's version changes, every combination that pins it refuses to compile
until it is regenerated.

## Withdrawn tuples

A method tuple that a later check refuses (a run on the lab, say) is moved from `methods` to
`combination.withdrawn`, with the reason, and the file takes the next minor version. The
generator never proposes a withdrawn tuple again, and never gives its id to another tuple:
a tuple the file already lists keeps its id when the file is regenerated, and a new one takes
an id the file never used, so a task id always names the same faults. Regenerating may fill
the slot with another tuple, checked like any other.

Withdrawn on 2026-11-14 after the campus_small lab check (seed 9): the four tuples whose
`zone_interface_unassigned` drew eth4 (sftp-dmz). An unzoned port still lets through the
connections the desks open toward it, and every required path to sftp-dmz is one of those,
so that fault degrades nothing there.

## Known limits

- Disjoint blast radii are disjoint *endpoints*. Two faults can still meet on a service
  the endpoints consume: a resolver taken down by one component makes a DHCP resolver
  component's name check fail as well. The degradation is real, but it is not that
  component's alone.
- A tuple is checked for sample 1 of each seed. A suite compiled with
  `samples_per_definition > 1` draws further samples that were not checked.
- Two components on the same device go in as consecutive commands, which the injector
  batches into one commit for VyOS and EOS. A refusal of one refuses both.
