import os
import numpy as np
from sentence_transformers import SentenceTransformer
import openai
import streamlit as st

# ─── LOAD OPENAI API KEY ────────────────────────────────────────────────────────
try:
    openai.api_key = st.secrets["openai"]["OPENAI_API_KEY"]
except Exception:
    # Fallback for local development
    from dotenv import load_dotenv
    load_dotenv()
    openai.api_key = os.getenv("OPENAI_API_KEY")

# Confirm load
print("🔐 OpenAI key loaded:", openai.api_key[:6] + "...")

# ─── EMBEDDING MODEL ────────────────────────────────────────────────────────────
model = SentenceTransformer("all-MiniLM-L6-v2")

# ─── FAISS SEARCH FUNCTION ──────────────────────────────────────────────────────
def search_index(user_question, index, chunks, top_k=3):
    question_embedding = model.encode([user_question])
    D, I = index.search(np.array(question_embedding), top_k)
    matched_chunks = [chunks[i] for i in I[0]]
    return matched_chunks

# ─── GPT-POWERED QUESTION ANSWERING ─────────────────────────────────────────────
def answer_question_with_gpt(question, context_chunks):
    prompt = f"""You are a helpful assistant. Use the following document snippets to answer the user's question.

Document Snippets:
{''.join(['- ' + chunk + '\n' for chunk in context_chunks])}

Question: {question}
Answer:"""

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500
        )

        # Debug logs (will show in Streamlit Cloud logs)
        print("🧪 Prompt sent to OpenAI:\n", prompt[:500])
        print("🧪 GPT response:\n", response.choices[0].message.content.strip())

        return response.choices[0].message.content.strip()

    except Exception as e:
        print("🛑 OpenAI API Error:", e)
        return "⚠️ Could not get a response from GPT. Please check your API key or question."
