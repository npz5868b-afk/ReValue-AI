"""Offline SQLite/PostgreSQL-adapter safety regression checks (no cloud credentials)."""
from __future__ import annotations

from contextlib import contextmanager, closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from backend.src import report_sharing as rs

SAMPLE = {"device_name": "Samsung Galaxy S24 Ultra", "market_value": "RM2,250–RM2,600"}


class FakePostgresError(Exception):
    pass


class FakePostgresConnection:
    """SQLite-backed test double checking Psycopg parameter placeholders."""

    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, typ, value, tb):
        try:
            if typ is None:
                self.db.commit()
            else:
                self.db.rollback()
        finally:
            self.db.close()

    def execute(self, sql: str, params=()):
        self.queries.append((sql, params))
        if "?" in sql:
            raise AssertionError("PostgreSQL query must use %s, not ?")
        return self.db.execute(sql.replace("%s", "?"), params)


class FakePsycopg:
    Error = FakePostgresError

    def __init__(self, dbfile):
        self.dbfile = dbfile
        self.kwargs = []
        self.connections = []
        self.fail = False

    def connect(self, url, **kwargs):
        if self.fail:
            raise FakePostgresError("temporary server outage")
        if not url.startswith("postgresql://"):
            raise AssertionError("unexpected URL")
        self.kwargs.append(kwargs)
        connection = FakePostgresConnection(self.dbfile)
        self.connections.append(connection)
        return connection


class StorageSelectionTests(unittest.TestCase):
    def test_sqlite_roundtrip_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"REVALUE_REPORT_DATABASE_URL": ""}):
                with patch.object(rs, "REPORT_DB", Path(directory) / "reports.sqlite3"):
                    token, expires, snap = rs.save_report(SAMPLE)
                    self.assertEqual(rs.load_report(token)["device_name"], SAMPLE["device_name"])
                    self.assertGreater(expires, 0)
                    self.assertIsNone(rs.load_report("invalid"))

    def test_postgres_sql_roundtrip_and_security_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = FakePsycopg(str(Path(directory) / "fake-postgres.sqlite3"))
            with patch.dict(sys.modules, {"psycopg": fake}):
                with patch.dict(os.environ, {"REVALUE_REPORT_DATABASE_URL": "postgresql://user:secret@example.invalid:5432/postgres"}):
                    token, expires, _ = rs.save_report(SAMPLE)
                    loaded = rs.load_report(token)
                    self.assertEqual(loaded["market_value"], SAMPLE["market_value"])
                    self.assertEqual(len(token), 32)
                    self.assertGreater(expires, 0)
                    self.assertEqual(fake.kwargs[0]["sslmode"], "require")
                    self.assertIs(fake.kwargs[0]["prepare_threshold"], None)
                    self.assertIn("%s", " ".join(q[0] for conn in fake.connections for q in conn.queries))
                    self.assertFalse((Path(directory) / "reports.sqlite3").exists())

    def test_bad_postgres_url_never_falls_back_to_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "fallback.sqlite3"
            with patch.object(rs, "REPORT_DB", file):
                with patch.dict(os.environ, {"REVALUE_REPORT_DATABASE_URL": "invalid-secret"}):
                    with self.assertRaisesRegex(rs.ReportStorageUnavailable, "report_storage_configuration_invalid"):
                        rs.save_report(SAMPLE)
            self.assertFalse(file.exists())

    def test_postgres_outage_never_falls_back_to_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "fallback.sqlite3"
            fake = FakePsycopg(str(Path(directory) / "fake-postgres.sqlite3"))
            fake.fail = True
            with patch.object(rs, "REPORT_DB", file):
                with patch.dict(sys.modules, {"psycopg": fake}):
                    with patch.dict(os.environ, {"REVALUE_REPORT_DATABASE_URL": "postgresql://user:secret@example.invalid/postgres"}):
                        with self.assertRaisesRegex(rs.ReportStorageUnavailable, "report_database_unavailable") as ctx:
                            rs.save_report(SAMPLE)
                        self.assertNotIn("secret", str(ctx.exception))
            self.assertFalse(file.exists())

    def test_expired_tokens_remain_inaccessible(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"REVALUE_REPORT_DATABASE_URL": ""}):
                with patch.object(rs, "REPORT_DB", Path(directory) / "reports.sqlite3"):
                    token, expires, _ = rs.save_report(SAMPLE)
                    # sqlite3.Connection.__exit__ commits/rolls back but DOES NOT close.
                    # Explicitly close before TemporaryDirectory cleanup (Windows locks files).
                    with closing(sqlite3.connect(rs.REPORT_DB)) as db:
                        with db:
                            db.execute("UPDATE reports SET expires = 1 WHERE token = ?", (token,))
                    self.assertIsNone(rs.load_report(token))


if __name__ == "__main__":
    unittest.main(verbosity=2)
