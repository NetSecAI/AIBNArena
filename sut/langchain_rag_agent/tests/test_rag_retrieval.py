"""The corpus is read, cut and ranked the same way every time, or refused by name."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from sut.langchain_rag_agent.retrieval import (
    CorpusError,
    EmbeddingRetriever,
    LexicalRetriever,
    build_retriever,
    chunk_document,
    html_to_text,
    load_corpus,
    split_text,
    tokenize,
)

FIREWALL = """# VyOS firewall

VyOS filters traffic with named rule sets bound to zones.

## Zones

A zone groups interfaces. Traffic between two zones is filtered by the rule set
named in `set firewall zone <to> from <from> firewall name <ruleset>`.

```
# this line is a comment in a code block, not a heading
set firewall zone LAN interface eth1
```

## Default action

`set firewall name <ruleset> default-action drop` drops what no rule accepts.
"""

SRLINUX = """# SR Linux interfaces

## Admin state

An interface is enabled with `set / interface ethernet-1/1 admin-state enable`
and disabled with `admin-state disable`.

## MTU

`set / interface ethernet-1/1 mtu 9212` sets the MTU of the port.
"""


def write(directory: Path, name: str, text: str | bytes) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    write(tmp_path, "vyos/firewall.md", FIREWALL)
    write(tmp_path, "srlinux.md", SRLINUX)
    write(tmp_path, ".gitkeep", "")
    write(tmp_path, ".cache/notes.md", "hidden, never indexed")
    return tmp_path


# --- the corpus --------------------------------------------------------------------

def test_every_visible_document_is_read_in_path_order_and_hidden_files_are_not(corpus_dir):
    corpus = load_corpus(corpus_dir)
    assert [item.path for item in corpus.files] == ["srlinux.md", "vyos/firewall.md"]
    assert {chunk.source for chunk in corpus.chunks} == {"srlinux.md", "vyos/firewall.md"}
    manifest = corpus.manifest()
    assert manifest["chunks"] == len(corpus.chunks)
    assert manifest["sha256"] == corpus.sha256
    assert sum(item["chunks"] for item in manifest["files"]) == len(corpus.chunks)


def test_the_digest_names_the_content_and_nothing_else(corpus_dir):
    first = load_corpus(corpus_dir).sha256
    assert load_corpus(corpus_dir, chunk_chars=300, chunk_overlap=0).sha256 == first, \
        "chunking is recorded apart"
    write(corpus_dir, "srlinux.md", SRLINUX + "\nOne more line.\n")
    assert load_corpus(corpus_dir).sha256 != first


def test_a_file_that_cannot_be_read_is_refused_by_name(corpus_dir):
    write(corpus_dir, "manual.docx", b"PK\x03\x04")
    with pytest.raises(CorpusError, match="manual.docx"):
        load_corpus(corpus_dir)


def test_a_document_with_no_text_is_refused_by_name(corpus_dir):
    write(corpus_dir, "empty.md", "   \n")
    with pytest.raises(CorpusError, match=r"empty\.md .*has no text"):
        load_corpus(corpus_dir)


def test_a_missing_directory_is_refused(tmp_path):
    with pytest.raises(CorpusError, match="does not exist"):
        load_corpus(tmp_path / "nowhere")


@pytest.mark.parametrize("chunk_chars, chunk_overlap", [(50, 0), (1000, 600), (1000, -1)])
def test_chunk_settings_that_cannot_work_are_refused(corpus_dir, chunk_chars, chunk_overlap):
    with pytest.raises(CorpusError):
        load_corpus(corpus_dir, chunk_chars=chunk_chars, chunk_overlap=chunk_overlap)


def test_a_windows_encoded_document_is_read(tmp_path):
    write(tmp_path, "notes.txt", "Réseau d'accès : pare-feu activé".encode("cp1252"))
    corpus = load_corpus(tmp_path)
    assert corpus.chunks[0].text == "Réseau d'accès : pare-feu activé"


@pytest.mark.skipif(importlib.util.find_spec("pypdf") is not None, reason="pypdf is installed")
def test_a_pdf_without_pypdf_names_what_to_install(tmp_path):
    write(tmp_path, "manual.pdf", b"%PDF-1.4\n")
    with pytest.raises(CorpusError, match="pypdf"):
        load_corpus(tmp_path)


# --- cutting ---------------------------------------------------------------------

def test_markdown_is_cut_at_its_headings_and_each_piece_knows_its_section():
    pieces = chunk_document(FIREWALL, markdown=True, chunk_chars=1000, chunk_overlap=0)
    assert [section for section, _ in pieces] == [
        "VyOS firewall", "VyOS firewall > Zones", "VyOS firewall > Default action"]
    zones = pieces[1][1]
    assert "# this line is a comment in a code block" in zones, "a # inside a fence is not a heading"


def test_plain_text_has_no_sections():
    pieces = chunk_document(FIREWALL, markdown=False, chunk_chars=5000, chunk_overlap=0)
    assert pieces == [(None, FIREWALL.strip())]


def test_long_text_is_cut_on_boundaries_under_the_size_with_overlap():
    paragraphs = [f"Paragraph {n}. " + "word " * 40 for n in range(12)]
    text = "\n\n".join(paragraphs)
    chunks = split_text(text, 600, 250)
    assert len(chunks) > 1
    assert all(len(chunk) <= 600 for chunk in chunks)
    for paragraph in paragraphs:
        assert any(paragraph.strip() in chunk for chunk in chunks), "nothing is lost"
    # Consecutive chunks share whole paragraphs, never a word cut in two.
    shared = [paragraph for paragraph in paragraphs
              if sum(paragraph.strip() in chunk for chunk in chunks) > 1]
    assert shared


def test_a_single_overlong_line_is_still_cut_to_size():
    chunks = split_text("x" * 2500, 1000, 0)
    assert [len(chunk) for chunk in chunks] == [1000, 1000, 500]


def test_html_headings_become_sections_and_scripts_are_dropped(tmp_path):
    write(tmp_path, "page.html", """<html><head><title>t</title><style>p{}</style></head>
        <body><script>var hidden = 1;</script><h1>QoS</h1><p>Shaping with   HTB.</p>
        <h2>Classes</h2><ul><li>class 10</li><li>class 20</li></ul></body></html>""")
    corpus = load_corpus(tmp_path)
    assert [chunk.section for chunk in corpus.chunks] == ["QoS", "QoS > Classes"]
    assert corpus.chunks[0].text == "Shaping with HTB."
    assert "hidden" not in " ".join(chunk.text for chunk in corpus.chunks)
    assert html_to_text("<p>a</p><p>b</p>") == "a\n\nb"


# --- ranking ---------------------------------------------------------------------

def test_tokens_keep_compounds_and_their_parts_without_case_accents_or_stopwords():
    tokens = tokenize("Set the Interface ethernet-1/1 admin_state to Réseau")
    assert "ethernet-1/1" in tokens and "ethernet" in tokens
    assert "admin_state" in tokens and {"admin", "state"} <= set(tokens)
    assert "reseau" in tokens
    assert "the" not in tokens and "to" not in tokens


def test_bm25_puts_the_excerpt_that_answers_first(corpus_dir):
    retriever = LexicalRetriever(load_corpus(corpus_dir))
    hits = retriever.search("how to disable an interface admin state", 2)
    assert hits[0].chunk.section == "SR Linux interfaces > Admin state"
    hits = retriever.search("firewall zone rule set", 1)
    assert hits[0].chunk.source == "vyos/firewall.md"
    assert retriever.search("completely unrelated banana", 3) == []


def test_an_inflected_word_finds_the_command_that_names_it(tmp_path):
    """The way a task words it is not the way a command reference does."""
    write(tmp_path, "zones.md", "# Zones\n\n`set firewall zone LAN interface eth1` puts eth1 in LAN.\n")
    write(tmp_path, "ports.md", "# Admin state\n\nA port comes back with "
                                "`set / interface ethernet-1/1 admin-state enable`.\n")
    retriever = LexicalRetriever(load_corpus(tmp_path))
    hits = retriever.search("An interface was administratively disabled; restore connectivity.", 2)
    assert hits[0].chunk.source == "ports.md"
    assert tokenize("désactivée")[-1] == tokenize("désactiver")[-1] == "desac"


def test_bm25_ranks_the_same_way_every_time(corpus_dir):
    first = LexicalRetriever(load_corpus(corpus_dir)).search("interface", 4)
    second = LexicalRetriever(load_corpus(corpus_dir)).search("interface", 4)
    assert [(hit.chunk.id, hit.score) for hit in first] == [(hit.chunk.id, hit.score) for hit in second]


class BagOfWords:
    """A deterministic stand-in for an embedding model: one axis per known word."""

    VOCABULARY = ("firewall", "zone", "interface", "mtu", "admin", "drop")

    def __init__(self) -> None:
        self.document_calls = 0
        self.timeouts: list[float] = []

    def _vector(self, text: str) -> list[float]:
        words = tokenize(text)
        return [float(words.count(word)) for word in self.VOCABULARY]

    def embed_documents(self, texts):
        self.document_calls += 1
        return [self._vector(text) for text in texts]

    def embed_query(self, text):
        return self._vector(text)

    def model_copy(self, update):
        self.timeouts.append(update["request_timeout"])
        return self


def test_an_embedding_model_the_endpoint_refuses_stops_startup_by_name(corpus_dir, monkeypatch):
    import litellm

    def refuse(**kwargs):
        raise litellm.exceptions.BadRequestError("model not found", model=kwargs["model"],
                                                 llm_provider="openai")

    monkeypatch.setattr(litellm, "embedding", refuse)
    with pytest.raises(CorpusError, match="could not embed the corpus with openai/no-such-model"):
        build_retriever(load_corpus(corpus_dir), embedding_model="openai/no-such-model",
                        api_base="http://127.0.0.1:9", api_key="k")


def test_embeddings_are_computed_once_in_batches_and_rank_by_cosine(corpus_dir):
    embeddings = BagOfWords()
    retriever = EmbeddingRetriever(load_corpus(corpus_dir), embeddings, batch_size=2)
    batches = embeddings.document_calls
    assert batches == -(-len(retriever.corpus.chunks) // 2)
    hits = retriever.search("mtu", 1, timeout=12.5)
    assert hits[0].chunk.section == "SR Linux interfaces > MTU"
    assert 0.0 < hits[0].score <= 1.0
    assert embeddings.timeouts == [12.5], "the query is bounded by the time the episode has left"
    assert embeddings.document_calls == batches, "a search embeds the query, never the corpus again"
