import os
import numpy as np
from sentence_transformers import SentenceTransformer
import openai
import streamlit as st

# Load OpenAI key
try:
    openai.api_key = st.secrets["openai"]["OPENAI_API_KEY"]
except Exception:
    from dotenv import load_dotenv
    load_dotenv()
    openai.api_key = os.getenv("OPENAI_API_KEY")

# Confirm load (optional)
print("🔐 OpenAI key loaded:", openai.api_key[:6] + "...")

# Embed model
model = SentenceTransformer("all-MiniLM-L6-v2")

# FAISS search
def search_index(user_question, index, chunks, top_k=3):
    question_embedding = model.encode([user_question])
    D, I = index.search(np.array(question_embedding), top_k)
    matched_chunks = [chunks[i] for i in I[0]]
    return matched_chunks

# GPT Answer
def answer_question_with_gpt(question, context_chunks):
    prompt = f"""You are a helpful assistant. Use the following document snippets to answer the user's question.

Document Snippets:
{''.join(['- ' + chunk + '\\n' for chunk in context_chunks])}

Question: {question}
Answer:"""

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500
        )

        # Debug (view logs in Streamlit Cloud)
        print("🧪 Prompt sent:", prompt[:400])
        print("🧪 GPT response:", response.choices[0].message.content[:300])

        return response.choices[0].message.content.strip()

    except Exception as e:
        print("🛑 OpenAI API Error:", e)
        return "⚠️ Could not get a response from GPT. Check key or quota."
