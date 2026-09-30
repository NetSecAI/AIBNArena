# LangChain RAG subject

The [LangChain baseline](../langchain_agent/) with retrieval-augmented generation
over a corpus of reference documents.

`LangChainRAGAgent` subclasses `LangChainRepairAgent` rather than copying it: the
model binding, the eight ANI tools, the budget, the stop rules and the report are
the baseline's. A difference between the two subjects in a campaign is therefore
the retrieval and nothing else. The system prompt
([`prompts/default.txt`](prompts/default.txt)) is the baseline's, with a
*Documentation* section added and one sentence saying that excerpts may follow the task.

## The documents: `rag_document/`

Put the reference documents in [`rag_document/`](rag_document/): vendor manuals,
command references, runbooks, notes on the lab. Subdirectories are fine.

- **Formats**: Markdown, plain-text formats (`.txt .rst .adoc .json .yaml .yml .toml
  .ini .cfg .conf .csv .xml`), HTML, and PDF. Reading a PDF needs `pypdf`
  (`.venv/bin/python -m pip install pypdf`).
- **Read once, at startup.** Restart the server after adding or changing a document.
- **Refused by name, at startup**: a file in any other format, a document with no
  text (a scanned PDF, for example), and an empty corpus outside `--dry-run`.
  Hidden files (`.gitkeep`) are ignored. A corpus that silently lacked a document
  would be recorded as the corpus that was meant.
- **What goes in is what the agent can read.** Do not put this repository's
  scenarios, oracles or fault descriptions there: the subject would be reading the
  answer key, and its results would say nothing about repair.

Markdown and HTML documents are cut at their headings first, so each excerpt knows
its section (`Firewall > Zones`). Each section is then cut into excerpts of at most
`--rag-chunk-chars` characters on paragraph, line, sentence or word boundaries, and
consecutive excerpts share up to `--rag-chunk-overlap` characters.

## How retrieval reaches the model

`--rag-mode` selects one or both:

| Mode | What the model gets |
|---|---|
| `context` | The task is used as a query (its intent and the values of its success criteria), and the best `--rag-top-k` excerpts are appended to the first message, after the task JSON. |
| `tool` | A `search_documents(query, top_k)` tool next to the ANI tools. The first message is exactly the baseline's. |
| `both` (default) | Both. |

Excerpts are ranked by **BM25** by default: no service, no cost, the same ranking
every time, and network documentation is full of exact keywords (`ethernet-1/1`,
`admin-state`, `set firewall zone`) that lexical ranking matches well. Compound tokens
are also split, so "admin state" finds `admin-state` and `admin_state`. Case and
accents are ignored, and common English and French words are dropped.

`--rag-embedding-model openai/text-embedding-3-small` (any LiteLLM model id) ranks
by cosine similarity between embeddings instead. The endpoint is the chat model's
(`--api-base`, `--api-key`). The whole corpus is embedded once, at startup. Each
search embeds its query within the episode's remaining budget.

A search is not an ANI operation. It reads no device and changes nothing. It is
not counted in `ani_operations`, `tool_call_count` or `--ani-call-limit`, so those
stay comparable with the baseline's. If a search fails, the model gets
`{"ok": false, "error": ...}` and the episode continues. A search after the budget
ends the episode on its budget, the same way an ANI call would.

## Settings

| Flag | Environment | Default |
|---|---|---|
| `--rag-directory` | `LANGCHAIN_RAG_AGENT_RAG_DIRECTORY` | `sut/langchain_rag_agent/rag_document/` |
| `--rag-mode` | `LANGCHAIN_RAG_AGENT_RAG_MODE` | `both` |
| `--rag-top-k` | `LANGCHAIN_RAG_AGENT_RAG_TOP_K` | `4` (at most 10) |
| `--rag-chunk-chars` | `LANGCHAIN_RAG_AGENT_RAG_CHUNK_CHARS` | `1500` |
| `--rag-chunk-overlap` | `LANGCHAIN_RAG_AGENT_RAG_CHUNK_OVERLAP` | `200` |
| `--rag-embedding-model` | `LANGCHAIN_RAG_AGENT_RAG_EMBEDDING_MODEL` | none: BM25 |

Every baseline flag is accepted too, and read from the environment under this
subject's own prefix (`LANGCHAIN_RAG_AGENT_MODEL`, `LANGCHAIN_RAG_AGENT_API_KEY`, and
so on), falling back to `LLM_API_BASE` and `LLM_API_KEY`. The two subjects can therefore
run from one shell without reading each other's settings. The web interface offers
this subject as *LangChain RAG*, on port 8004. It does not offer the `--rag-*`
settings, so it uses the defaults or the environment it was started from.

## What a result records

- `execution.model_parameters` has every setting above as `rag_*`, `rag_retriever`
  (`bm25` or `embedding`), `rag_directory`, and `rag_corpus`. `rag_corpus` holds the
  SHA-256 of the whole corpus and each file's path, digest and excerpt count. It is
  how two results show whether they ran on the same documents.
- `execution.rag` records each search the episode made: its origin (`context` or
  `tool`), query, duration, the ids and scores of the excerpts it returned, and any
  error. It also records `tool_searches` and `context_excerpts`. It records no excerpt text.
- With `--debug-trace full`, the trace holds the excerpts' text as the model saw them.

## Running it

```bash
./sut/langchain_rag_agent/a2a_server.py \
  --scenario-topology scenarios/topologies/sme_leaf_spine_dmz_small.yaml \
  --healthy-state benchmarks/testbeds/containerlab/sme01-small/states/healthy.json
```

At startup the server logs the corpus it indexed: directory, number of documents and
excerpts, and digest. Pass `--sut-url http://127.0.0.1:8004` to `benchmarks/run.py`.

## Tests

```bash
.venv/bin/python -m pytest -q sut/langchain_rag_agent
```

These tests use scripted graphs and a fake ANI. They need no lab, provider or server.
