"""Vector embedding generation using sentence-transformers."""

from sentence_transformers import SentenceTransformer

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    """Lazy-load the embedding model."""
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def encode(texts: list[str]) -> list[list[float]]:
    """Encode texts to 384-dim vectors."""
    model = get_model()
    embeddings = model.encode(texts, show_progress_bar=False)
    return [emb.tolist() for emb in embeddings]


def encode_query(query: str) -> str:
    """Encode a single query and return as pgvector string."""
    model = get_model()
    emb = model.encode([query])[0]
    return "[" + ",".join(str(float(x)) for x in emb) + "]"
