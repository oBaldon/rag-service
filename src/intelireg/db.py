from contextlib import contextmanager
import psycopg
from .config import get_database_url

@contextmanager
def get_conn():
    # autocommit False por padrão; vamos controlar transação onde precisa
    conn = psycopg.connect(get_database_url())
    try:
        yield conn
    finally:
        conn.close()
