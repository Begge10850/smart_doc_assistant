import streamlit as st
import os
import asyncio
import requests

from s3_upload import upload_to_s3
from rag_pipeline import download_file_from_s3, extract_text_from_file
from vector_store import chunk_text, embed_chunks, build_faiss_index
from qa_engine import search_index, answer_question_with_gpt

# Fix RuntimeError for torch + Streamlit on Windows
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# ─── OCR SPACE HELPER ──────────────────────────────────────────────────────────
def ocr_space_file(filepath: str) -> str:
    """
    Send file to OCR.Space API and return the extracted text.
    """
    api_key = st.secrets["ocr"]["OCR_API_KEY"]
    with open(filepath, "rb") as f:
        response = requests.post(
            url="https://api.ocr.space/parse/image",
            files={"file": f},
            data={
                "apikey": api_key,
                "language": "eng",
            },
        )
    result = response.json()
    if result.get("IsErroredOnProcessing"):
        # Show the first error message if any
        err = result.get("ErrorMessage", ["Unknown error"])[0]
        return f"❌ OCR Error: {err}"
    return result["ParsedResults"][0]["ParsedText"]


# ─── PAGE SETUP ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="📄 Saidia Smart Document Assistant", layout="wide"
)
st.title("📄 Saidia Smart Document Assistant")
st.markdown(
    "Upload a document, extract the text (with OCR fallback), and interact with it using smart search and GPT-powered Q&A."
)

# ─── SIDEBAR UPLOAD ───────────────────────────────────────────────────────────
with st.sidebar:
    st.header("📤 Upload Document")
    uploaded_file = st.file_uploader(
        "Choose a .pdf, .txt, or .docx file", type=["pdf", "txt", "docx"]
    )
    process_triggered = st.button("🚀 Process Document")

# ─── PROCESSING ───────────────────────────────────────────────────────────────
if uploaded_file and process_triggered:
    st.info(f"📁 File selected: `{uploaded_file.name}` ({uploaded_file.type})")

    # 1) Upload to S3
    if not upload_to_s3(uploaded_file):
        st.error("❌ Upload to S3 failed.")
        st.stop()
    st.success("✅ Uploaded to S3")

    # 2) Download back locally for processing
    local_dir = "temp"
    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, uploaded_file.name)
    if not download_file_from_s3(uploaded_file.name, local_path):
        st.error("❌ Download from S3 failed.")
        st.stop()
    st.success("📥 File downloaded from S3")

    # 3) Extract text via your pipeline
    extracted_text = extract_text_from_file(local_path)
    st.write(f"🧾 Extracted content length: {len(extracted_text)} characters")

    # 4) OCR fallback if no text found
    if not extracted_text.strip():
        st.warning("⚠️ No text extracted via pipeline, using OCR fallback...")
        extracted_text = ocr_space_file(local_path)
        st.write(f"🧾 OCR-extracted content length: {len(extracted_text)} characters")

    # 5) Preview and proceed only if we have text
    if not extracted_text.strip():
        st.error("❌ Still no text found. Please try a different document.")
        st.stop()

    with st.expander("🧠 Preview Extracted Text", expanded=False):
        st.text_area("Document Text", extracted_text, height=300)

    # 6) Chunking
    chunks = chunk_text(extracted_text)
    st.info(f"📄 Document split into **{len(chunks)}** chunks.")

    # 7) Embedding
    embeddings = embed_chunks(chunks)
    st.success("🔢 Chunks embedded.")

    # 8) Build FAISS Index
    index = build_faiss_index(embeddings)
    st.success("📦 Vector index created and ready.")

    # 9) Q&A form
    with st.form(key="qa_form"):
        st.subheader("💬 Ask a Question About the Document")
        user_question = st.text_input("Type your question here:")
        ask_button = st.form_submit_button("Ask GPT")

        if ask_button and user_question:
            st.info("🔍 Searching relevant chunks and preparing answer...")
            relevant_chunks = search_index(user_question, index, chunks)
            answer = answer_question_with_gpt(user_question, relevant_chunks)
            st.success("✅ Answer:")
            st.write(answer)
