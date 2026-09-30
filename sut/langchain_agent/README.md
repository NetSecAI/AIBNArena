# LangChain ANI baseline

This framework baseline uses `langchain.agents.create_agent`, `@tool` wrappers
in `ani_tools.py`, and a `ToolStrategy` structured terminal result. The
underlying `ContainerLabANI` contains no LangChain imports.

ANI reads support node/path scoping, filtering and deterministic pagination.
Oversized responses are explicitly marked as truncated and stored as
content-addressed JSON artifacts under `reports/manual/results/artifacts`.

```bash
./sut/langchain_agent/a2a_server.py \
  --model openai/gpt-4o-mini \
  --scenario-topology scenarios/topologies/sme_leaf_spine_dmz_small.yaml \
  --healthy-state benchmarks/testbeds/containerlab/sme01-small/states/healthy.json
```

Pass `--sut-url http://127.0.0.1:8003` to a benchmark run. To execute the complete reproducible baseline campaign, use `experiments/run_langchain_baseline.sh` from the repository root.
