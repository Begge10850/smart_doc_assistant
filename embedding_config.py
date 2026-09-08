"""Single source of truth for Saidia's local embedding contract."""

EMBEDDING_MODEL = "sentence-transformers/all-mpnet-base-v2"
EMBEDDING_DIMENSION = 768


def validate_embedding_dimension(embedding):
    """Reject vectors that cannot fit the PostgreSQL pgvector columns."""
    try:
        dimension = len(embedding)
    except TypeError as exc:
        raise ValueError("Embedding must be a one-dimensional vector.") from exc
    if dimension != EMBEDDING_DIMENSION:
        raise ValueError(
            f"Expected a {EMBEDDING_DIMENSION}-dimension embedding from "
            f"{EMBEDDING_MODEL}, received {dimension}."
        )

