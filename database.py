import os

import psycopg
import streamlit as st
from dotenv import load_dotenv
from pgvector import Vector

from pgvector.psycopg import register_vector


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
    extracted_text=None,
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
            extracted_text,
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
            %(extracted_text)s,
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
            extracted_text = excluded.extracted_text,
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
        "extracted_text": extracted_text,
        "processing_status": processing_status,
        "processing_error": processing_error,
    }

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            document_id = cursor.fetchone()[0]
            connection.commit()

    return document_id


def save_document_chunks(
    *,
    document_id,
    chunks,
    embeddings,
    embedding_model,
):
    """Persist document chunks and their embeddings in PostgreSQL."""

    if len(chunks) != len(embeddings):
        raise ValueError(
            "The number of chunks must match the number of embeddings."
        )

    database_url = get_database_url()

    query = """
        insert into document_chunks (
            document_id,
            chunk_index,
            chunk_text,
            character_count,
            embedding,
            embedding_model
        )
        values (
            %(document_id)s,
            %(chunk_index)s,
            %(chunk_text)s,
            %(character_count)s,
            %(embedding)s,
            %(embedding_model)s
        )
        on conflict (document_id, chunk_index)
        do update set
            chunk_text = excluded.chunk_text,
            character_count = excluded.character_count,
            embedding = excluded.embedding,
            embedding_model = excluded.embedding_model;
    """

    with psycopg.connect(database_url) as connection:
        register_vector(connection)

        with connection.cursor() as cursor:
            for chunk_index, (chunk, embedding) in enumerate(
                zip(chunks, embeddings)
            ):
                cursor.execute(
                    query,
                    {
                        "document_id": document_id,
                        "chunk_index": chunk_index,
                        "chunk_text": chunk,
                        "character_count": len(chunk),
                        "embedding": Vector(embedding),
                        "embedding_model": embedding_model,
                    },
                )

        connection.commit()

def search_document_chunks(
    *,
    document_id,
    query_embedding,
    limit=4,
):
    """Return the document chunks most semantically similar to a query."""

    database_url = get_database_url()

    query = """
        select
            chunk_index,
            chunk_text,
            1 - (embedding <=> %(query_embedding)s) as similarity
        from document_chunks
        where document_id = %(document_id)s
          and embedding is not null
        order by embedding <=> %(query_embedding)s
        limit %(limit)s;
    """

    with psycopg.connect(database_url) as connection:
        register_vector(connection)

        with connection.cursor() as cursor:
            cursor.execute(
                query,
                {
                    "document_id": document_id,
                    "query_embedding": Vector(query_embedding),
                    "limit": limit,
                },
            )

            rows = cursor.fetchall()

    return rows