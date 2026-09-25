import os
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from contextlib import contextmanager

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/todolist")

# Connection pool for efficient thread-safe resource handling
pool = None

def init_pool():
    global pool
    if pool is None:
        pool = SimpleConnectionPool(1, 10, dsn=DATABASE_URL)

def close_pool():
    global pool
    if pool:
        pool.closeall()

@contextmanager
def get_db_connection():
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)