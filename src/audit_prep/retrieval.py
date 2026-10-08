"""Filter before lexical ranking, then expand selected documents within a fixed budget."""

import hashlib
import math
import re
from collections import Counter
from typing import Protocol

from audit_prep.domain import AuthorizationContext, Document, EvidenceChunk, RetrievalResult

# A conservative heuristic for instruction-like text, not a complete prompt-injection defense.
INSTRUCTION_LIKE = re.compile(
    r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\s+"
    r"(?:instructions|prompts?|rules)|"
    r"disregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)|"
    r"^\s*(?:system|assistant)\s*:|\byou\s+(?:are|must)\s+now\b|"
    r"\b(?:do\s+not|don't)\s+(?:mention|tell|reveal)\b|</?\s*(?:evidence|system|instructions?)\b",
    re.IGNORECASE | re.MULTILINE,
)


def looks_like_injection(text: str) -> bool:
    return INSTRUCTION_LIKE.search(text) is not None


def is_authorized(document: Document | EvidenceChunk, auth: AuthorizationContext) -> bool:
    document_id = document.id if isinstance(document, Document) else document.document_id

    return (
        document.tenant_id == auth.tenant_id
        and document.audit_id in auth.allowed_audit_ids
        and (auth.allowed_document_ids is None or document_id in auth.allowed_document_ids)
    )


def authorized_documents(documents: list[Document], auth: AuthorizationContext) -> list[Document]:
    return [doc for doc in documents if is_authorized(doc, auth)]


def chunk_document(document: Document, chunk_size: int = 1800) -> list[EvidenceChunk]:
    chunks = []
    section = "Document header"
    paragraphs = re.split(r"\n\s*\n", document.content.strip())
    for paragraph_index, paragraph in enumerate(paragraphs, start=1):
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        if paragraph.startswith("Section "):
            section = paragraph.splitlines()[0]

        for offset in range(0, len(paragraph), chunk_size):
            text = paragraph[offset : offset + chunk_size]
            identity = "\0".join(
                [
                    document.tenant_id,
                    document.audit_id,
                    document.id,
                    str(paragraph_index),
                    str(offset),
                    text,
                ]
            )
            chunks.append(
                EvidenceChunk(
                    id="chunk-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                    document_id=document.id,
                    tenant_id=document.tenant_id,
                    organization_id=document.organization_id,
                    audit_id=document.audit_id,
                    document_type=document.document_type,
                    text=text,
                    section=f"{section} / paragraph {paragraph_index}",
                    version=document.version,
                )
            )

    return chunks


def _tokens(text: str) -> Counter[str]:
    return Counter(re.findall(r"[a-z0-9]+", text.lower()))


def _rank_documents(query: str, documents: list[Document]) -> list[Document]:
    """Small local TF-IDF cosine implementation. The input is already authorized."""
    counts = [_tokens(doc.content) for doc in documents]
    query_counts = _tokens(query)
    frequencies = Counter(token for count in counts for token in count)
    idf = {
        token: math.log((1 + len(counts)) / (1 + frequency)) + 1
        for token, frequency in frequencies.items()
    }

    def vector(count: Counter[str]) -> dict[str, float]:
        return {token: (1 + math.log(n)) * idf.get(token, 1) for token, n in count.items()}

    query_vector = vector(query_counts)
    query_norm = math.sqrt(sum(weight * weight for weight in query_vector.values()))
    ranked = []
    for doc, count in zip(documents, counts, strict=True):
        weights = vector(count)
        norm = math.sqrt(sum(weight * weight for weight in weights.values()))
        dot = sum(weight * weights.get(token, 0) for token, weight in query_vector.items())
        score = dot / (query_norm * norm) if query_norm and norm else 0
        if score > 0:
            ranked.append([score, doc])

    ranked.sort(key=lambda entry: [-entry[0], entry[1].audit_id, entry[1].id])

    return [doc for _, doc in ranked]


class EvidenceRetriever(Protocol):
    def retrieve(
        self,
        query: str,
        documents: list[Document],
        auth: AuthorizationContext,
        document_types: set[str] | None = None,
    ) -> RetrievalResult: ...


class LexicalEvidenceRetriever:
    def __init__(self, max_chunks: int = 24, max_characters: int = 16000):
        if max_chunks < 1 or max_characters < 1:
            raise ValueError("Retrieval budgets must be positive")

        self.max_chunks = max_chunks
        self.max_characters = max_characters

    def retrieve(
        self,
        query: str,
        documents: list[Document],
        auth: AuthorizationContext,
        document_types: set[str] | None = None,
    ) -> RetrievalResult:
        visible = authorized_documents(documents, auth)
        visible = [
            doc for doc in visible if document_types is None or doc.document_type in document_types
        ]
        ranked = _rank_documents(query, visible)
        chunks = []
        withheld = []
        characters = 0
        for document in ranked:
            # Include all sections of a selected document, including possible counter-evidence.
            for chunk in chunk_document(document):
                if looks_like_injection(chunk.text):
                    withheld.append(chunk)
                    continue

                if (
                    len(chunks) >= self.max_chunks
                    or characters + len(chunk.text) > self.max_characters
                ):
                    return RetrievalResult(
                        chunks=chunks,
                        complete=False,
                        detail="Relevant document context exceeded the retrieval budget.",
                        withheld=withheld,
                    )

                chunks.append(chunk)
                characters += len(chunk.text)

        return RetrievalResult(chunks=chunks, complete=True, withheld=withheld)
