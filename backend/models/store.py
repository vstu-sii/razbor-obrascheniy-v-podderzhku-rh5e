"""SQLite prototype storage with explicit transaction and connection lifetime."""
from contextlib import contextmanager
import sqlite3


def migrate(connection, directory):
    """Apply numbered migrations once, atomically, including existing version-1 DBs."""
    for path in sorted(directory.glob('[0-9][0-9][0-9]_*.sql')):
        version = int(path.name.split('_')[0])
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='schema_version'").fetchone()
        if exists and connection.execute('SELECT 1 FROM schema_version WHERE version=?', (version,)).fetchone():
            continue
        try:
            connection.executescript('BEGIN IMMEDIATE;\n' + path.read_text(encoding='utf-8') +
                f'\nINSERT OR IGNORE INTO schema_version VALUES ({version});\nCOMMIT;')
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

@contextmanager
def open_store(path):
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    try:
        with connection:
            yield connection
    finally:
        connection.close()
