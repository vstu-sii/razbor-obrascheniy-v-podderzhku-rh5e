"""Safety regression: demo reset must never overwrite an unrelated database."""

import hashlib
import logging
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from reset import reset

logging.disable(logging.CRITICAL)


class ResetSafety(unittest.TestCase):
    def test_failed_seed_preserves_previous_database(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "demo.sqlite"
            reset(target)
            before = target.read_bytes()
            with patch("reset.seed", side_effect=RuntimeError("fixture failed")):
                with self.assertRaisesRegex(RuntimeError, "fixture failed"):
                    reset(target, allow_reset=True)
            self.assertEqual(target.read_bytes(), before)

    def test_unmarked_database_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "existing.sqlite"
            with closing(sqlite3.connect(target)) as db:
                db.execute("CREATE TABLE customer_data (value TEXT)")
                db.execute("INSERT INTO customer_data VALUES ('preserve me')")
                db.commit()
            before = hashlib.sha256(target.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "unmarked"):
                reset(target, allow_reset=True)
            self.assertEqual(before, hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual([], list(Path(folder).glob("*.bak")))

    def test_reset_requires_flag_and_keeps_recoverable_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "demo.sqlite"
            result = reset(target)
            self.assertEqual(result["external_model_calls"], 0)
            with self.assertRaisesRegex(ValueError, "--reset required"):
                reset(target)
            before = hashlib.sha256(target.read_bytes()).hexdigest()
            result = reset(target, allow_reset=True)
            backups = list(Path(folder).glob("*.bak"))
            self.assertEqual(1, len(backups))
            self.assertEqual(
                before, hashlib.sha256(backups[0].read_bytes()).hexdigest()
            )
            self.assertEqual(6, len(result["cases"]))
            self.assertTrue(result["operator_review_checked"])

    def test_non_database_suffix_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "document.txt"
            target.write_text("preserve me", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SQLite file required"):
                reset(target, allow_reset=True)
            self.assertEqual("preserve me", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
