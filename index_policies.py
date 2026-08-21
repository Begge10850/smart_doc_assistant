from database import (
    get_database_url,
    policy_has_embeddings,
    save_policy_chunks,
)
from vector_store import chunk_text, embed_chunks

import psycopg


EMBEDDING_MODEL = "sentence-transformers/all-mpnet-base-v2"


def load_policies():
    """Load policy IDs and full policy text from PostgreSQL."""

    database_url = get_database_url()

    query = """
        select id, policy_id, title, policy_text
        from carrier_policies
        order by id;
    """

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            return cursor.fetchall()


def index_policy(policy_db_id, policy_code, title, policy_text):
    """Chunk, embed, and save one policy if it is not already indexed."""

    if policy_has_embeddings(policy_db_id):
        print(f"Skipping {policy_code}: embeddings already exist.")
        return

    chunks = chunk_text(policy_text)

    if not chunks:
        print(f"Skipping {policy_code}: no chunks were created.")
        return

    embeddings = embed_chunks(chunks)

    save_policy_chunks(
        policy_id=policy_db_id,
        chunks=chunks,
        embeddings=embeddings,
        embedding_model=EMBEDDING_MODEL,
    )

    print(
        f"Indexed {policy_code} - {title}: "
        f"{len(chunks)} chunk(s), embedding shape {embeddings.shape}"
    )


def main():
    policies = load_policies()

    print(f"Found {len(policies)} carrier policies.")

    for policy_db_id, policy_code, title, policy_text in policies:
        index_policy(
            policy_db_id,
            policy_code,
            title,
            policy_text,
        )


if __name__ == "__main__":
    main()