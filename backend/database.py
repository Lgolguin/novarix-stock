from contextlib import contextmanager
from pathlib import Path
import sqlite3


class Repository:
    """Separate storage boundary; replace this repository for a server DB later."""
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS installations (
                installation_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL,
                license_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'FREE',
                ever_active INTEGER NOT NULL DEFAULT 0, expires_at TEXT,
                validated_at TEXT, revision INTEGER NOT NULL DEFAULT 0,
                current_reference TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
                reference TEXT PRIMARY KEY, installation_id TEXT NOT NULL,
                mp_id TEXT UNIQUE, state TEXT NOT NULL DEFAULT 'creating',
                amount TEXT NOT NULL, checkout TEXT,
                FOREIGN KEY(installation_id) REFERENCES installations(installation_id))''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(subscriptions)')}
            if 'payer_email' not in columns:
                db.execute('ALTER TABLE subscriptions ADD COLUMN payer_email TEXT')
            if 'payer_id' not in columns:
                db.execute('ALTER TABLE subscriptions ADD COLUMN payer_id TEXT')
            db.execute('CREATE TABLE IF NOT EXISTS webhook_events (event_key TEXT PRIMARY KEY, received_at TEXT NOT NULL)')

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

