import os
import numpy as np
from sentence_transformers import SentenceTransformer
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize OpenAI client (new API style)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
print("🔐 OpenAI key loaded:", os.getenv("OPENAI_API_KEY")[:6] + "...")

# Load the same embedding model used for your chunks
model = SentenceTransformer("all-MiniLM-L6-v2")

# Search FAISS index for top-k relevant chunks
def search_index(user_question, index, chunks, top_k=3):
    question_embedding = model.encode([user_question])
    D, I = index.search(np.array(question_embedding), top_k)
    matched_chunks = [chunks[i] for i in I[0]]
    return matched_chunks

print("✅ GPT response received")

# Use OpenAI GPT to answer the question based on context
def answer_question_with_gpt(question, context_chunks):
    prompt = f"""You are a helpful assistant. Use the following document snippets to answer the user's question.

Document Snippets:
{''.join(['- ' + chunk + '\n' for chunk in context_chunks])}

Question: {question}
Answer:"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        print("🛑 OpenAI API Error:", e)
        return "⚠️ Could not get a response from GPT. You may have exhausted your API quota or encountered a network issue."

