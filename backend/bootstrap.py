"""Generate a persistent local demo operator token before starting the HTTP server."""
import os
from pathlib import Path
import secrets
import sys


def operator_token(directory):
    """Explicit environment token wins; otherwise reuse a random token on the volume."""
    configured = os.environ.get('DEMO_OPERATOR_TOKEN', '').strip()
    if configured:
        return configured
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if database_url:
        return database_token(database_url)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'operator-token'
    try:
        with path.open('x', encoding='utf-8') as output:
            os.chmod(path, 0o600)
            output.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    value = path.read_text(encoding='utf-8').strip()
    if len(value) < 32:
        raise RuntimeError('Invalid persisted operator token; configure DEMO_OPERATOR_TOKEN explicitly')
    return value


def database_token(database_url):
    """Keep generated credentials stable on an ephemeral hosting filesystem."""
    import psycopg
    try:
        with psycopg.connect(database_url, connect_timeout=10) as connection:
            connection.execute('SELECT pg_advisory_xact_lock(310011)')
            connection.execute('CREATE TABLE IF NOT EXISTS app_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL)')
            connection.execute("INSERT INTO app_settings VALUES ('operator_token', %s) ON CONFLICT(setting_key) DO NOTHING", (secrets.token_urlsafe(32),))
            value = connection.execute("SELECT setting_value FROM app_settings WHERE setting_key='operator_token'").fetchone()[0]
            if len(value) < 32:
                raise RuntimeError('Invalid persisted operator token; configure DEMO_OPERATOR_TOKEN explicitly')
            return value
    except psycopg.Error:
        raise RuntimeError('PostgreSQL startup failed; check DATABASE_URL in hosting settings') from None


if __name__ == '__main__':
    os.environ['DEMO_OPERATOR_TOKEN'] = operator_token(os.environ.get('TOKEN_DIRECTORY', '/data'))
    module = os.environ.get('APP_FACTORY', 'docs.implementation.lab03.backend.main:create_app')
    port = int(os.environ.get('PORT', '8000'))
    if not 1 <= port <= 65535:
        raise RuntimeError('PORT must be between 1 and 65535')
    os.execvp(sys.executable, [sys.executable, '-m', 'uvicorn', module, '--factory', '--host', '0.0.0.0', '--port', str(port)])
