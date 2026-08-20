from qa_engine import get_embedding_model
import numpy as np

# Chunk the document into smaller overlapping windows
def chunk_text(text, chunk_size=300, overlap=50):
    words = text.split()
    chunks = []

    for start in range(0, len(words), chunk_size - overlap):
        chunk = words[start:start + chunk_size]
        chunks.append(" ".join(chunk))

    return chunks

# Embed the chunks into vector space
def embed_chunks(chunks):
    model = get_embedding_model()
    embeddings = model.encode(chunks)
    return np.array(embeddings)
