"""Domain RAG for research agents (kick-off: multi-agent RAG pipelines).

Was a keyword scan over `demo/maya/rag_corpus.json` -- one creator's notes,
served to everyone. Now it reads `rag_documents` for the profile in context, so
each creator retrieves their own history.

The scoring is still keyword overlap. Replace with embeddings when it matters;
the interface does not change.
"""

from __future__ import annotations

from typing import Any

# Documents carry title/notes, not a single `text` blob - scoring against a
# field that does not exist scored every document zero.
_FIELDS = ("text", "title", "notes", "insight", "summary", "type", "platform")


def _searchable(doc: dict[str, Any]) -> str:
    return " ".join(str(doc.get(f, "")) for f in _FIELDS).lower()


def rank(docs: list[dict[str, Any]], query: str, k: int = 4) -> list[dict[str, Any]]:
    """Score by keyword overlap. Falls back to the first k when nothing matches."""
    q = query.lower()
    scored = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        score = sum(1 for w in q.split() if w in _searchable(doc))
        scored.append((score, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [d for s, d in scored[:k] if s > 0] or [d for _, d in scored[:k]]


async def retrieve(query: str, k: int = 4) -> list[dict[str, Any]]:
    """Retrieve this creator's notes. Requires a profile in context."""
    from pipeline_manager import db

    return rank(await db.list_rag_documents(), query, k)
