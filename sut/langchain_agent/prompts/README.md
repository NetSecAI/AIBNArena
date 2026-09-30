# System prompts

Every system prompt this subject runs under, one file per wording, selected by
name with `prompt_variant` in a run file. This is the only place prompt text is
written: `agent.py` holds none, and there is no prompt constant in the code.

- `default.txt`: the prompt the subject ships. It is what a run that names no
  variant gets, and what `prompt_variant = "default"` resolves to.
- `guided.txt`: the shipped prompt's contract with the tool use spelled out.
  Same opening paragraph, same output contract, same safety statements; what it
  adds is the method — which read tool answers what and how to scope it, how to
  form a hypothesis worth a mutation, what belongs in an `update_config` change,
  and that a conclusion needs a passing `public_success_criteria` validation
  behind it. It is there so a run can separate what the tool surface carries
  from what the prompt carries.

Adding a wording is dropping a `.txt` file here; no code changes. An unknown
name stops the run at startup rather than quietly measuring the default, and an
empty file is refused for the same reason.

Note the older subjects keep their shipped prompt as a module constant in
`agent.py` and have no `default.txt`; `sut.common.resolve_system_prompt`
supports both, preferring the file.
