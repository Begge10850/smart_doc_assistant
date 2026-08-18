import os

import psycopg
from dotenv import load_dotenv


load_dotenv()


def get_database_url():
    """Return the configured PostgreSQL connection URL."""
    database_url = os.getenv("DATABASE_URL")

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