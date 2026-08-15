"""
ChromaDB-backed similarity index over past incidents ("knowledge base").

Embeddings are generated ourselves via the local Ollama nomic-embed-text model
(see ollama_client.embed) and passed into Chroma directly, rather than relying on
Chroma's built-in embedding function -- this keeps embedding generation, provider,
and model choice explicit and in one place.
"""

from typing import Optional

import chromadb

from triage_agent.config import CHROMA_DB_DIR, CHROMA_COLLECTION
from triage_agent.ollama_client import embed

_client = None
_collection = None


def get_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=CHROMA_DB_DIR)
        _collection = _client.get_or_create_collection(
            name=CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _embedding_text(record: dict) -> str:
    """The text we embed for a record -- consistent between indexing historical
    incidents and querying with a new incoming log, so vectors are comparable."""
    parts = [
        record.get("error_type", ""),
        record.get("service", ""),
        record.get("raw_log", ""),
    ]
    return "\n".join(p for p in parts if p)


def embed_record(record: dict) -> list:
    """Public helper so a caller can embed once and reuse the vector for both the
    similarity query and the write-back, instead of paying for two identical
    embedding calls per triage."""
    return embed(_embedding_text(record))


def add_incident(record: dict, vector: Optional[list] = None) -> None:
    """Embed and upsert a single incident record into the knowledge base.

    Pass `vector` to reuse an already-computed embedding (see embed_record)."""
    collection = get_collection()
    if vector is None:
        vector = embed(_embedding_text(record))
    metadata = {
        "log_id": record["log_id"],
        "timestamp": record.get("timestamp", ""),
        "service": record.get("service", ""),
        "error_type": record.get("error_type", ""),
        "layer": record.get("layer", ""),
        "severity": record.get("severity", ""),
        "resolution": record.get("resolution", ""),
        "raw_log": record.get("raw_log", ""),
    }
    collection.upsert(
        ids=[record["log_id"]],
        embeddings=[vector],
        metadatas=[metadata],
        documents=[record.get("raw_log", "")],
    )


def add_incidents_bulk(records: list) -> None:
    collection = get_collection()
    ids, embeddings, metadatas, documents = [], [], [], []
    for record in records:
        ids.append(record["log_id"])
        embeddings.append(embed(_embedding_text(record)))
        metadatas.append(
            {
                "log_id": record["log_id"],
                "timestamp": record.get("timestamp", ""),
                "service": record.get("service", ""),
                "error_type": record.get("error_type", ""),
                "layer": record.get("layer", ""),
                "severity": record.get("severity", ""),
                "resolution": record.get("resolution", ""),
                "raw_log": record.get("raw_log", ""),
            }
        )
        documents.append(record.get("raw_log", ""))
    collection.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)


def query_similar(
    raw_log: str,
    error_type: Optional[str] = None,
    service: Optional[str] = None,
    top_k: int = 3,
    vector: Optional[list] = None,
) -> list:
    """Find the top-k most similar past incidents to a new incoming log.

    Pass `vector` to reuse an already-computed embedding (see embed_record)."""
    collection = get_collection()
    if vector is None:
        text = _embedding_text({"raw_log": raw_log, "error_type": error_type or "", "service": service or ""})
        vector = embed(text)
    count = collection.count()
    if count == 0:
        return []
    result = collection.query(query_embeddings=[vector], n_results=min(top_k, count))

    matches = []
    ids = result.get("ids", [[]])[0]
    distances = result.get("distances", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    for i in range(len(ids)):
        meta = metadatas[i] or {}
        distance = distances[i]
        similarity = max(0.0, 1 - distance / 2)  # cosine distance -> similarity in [0,1]
        matches.append(
            {
                "log_id": ids[i],
                "similarity": round(similarity, 3),
                "service": meta.get("service"),
                "error_type": meta.get("error_type"),
                "layer": meta.get("layer"),
                "severity": meta.get("severity"),
                "resolution": meta.get("resolution"),
                "raw_log": meta.get("raw_log"),
            }
        )
    return matches


def count() -> int:
    return get_collection().count()


def reset_collection() -> None:
    """Drop the whole collection and start clean.

    Useful because every triage writes itself back into the index -- after a
    demo or a test run the knowledge base fills up with near-duplicates of your
    own sample logs, which then show up as spurious ~1.00 similarity matches."""
    global _client, _collection
    client = _client or chromadb.PersistentClient(path=CHROMA_DB_DIR)
    try:
        client.delete_collection(name=CHROMA_COLLECTION)
    except Exception:
        pass  # collection may not exist yet
    _client, _collection = None, None
