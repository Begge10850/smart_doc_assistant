from sentence_transformers import SentenceTransformer
import faiss
import numpy as np
import re

# Load a lightweight but powerful model
model = SentenceTransformer('all-MiniLM-L6-v2')

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
    embeddings = model.encode(chunks)
    return np.array(embeddings)

# Store the embeddings in FAISS
def build_faiss_index(embeddings):
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(embeddings)
    return index
