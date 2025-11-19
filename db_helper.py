from contextlib import contextmanager
import sqlite3

@contextmanager
def get_db_connection():
    conn = sqlite3.connect('gre_practice.db')
    try:
        yield conn
    finally:
        conn.close()
