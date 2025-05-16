import streamlit as st
import os
import asyncio
from s3_upload import upload_to_s3
from rag_pipeline import download_file_from_s3, extract_text_from_file
from vector_store import chunk_text, embed_chunks, build_faiss_index
from qa_engine import search_index, answer_question_with_gpt

# ─── Fix asyncio loop issue (for Windows + Streamlit) ───
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# ─── Page Setup ───
st.set_page_config(page_title="📄 Saidia Smart Document Assistant", layout="wide")
st.title("📄 Saidia Smart Document Assistant")
st.markdown("Upload a document, extract the text, and interact with it using smart search and GPT-powered Q&A.")

# ─── Sidebar: Upload Document ───
with st.sidebar:
    st.header("📤 Upload Document")
    uploaded_file = st.file_uploader("Choose a .pdf, .txt, or .docx file", type=["pdf", "txt", "docx"])
    process_triggered = st.button("🚀 Process Document")

# ─── Main Workflow ───
if uploaded_file and process_triggered:
    st.info(f"📁 File selected: `{uploaded_file.name}` ({uploaded_file.type})")

    # Upload to S3
    result = upload_to_s3(uploaded_file)
    if not result:
        st.error("❌ Upload to S3 failed.")
    else:
        st.success("✅ Uploaded to S3")

        # Download to temp folder
        local_path = os.path.join("temp", uploaded_file.name)
        os.makedirs("temp", exist_ok=True)
        downloaded = download_file_from_s3(uploaded_file.name, local_path)

        if downloaded:
            st.success("📥 File downloaded from S3")

            # Extract text
            extracted_text = extract_text_from_file(local_path)
            st.success(f"🧾 Extracted DOCX content length: {len(extracted_text)} characters")

            if not extracted_text.strip():
                st.warning("⚠️ No text extracted.")
            else:
                # Preview text
                with st.expander("🧠 Preview Extracted Text", expanded=False):
                    st.text_area("Document Text", extracted_text, height=300)

                # Chunking
                chunks = chunk_text(extracted_text)
                st.info(f"📄 Document split into **{len(chunks)}** chunks.")

                # Embedding
                embeddings = embed_chunks(chunks)
                st.success("🔢 Chunks embedded.")

                # FAISS Index
                index = build_faiss_index(embeddings)
                st.success("📦 Vector index created and ready.")

                # Q&A Section
                with st.form(key="qa_form"):
                    st.subheader("💬 Ask a Question About the Document")
                    user_question = st.text_input("Type your question:")
                    ask_button = st.form_submit_button("Ask GPT")

                    if ask_button and user_question:
                        st.info("🔍 Searching and generating answer...")
                        relevant_chunks = search_index(user_question, index, chunks)
                        answer = answer_question_with_gpt(user_question, relevant_chunks)

                        if answer:
                            st.success("✅ Answer:")
                            st.write(answer)
                        else:
                            st.warning("⚠️ GPT returned no answer.")
