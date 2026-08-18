import os

import psycopg
import streamlit as st
from dotenv import load_dotenv


load_dotenv()


def get_database_url():
    """Return the configured PostgreSQL connection URL."""

    database_url = os.getenv("DATABASE_URL")

    if database_url:
        return database_url

    try:
        database_url = st.secrets["DATABASE_URL"]
    except (KeyError, FileNotFoundError):
        database_url = None

    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured.")

    return database_url


def test_database_connection():
    """Open a short PostgreSQL connection and verify the database responds."""
    database_url = get_database_url()

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version();")
            return cursor.fetchone()[0]

def upsert_document(
    *,
    document_hash,
    original_file_name,
    s3_object_key,
    content_type=None,
    size_bytes=None,
    document_kind=None,
    extraction_method=None,
    used_vision=False,
    page_count=None,
    extracted_word_count=None,
    extracted_character_count=None,
    processing_status="uploaded",
    processing_error=None,
):
    """Insert a document record, or update it if the document hash already exists."""

    database_url = get_database_url()

    query = """
        insert into documents (
            document_hash,
            original_file_name,
            s3_object_key,
            content_type,
            size_bytes,
            document_kind,
            extraction_method,
            used_vision,
            page_count,
            extracted_word_count,
            extracted_character_count,
            processing_status,
            processing_error,
            processed_at
        )
        values (
            %(document_hash)s,
            %(original_file_name)s,
            %(s3_object_key)s,
            %(content_type)s,
            %(size_bytes)s,
            %(document_kind)s,
            %(extraction_method)s,
            %(used_vision)s,
            %(page_count)s,
            %(extracted_word_count)s,
            %(extracted_character_count)s,
            %(processing_status)s,
            %(processing_error)s,
            case
                when %(processing_status)s = 'processed'
                then now()
                else null
            end
        )
        on conflict (document_hash)
        do update set
            original_file_name = excluded.original_file_name,
            s3_object_key = excluded.s3_object_key,
            content_type = excluded.content_type,
            size_bytes = excluded.size_bytes,
            document_kind = excluded.document_kind,
            extraction_method = excluded.extraction_method,
            used_vision = excluded.used_vision,
            page_count = excluded.page_count,
            extracted_word_count = excluded.extracted_word_count,
            extracted_character_count = excluded.extracted_character_count,
            processing_status = excluded.processing_status,
            processing_error = excluded.processing_error,
            processed_at = excluded.processed_at
        returning id;
    """

    params = {
        "document_hash": document_hash,
        "original_file_name": original_file_name,
        "s3_object_key": s3_object_key,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "document_kind": document_kind,
        "extraction_method": extraction_method,
        "used_vision": used_vision,
        "page_count": page_count,
        "extracted_word_count": extracted_word_count,
        "extracted_character_count": extracted_character_count,
        "processing_status": processing_status,
        "processing_error": processing_error,
    }

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            document_id = cursor.fetchone()[0]
            connection.commit()

    return document_id