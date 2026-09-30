# Evaluation

Evaluation records, laid out as `<model>/<campaign>/<subject>/<intent>/` with `records/`, `logs/` and `reports/` in each
cell; `reports/tasks/<plate>/` holds one folder per task (`report.html` in the shared per-task format of
`scripts/generate_report.py`, `summary.md`, and the report cut into small JSON files). `logs/devices/<plate>/` carries every
device's running configuration at healthy, before the subject and after it, with the diffs of what the fault and the subject
changed.

| model | campaign |
|---|---|
| `gpt-5.4` | `netrepairarena-gpt54/`, the two subjects on the 35 scenarios of the AIxNET 2026 demo (Table I) |
| `qwen3.8-27b` | `netrepairarena-qwen38-27b/`, the same campaign with Qwen3.8-27B (Table I) |

Both campaigns use the same 35 scenarios and the same seeds (`seed_campaign` 151103432), a 400 s execution budget per
episode, the ParaPLUIE judge on gpt-4.1 for the root cause, and the two LangChain subjects of `sut/`: `langchain_agent`
(baseline) and `langchain_rag_agent` (RAG). The RAG agent receives the four best excerpts of the guide
`sut/langchain_rag_agent/rag_context/network_troubleshooting_guide_rag.md` at the start of each task and searches the vendor
documentation in `sut/langchain_rag_agent/rag_document/` through its search tool. A cell that ended by the stop rule or
without a conclusion was run once more with the same seed and the later record replaced the earlier one. Qwen3.8-27B ran
through OpenRouter on DeepInfra (bf16, reasoning disabled, `max_tokens` 8000). The RAG parameters and the corpus digests of
every episode are in its record under `provenance.model_parameters`.

Built with `scripts/export_results.py --out reports/campaigns` from the campaign directories the web interface writes under
`runs/<campaign-id>/` (`--campaign`). The derived reports can be regenerated from the records with
`scripts/summarize_campaign.py` and `scripts/evaluation_parameters.py`.
