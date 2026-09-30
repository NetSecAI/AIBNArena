# gpt-5.4 / netrepairarena-gpt54 / langchain_rag / high

Per task: `reports/tasks/<plate>/` holds `report.html` (the shared per-task report format), `summary.md` and the report cut into `summary.json`, `timeline.json`, `tool_calls.json`, `evaluation_parameters.json`; `logs/traces/<plate>/` holds the trace split into the task, the prompts, the model messages and the operations.

| plate | repair | termination | model calls | report | record | trace | judge log | devices |
|---|---|---|---|---|---|---|---|---|
| qos.assured_bandwidth.m1 | False | own_conclusion | 24 | [html](reports/tasks/qos.assured_bandwidth.m1/report.html) [md](reports/tasks/qos.assured_bandwidth.m1/summary.md) [json](reports/tasks/qos.assured_bandwidth.m1/summary.json) | [qos.assured_bandwidth.m1.json](records/qos.assured_bandwidth.m1.json) | not recorded | [qos.assured_bandwidth.m1.judge.log](logs/judge/qos.assured_bandwidth.m1.judge.log) | [before/after](logs/devices/qos.assured_bandwidth.m1/) |
