import asyncio
import hashlib
import os
from pathlib import Path

import streamlit as st

from qa_engine import answer_question_with_gpt, search_index
from rag_pipeline import download_file_from_s3, extract_text_from_file
from s3_upload import S3UploadError, upload_to_s3
from vector_store import build_faiss_index, chunk_text, embed_chunks


# Fix asyncio initialization for environments that do not provide a loop.
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())


DOCUMENT_STATE_KEYS = [
    "processing_requested",
    "uploaded_file_name",
    "uploaded_file_data",
    "processed_doc_hash",
    "processed_file_name",
    "s3_object_key",
    "extracted_text",
    "chunks",
    "vector_index",
    "chat_messages",
]


def clear_document_session():
    """Forget the processed document, vector index, and its conversation."""
    for key in DOCUMENT_STATE_KEYS:
        st.session_state.pop(key, None)


def retrieval_query(question, history):
    """Include recent user turns so follow-up questions retrieve useful chunks."""
    recent_user_turns = [
        message["content"]
        for message in history[-6:]
        if message.get("role") == "user"
    ]
    return "\n".join([*recent_user_turns[-2:], question])


st.set_page_config(page_title="Saidia Smart Document Assistant", layout="wide")
st.title("📄 Saidia Smart Document Assistant")
st.markdown(
    "Upload a document once, then continue a grounded conversation about it."
)


with st.sidebar:
    st.header("📤 Upload Document")
    uploaded_file = st.file_uploader(
        "Choose a .pdf, .txt, or .docx file",
        type=["pdf", "txt", "docx"],
    )

    if uploaded_file and st.button("🚀 Process Document", key="process_btn"):
        file_data = uploaded_file.getvalue()
        document_hash = hashlib.sha256(file_data).hexdigest()

        if (
            document_hash == st.session_state.get("processed_doc_hash")
            and st.session_state.get("vector_index") is not None
        ):
            st.info("This document is already processed for the current session.")
        else:
            clear_document_session()
            st.session_state.uploaded_file_name = Path(uploaded_file.name).name
            st.session_state.uploaded_file_data = file_data
            st.session_state.processing_requested = True
            st.rerun()

    st.button(
        "❌ Clear Document",
        on_click=clear_document_session,
        key="clear_btn",
    )


# This block runs only for a newly selected document. Once complete, later
# Streamlit reruns reuse the objects stored in session_state.
if st.session_state.get("processing_requested"):
    file_name = st.session_state.uploaded_file_name
    file_data = st.session_state.uploaded_file_data
    document_hash = hashlib.sha256(file_data).hexdigest()

    st.info(f"📁 Processing `{file_name}` for the first time in this session.")

    try:
        with st.status("Preparing document...", expanded=True) as status:
            status.write("Uploading the original document to S3...")
            s3_object_key = upload_to_s3(file_data, file_name)

            local_path = os.path.join("temp", s3_object_key)
            os.makedirs("temp", exist_ok=True)

            status.write("Downloading the stored document for processing...")
            if not download_file_from_s3(s3_object_key, local_path):
                raise RuntimeError("The document could not be downloaded from S3.")

            status.write("Extracting text...")
            extracted_text = extract_text_from_file(local_path)
            if not extracted_text.strip():
                raise RuntimeError("No text could be extracted from the document.")

            status.write("Creating document chunks and embeddings...")
            chunks = chunk_text(extracted_text)
            if not chunks:
                raise RuntimeError("The extracted document produced no text chunks.")

            embeddings = embed_chunks(chunks)
            vector_index = build_faiss_index(embeddings)

            st.session_state.processed_doc_hash = document_hash
            st.session_state.processed_file_name = file_name
            st.session_state.s3_object_key = s3_object_key
            st.session_state.extracted_text = extracted_text
            st.session_state.chunks = chunks
            st.session_state.vector_index = vector_index
            st.session_state.chat_messages = []
            st.session_state.processing_requested = False

            status.update(label="Document ready for conversation", state="complete")

        st.rerun()
    except S3UploadError as exc:
        st.session_state.processing_requested = False
        st.error(f"❌ Upload to S3 failed: {exc}")
        st.stop()
    except Exception as exc:
        st.session_state.processing_requested = False
        st.error(f"❌ Document processing failed: {exc}")
        st.stop()


if st.session_state.get("processed_doc_hash"):
    file_name = st.session_state.processed_file_name
    extracted_text = st.session_state.extracted_text
    chunks = st.session_state.chunks
    vector_index = st.session_state.vector_index
    chat_messages = st.session_state.setdefault("chat_messages", [])

    st.success(f"✅ `{file_name}` is processed and cached for this session.")
    col1, col2 = st.columns(2)
    col1.metric("Extracted characters", f"{len(extracted_text):,}")
    col2.metric("Searchable chunks", len(chunks))

    with st.expander("🧠 Preview extracted text", expanded=False):
        st.text_area(
            "Document text",
            extracted_text,
            height=300,
            disabled=True,
            key=f"preview_{st.session_state.processed_doc_hash}",
        )

    st.subheader("💬 Chat with this document")
    if not chat_messages:
        st.caption(
            "The document will not be uploaded or embedded again while this session remains active."
        )

    for message in chat_messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input("Ask a question about the document")
    if question:
        prior_history = list(chat_messages)
        chat_messages.append({"role": "user", "content": question})

        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("Searching the document and preparing an answer..."):
                try:
                    search_query = retrieval_query(question, prior_history)
                    relevant_chunks = search_index(
                        search_query,
                        vector_index,
                        chunks,
                    )
                    answer = answer_question_with_gpt(
                        question,
                        relevant_chunks,
                        chat_history=prior_history,
                    )
                except Exception as exc:
                    answer = f"I could not answer that question: {exc}"

            st.markdown(answer)
            chat_messages.append({"role": "assistant", "content": answer})
else:
    st.info("Upload a document and select **Process Document** to begin.")
