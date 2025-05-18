import os
import numpy as np
from sentence_transformers import SentenceTransformer
from openai import OpenAI
import streamlit as st

# Load OpenAI API key
try:
    api_key = st.secrets["openai"]["OPENAI_API_KEY"]
except Exception:
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")

# Initialize OpenAI client
client = OpenAI(api_key=api_key)

# Load shared embedding model (more accurate)
model = SentenceTransformer("all-mpnet-base-v2")

# FAISS vector search
def search_index(user_question, index, chunks, top_k=3):
    question_embedding = model.encode([user_question])
    D, I = index.search(np.array(question_embedding), top_k)
    matched_chunks = [chunks[i] for i in I[0]]
    return matched_chunks

# Ask GPT using OpenAI client
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
        return "⚠️ Could not get a response from GPT. Check your API key or usage limits."
