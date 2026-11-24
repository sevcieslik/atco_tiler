"""Regression tests for space/underscore AOI identifiers."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from modules.circuit_identity import normalized_entity_id, resolve_circuit_id


class CircuitIdentityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute(
            "CREATE TABLE entities (entity_kind TEXT, entity_id TEXT, current_revision INTEGER)"
        )
        self.db.execute(
            "INSERT INTO entities VALUES ('circuit', 'PADDLE_PRAIRIE_METIS_SETTLEMENT', 16)"
        )
        self.db.execute(
            "INSERT INTO entities VALUES ('area', 'UNRELATED_TEST_AREA', 7)"
        )

    def tearDown(self):
        self.db.close()

    def test_spaces_resolve_to_underscored_canonical_id(self):
        self.assertEqual(
            resolve_circuit_id(self.db, "PADDLE PRAIRIE METIS SETTLEMENT"),
            ("PADDLE_PRAIRIE_METIS_SETTLEMENT", 16),
        )

    def test_underscores_and_mixed_separators(self):
        for name in (
            "PADDLE_PRAIRIE_METIS_SETTLEMENT",
            "PADDLE_PRAIRIE METIS_SETTLEMENT",
            "  paddle prairie_metis  settlement  ",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    resolve_circuit_id(self.db, name),
                    ("PADDLE_PRAIRIE_METIS_SETTLEMENT", 16),
                )

    def test_no_wrong_match(self):
        with self.assertRaisesRegex(ValueError, "not registered"):
            resolve_circuit_id(self.db, "PADDLE PRAIRIE METIS OTHER")

    def test_ambiguous_normalized_names_fail_closed(self):
        self.db.execute(
            "INSERT INTO entities VALUES ('circuit', 'PADDLE PRAIRIE METIS SETTLEMENT', 18)"
        )
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            resolve_circuit_id(self.db, "paddle prairie metis settlement")

    def test_entity_kinds_not_cross_matched(self):
        with self.assertRaisesRegex(ValueError, "not registered"):
            resolve_circuit_id(self.db, "UNRELATED TEST AREA")


if __name__ == "__main__":
    unittest.main()
