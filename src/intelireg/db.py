from contextlib import contextmanager
import psycopg
from psycopg import sql
from .config import get_database_url
from . import settings

@contextmanager
def get_conn():
    # autocommit False por padrão; vamos controlar transação onde precisa
    conn = psycopg.connect(get_database_url(), connect_timeout=10)
    try:
        # Persistir na sessão (não só na primeira transação). Há também public.jobs.
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SET search_path TO {}, public").format(
                    sql.Identifier(settings.PG_SCHEMA)
                )
            )
        conn.commit()
        yield conn
    finally:
        conn.close()
