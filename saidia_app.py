import streamlit as st
import os
import asyncio
from io import BytesIO
from s3_upload import S3UploadError, upload_to_s3
from rag_pipeline import download_file_from_s3, extract_text_from_file
from vector_store import chunk_text, embed_chunks, build_faiss_index
from qa_engine import search_index, answer_question_with_gpt

# ─── Fix asyncio issue for Windows ───
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# ─── Session Initialization ───
if "doc_ready" not in st.session_state:
    st.session_state.doc_ready = False

# ─── Page Config ───
st.set_page_config(page_title="📄 Saidia Smart Document Assistant", layout="wide")
st.title("📄 Saidia Smart Document Assistant")
st.markdown("Upload a document, extract the text, and interact with it using smart search and GPT-powered Q&A.")

# ─── Sidebar Upload ───
with st.sidebar:
    st.header("📤 Upload Document")

    # Define a safe clearing function
    def clear_session():
        keys_to_clear = [
            "doc_ready",
            "uploaded_file_name",
            "uploaded_file_data",
        ]
        for key in keys_to_clear:
            if key in st.session_state:
                del st.session_state[key]
        # No need for rerun flag - this will automatically trigger a rerun

    # Upload logic
    uploaded_file = st.file_uploader("Choose a .pdf, .txt, or .docx file", type=["pdf", "txt", "docx"])

    if uploaded_file and st.button("🚀 Process Document", key="process_btn"):
        st.session_state.doc_ready = True
        st.session_state.uploaded_file_name = uploaded_file.name
        st.session_state.uploaded_file_data = uploaded_file.read()
        st.rerun()  # Use st.rerun() instead of st.experimental_rerun()

    # Clear button with callback 
    st.button("❌ Clear Document", on_click=clear_session, key="clear_btn")

# ─── Main Workflow ───
if st.session_state.doc_ready and "uploaded_file_data" in st.session_state:
    uploaded_file = BytesIO(st.session_state.uploaded_file_data)
    uploaded_file.name = st.session_state.uploaded_file_name
    st.info(f"📁 File selected: `{uploaded_file.name}`")

    # Upload to S3
    try:
        s3_object_key = upload_to_s3(
            st.session_state.uploaded_file_data,
            st.session_state.uploaded_file_name,
        )
    except S3UploadError as exc:
        st.error(f"❌ Upload to S3 failed: {exc}")
        st.info(
            "Check the Streamlit AWS secrets, the eu-north-1 region, and the "
            "IAM permissions for the smart-doc-assistant-saidia bucket."
        )
        st.stop()
    st.success(f"✅ Uploaded `{s3_object_key}` to S3")

    # Download to temp folder
    local_path = os.path.join("temp", uploaded_file.name)
    os.makedirs("temp", exist_ok=True)
    downloaded = download_file_from_s3(uploaded_file.name, local_path)
    if not downloaded:
        st.error("❌ Download from S3 failed.")
        st.stop()
    st.success("📥 File downloaded from S3")

    # Extract text
    extracted_text = extract_text_from_file(local_path)
    st.success(f"🧾 Extracted text length: {len(extracted_text)} characters")
    if not extracted_text.strip():
        st.warning("⚠️ No text extracted.")
        st.stop()

    # Show preview
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

    # Q&A
    with st.form(key="qa_form"):
        st.subheader("💬 Ask a Question About the Document")
        user_question = st.text_input("Type your question:")
        ask_button = st.form_submit_button("Ask about this Document")

        if ask_button:
            if not user_question.strip():
                st.warning("⚠️ Please enter a question.")
            else:
                st.info("🔍 Generating answer...")
                try:
                    relevant_chunks = search_index(user_question, index, chunks)
                    answer = answer_question_with_gpt(user_question, relevant_chunks)
                    if answer:
                        st.success("✅ Answer:")
                        st.write(answer)
                    else:
                        st.warning("⚠️ GPT returned no answer.")
                except Exception as e:
                    print("🛑 Error during Q&A:", e)
                    st.error(f"❌ An error occurred: {e}")
