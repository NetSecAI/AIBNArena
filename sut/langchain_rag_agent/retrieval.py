"""The documents the RAG subject retrieves from, and the two ways it ranks them.

Everything a retrieval depends on is fixed when the server starts: the files under
`rag_document/` are read, cut into chunks and indexed once, and the corpus is
fingerprinted so a result can say which documents were in force. An episode only
ranks.

Lexical ranking (BM25) is the default. It needs no service and costs nothing, it
gives the same answer every time, and network documentation is keyword-heavy
(command paths, interface names, attribute names), which is where it is strongest.
Dense ranking over an embedding model served through LiteLLM is the alternative,
selected by naming the model.

A file the loader cannot read is refused by name at startup rather than skipped:
a corpus that silently lacks a document someone put there would be recorded as
the corpus they meant.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol, Sequence

MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
HTML_SUFFIXES = frozenset({".html", ".htm"})
PDF_SUFFIXES = frozenset({".pdf"})
TEXT_SUFFIXES = frozenset({
    ".txt", ".text", ".rst", ".adoc", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".csv", ".xml",
}) | MARKDOWN_SUFFIXES
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | HTML_SUFFIXES | PDF_SUFFIXES


class CorpusError(ValueError):
    """The corpus cannot be indexed as it stands. Raised at startup, naming the file."""


@dataclass(frozen=True)
class Chunk:
    """One retrievable excerpt of one document."""

    source: str
    #: Position within its document, from 0.
    index: int
    #: The Markdown headings in force, outermost first ("Firewall > Zones").
    section: str | None
    text: str

    @property
    def id(self) -> str:
        return f"{self.source}#{self.index}"

    def searchable(self) -> str:
        """What is ranked: the text, with the words its location gives it.

        A file name and a heading say what an excerpt is about in words the text
        itself often leaves out ("Zones" above a list of commands).
        """
        location = Path(self.source).with_suffix("").as_posix().replace("/", " ")
        return "\n".join(part for part in (location, self.section, self.text) if part)


@dataclass(frozen=True)
class SourceFile:
    path: str
    sha256: str
    chunks: int


@dataclass(frozen=True)
class Corpus:
    directory: Path
    files: tuple[SourceFile, ...]
    chunks: tuple[Chunk, ...]

    @property
    def sha256(self) -> str:
        """One digest for the whole corpus: every file's path and content."""
        digest = hashlib.sha256()
        for item in self.files:
            digest.update(f"{item.path}\0{item.sha256}\n".encode())
        return digest.hexdigest()

    def manifest(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "chunks": len(self.chunks),
            "files": [
                {"path": item.path, "sha256": item.sha256, "chunks": item.chunks}
                for item in self.files
            ],
        }


def load_corpus(directory: str | Path, *, chunk_chars: int = 1500, chunk_overlap: int = 200) -> Corpus:
    """Read, cut and fingerprint every document under `directory`.

    Hidden files and directories (`.gitkeep`, `.DS_Store`, `.git/`) are not
    documents. Anything else must be a format this module reads, and must have
    text in it.
    """
    if chunk_chars < 100:
        raise CorpusError(f"rag chunk size must be at least 100 characters, not {chunk_chars}")
    if not 0 <= chunk_overlap <= chunk_chars // 2:
        raise CorpusError(
            f"rag chunk overlap must be between 0 and half the chunk size ({chunk_chars // 2}), "
            f"not {chunk_overlap}")
    root = Path(directory)
    if not root.is_dir():
        raise CorpusError(f"RAG document directory does not exist: {root}")
    paths = sorted(
        (path for path in root.rglob("*") if path.is_file() and not _hidden(path, root)),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    unsupported = [path.relative_to(root).as_posix() for path in paths
                   if path.suffix.lower() not in SUPPORTED_SUFFIXES]
    if unsupported:
        raise CorpusError(
            f"cannot index {', '.join(unsupported)} in {root}: supported formats are "
            f"{', '.join(sorted(SUPPORTED_SUFFIXES))}")
    files: list[SourceFile] = []
    chunks: list[Chunk] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        text = extract_text(path, raw)
        if not text.strip():
            raise CorpusError(f"{relative} in {root} has no text to index")
        pieces = chunk_document(
            text, markdown=path.suffix.lower() in MARKDOWN_SUFFIXES | HTML_SUFFIXES,
            chunk_chars=chunk_chars, chunk_overlap=chunk_overlap)
        chunks.extend(Chunk(relative, index, section, body)
                      for index, (section, body) in enumerate(pieces))
        files.append(SourceFile(relative, hashlib.sha256(raw).hexdigest(), len(pieces)))
    return Corpus(root, tuple(files), tuple(chunks))


def _hidden(path: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(root).parts)


# --- text extraction -----------------------------------------------------------------

def extract_text(path: Path, raw: bytes) -> str:
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return _pdf_text(path)
    text = _decode(raw)
    if suffix in HTML_SUFFIXES:
        return html_to_text(text)
    return text


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Documents saved on Windows in a Western European code page, which is the
        # other encoding French and English documentation turns up in.
        return raw.decode("cp1252", errors="replace")


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise CorpusError(
            f"{path.name} is a PDF, and reading a PDF needs pypdf "
            "(.venv/bin/python -m pip install pypdf)") from exc
    reader = PdfReader(str(path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


class _HTMLText(HTMLParser):
    """HTML reduced to text, with its headings written as Markdown headings.

    That way an HTML page is cut into sections exactly like a Markdown file.
    """

    BLOCKS = frozenset({
        "p", "div", "section", "article", "header", "footer", "table", "tr", "ul",
        "ol", "dl", "dt", "dd", "blockquote", "br", "hr", "pre", "figure",
    })
    SKIPPED = frozenset({"script", "style", "head", "nav", "noscript", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0
        self._pre = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self.SKIPPED:
            self._skipping += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag in self.BLOCKS:
            self.parts.append("\n")
        if tag == "pre":
            self._pre += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIPPED:
            self._skipping = max(0, self._skipping - 1)
        elif re.fullmatch(r"h[1-6]", tag) or tag in ("p", "pre", "table"):
            self.parts.append("\n\n")
        if tag == "pre":
            self._pre = max(0, self._pre - 1)

    def handle_data(self, data: str) -> None:
        if self._skipping:
            return
        self.parts.append(data if self._pre else re.sub(r"\s+", " ", data))


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    parser.close()
    lines = [line.rstrip() for line in "".join(parser.parts).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# --- chunking --------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
#: Coarsest first: a chunk is cut between paragraphs where it can be, then between
#: lines, then between sentences, then between words.
_SEPARATORS = ("\n\n", "\n", ". ", " ")


def chunk_document(
    text: str, *, markdown: bool, chunk_chars: int, chunk_overlap: int,
) -> list[tuple[str | None, str]]:
    """(section, text) pieces of at most `chunk_chars`, in document order.

    A Markdown document is first cut at its headings, so no chunk straddles two
    sections and every chunk knows which section it comes from; each section is
    then cut to size, consecutive chunks sharing up to `chunk_overlap` characters.
    """
    pieces: list[tuple[str | None, str]] = []
    for section, body in (_sections(text) if markdown else [(None, text)]):
        pieces.extend((section, chunk) for chunk in split_text(body, chunk_chars, chunk_overlap))
    return pieces


def _sections(text: str) -> list[tuple[str | None, str]]:
    sections: list[tuple[str | None, str]] = []
    trail: list[str] = []
    body: list[str] = []
    fenced = False

    def flush() -> None:
        content = "\n".join(body).strip()
        if content:
            sections.append((" > ".join(trail) or None, content))
        body.clear()

    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        heading = None if fenced else _HEADING.match(line)
        if heading is None:
            body.append(line)
            continue
        flush()
        level = len(heading.group(1))
        trail = trail[: level - 1] + [heading.group(2).strip()]
    flush()
    return sections


def split_text(text: str, chunk_chars: int, chunk_overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]
    return _merge(_atoms(text, chunk_chars, 0), chunk_chars, chunk_overlap)


def _atoms(text: str, size: int, level: int) -> list[str]:
    """Pieces of at most `size` that join back into `text`."""
    if len(text) <= size:
        return [text]
    if level == len(_SEPARATORS):
        return [text[start:start + size] for start in range(0, len(text), size)]
    separator = _SEPARATORS[level]
    parts = text.split(separator)
    atoms: list[str] = []
    for position, part in enumerate(parts):
        # The separator stays with the piece before it, so nothing is lost.
        piece = part + separator if position < len(parts) - 1 else part
        if piece:
            atoms.extend(_atoms(piece, size, level + 1))
    return atoms


def _merge(atoms: list[str], size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for atom in atoms:
        if current and length + len(atom) > size:
            chunks.append("".join(current).strip())
            # The next chunk opens with the whole pieces that end this one, as many
            # as fit in the overlap, so a sentence cut here is still read whole there.
            carried: list[str] = []
            for piece in reversed(current):
                if sum(map(len, carried)) + len(piece) > overlap:
                    break
                carried.insert(0, piece)
            current = carried
            length = sum(map(len, carried))
            if length + len(atom) > size:
                current, length = [], 0
        current.append(atom)
        length += len(atom)
    if current:
        chunks.append("".join(current).strip())
    return [chunk for chunk in chunks if chunk]


# --- ranking ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


class Retriever(Protocol):
    #: How it ranks, as a result records it.
    name: str

    def search(self, query: str, top_k: int, *, timeout: float | None = None) -> list[Hit]:
        ...


#: A compound such as `ethernet-1/1`, `10.10.40.0/24`, `admin-state` or `admin_state`
#: is one token, and its parts are tokens too: a query for "admin state" finds
#: `admin-state`, and one for `ethernet-1/1` prefers the exact interface.
_TOKEN = re.compile(r"\w+(?:[-./:]\w+)*")
_PARTS = re.compile(r"[-./:_]+")

#: Common English and French words, which say nothing about what an excerpt is for.
STOPWORDS = frozenset("""
a an and are as at be been but by can do does for from has have how if in into is it
its may must no not of on or should so than that the their then there these this those
to was were what when where which while who why will with would you your
au aux avec ce ces cette dans de des du elle en est et il ils je la le les leur mais
ne nous ou par pas pour qu que qui sa se ses son sont sur un une vous
""".split())


def _fold(text: str) -> str:
    """Case and accents dropped, so `Réseau` and `reseau` are one word."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


#: A word longer than this also counts by its first letters, so "disabled" finds
#: `disable`, "administratively" finds `admin` and "désactivée" finds "désactiver".
#: Truncation stems English and French alike, where a stemmer would need one per
#: language; the whole word still counts too, so an exact match ranks first.
PREFIX = 5


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN.finditer(_fold(text)):
        token = match.group(0)
        parts = [part for part in _PARTS.split(token) if part]
        if len(parts) > 1:
            tokens.append(token)
        for part in parts:
            if len(part) < 2 or part in STOPWORDS:
                continue
            tokens.append(part)
            if len(part) > PREFIX and part.isalpha():
                tokens.append(part[:PREFIX])
    return tokens


class BM25Index:
    """Okapi BM25 over a fixed list of texts."""

    def __init__(self, texts: Sequence[str], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.terms = [Counter(tokenize(text)) for text in texts]
        self.lengths = [sum(counts.values()) for counts in self.terms]
        self.average_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        postings: dict[str, list[int]] = defaultdict(list)
        for position, counts in enumerate(self.terms):
            for term in counts:
                postings[term].append(position)
        self.postings = dict(postings)
        count = len(self.terms)
        # The +1 keeps every weight positive, so a term found in most texts still
        # counts for a little rather than against.
        self.idf = {
            term: math.log(1 + (count - len(found) + 0.5) / (len(found) + 0.5))
            for term, found in self.postings.items()
        }

    def scores(self, query: str) -> dict[int, float]:
        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            weight = self.idf.get(term)
            if weight is None:
                continue
            for position in self.postings[term]:
                frequency = self.terms[position][term]
                relative = self.lengths[position] / self.average_length if self.average_length else 1.0
                scores[position] += weight * frequency * (self.k1 + 1) / (
                    frequency + self.k1 * (1 - self.b + self.b * relative))
        return dict(scores)


class LexicalRetriever:
    name = "bm25"

    def __init__(self, corpus: Corpus) -> None:
        self.corpus = corpus
        self.index = BM25Index([chunk.searchable() for chunk in corpus.chunks])

    def search(self, query: str, top_k: int, *, timeout: float | None = None) -> list[Hit]:
        # Ties go to the earlier chunk, so a ranking never depends on dict order.
        ranked = sorted(self.index.scores(query).items(), key=lambda item: (-item[1], item[0]))
        return [Hit(self.corpus.chunks[position], round(score, 4))
                for position, score in ranked[:top_k] if score > 0]


class EmbeddingRetriever:
    """Cosine similarity between the query and every chunk, embedded once at startup."""

    name = "embedding"

    def __init__(self, corpus: Corpus, embeddings: Any, *, batch_size: int = 64) -> None:
        import numpy as np

        self.corpus = corpus
        self.embeddings = embeddings
        texts = [chunk.searchable() for chunk in corpus.chunks]
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            vectors.extend(embeddings.embed_documents(texts[start:start + batch_size]))
        matrix = np.asarray(vectors, dtype=float).reshape(len(texts), -1)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.matrix = matrix / norms

    def search(self, query: str, top_k: int, *, timeout: float | None = None) -> list[Hit]:
        import numpy as np

        if not len(self.matrix):
            return []
        embeddings = self.embeddings
        if timeout is not None and hasattr(embeddings, "model_copy"):
            # The query is embedded inside the episode, so it gets the episode's
            # remaining time and not the client's own default.
            embeddings = embeddings.model_copy(update={"request_timeout": max(timeout, 0.001)})
        vector = np.asarray(embeddings.embed_query(query), dtype=float)
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            return []
        similarities = self.matrix @ (vector / norm)
        ranked = sorted(range(len(similarities)), key=lambda position: (-similarities[position], position))
        return [Hit(self.corpus.chunks[position], round(float(similarities[position]), 4))
                for position in ranked[:top_k]]


def build_retriever(
    corpus: Corpus,
    *,
    embedding_model: str | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
) -> Retriever:
    """BM25 unless an embedding model is named, in which case the corpus is embedded now."""
    if not embedding_model:
        return LexicalRetriever(corpus)
    import litellm
    from langchain_litellm import LiteLLMEmbeddings

    # The same banner the chat model silences (sut/langchain_agent/agent.py).
    litellm.suppress_debug_info = True
    try:
        return EmbeddingRetriever(corpus, LiteLLMEmbeddings(
            model=embedding_model, api_base=api_base, api_key=api_key))
    except Exception as exc:
        # An unknown model or an unreachable endpoint stops the server by name, as
        # an unreadable document does, rather than with the provider's traceback.
        raise CorpusError(
            f"could not embed the corpus with {embedding_model}: {type(exc).__name__}: {exc}") from exc
