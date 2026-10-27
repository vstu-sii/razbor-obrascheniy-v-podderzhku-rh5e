"""SQLite local mode and PostgreSQL/Neon mode with the same repository queries."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3


class PostgresStore:
    dialect = 'postgresql'

    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, parameters=()):
        # Only fixed repository SQL uses qmark placeholders. User text is always bound.
        return self.connection.execute(query.replace('?', '%s'), parameters)


@contextmanager
def open_store(target):
    if str(target).startswith(('postgresql://', 'postgres://')):
        import psycopg
        from psycopg.rows import dict_row
        try:
            connection = psycopg.connect(str(target), row_factory=dict_row, connect_timeout=10)
        except psycopg.OperationalError:
            # Never include a DSN, username or password in the public/logged exception.
            raise RuntimeError('PostgreSQL connection failed; check DATABASE_URL in hosting settings') from None
        try:
            with connection:
                yield PostgresStore(connection)
        except psycopg.IntegrityError:
            # Preserve the existing repository API's conflict handling across drivers.
            raise sqlite3.IntegrityError('PostgreSQL integrity constraint') from None
        finally:
            connection.close()
    else:
        connection = sqlite3.connect(target, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def initialize(connection, directory):
    if getattr(connection, 'dialect', None) == 'postgresql':
        # Transaction-level lock works with pooled Neon connections too.
        connection.execute('SELECT pg_advisory_xact_lock(310010)')
        connection.execute('CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)')
        applied = {row['version'] for row in connection.execute('SELECT version FROM schema_version').fetchall()}
        for source in sorted((Path(directory)/'postgresql').glob('[0-9][0-9][0-9]_*.sql')):
            version = int(source.name.split('_')[0])
            if version not in applied:
                connection.connection.execute(source.read_text(encoding='utf-8'))
                connection.execute('INSERT INTO schema_version(version) VALUES (?)', (version,))
    else:
        migrate(connection, Path(directory))


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
