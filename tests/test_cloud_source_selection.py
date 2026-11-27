"""Regression: multiple AOIs must not merge LAS/LAZ owned by other circuits."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "modules"))
from cloud_source_selection import select_entity_clouds


class CloudOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def create(self, relative):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        return p

    def test_flat_named_files_isolate_circuits(self):
        expected = self.create("9L66_0018_v1.laz")
        self.create("9L137_0018_v1.las")
        got, mode = select_entity_clouds(self.root, "9L66", ["9L66", "9L137"])
        self.assertEqual(got, [expected])
        self.assertEqual(mode, "entity-specific")

    def test_named_folders_isolate_circuits(self):
        expected = self.create("9L66/tiled/0018.laz")
        self.create("9L137/tiled/0018.laz")
        got, mode = select_entity_clouds(self.root, "9L66", ["9L66", "9L137"])
        self.assertEqual(got, [expected])
        self.assertEqual(mode, "entity-specific")

    def test_fully_unlabelled_shared_root_for_all_aois(self):
        files = [self.create("tile_1.laz"), self.create("tile_2.las")]
        for entity in ("9L66", "9L137"):
            got, mode = select_entity_clouds(self.root, entity, ["9L66", "9L137"])
            self.assertEqual(got, files)
            self.assertEqual(mode, "shared")

    def test_mixed_rejects_unlabelled_to_avoid_double_counting(self):
        owned = self.create("9L66/0018.laz")
        self.create("9L137/0018.laz")
        self.create("unspecified_tile.las")
        got, _ = select_entity_clouds(self.root, "9L66", ["9L66", "9L137"])
        self.assertEqual(got, [owned])

    def test_no_matching_tagged_source_is_error(self):
        self.create("9L137_0018.laz")
        with self.assertRaisesRegex(ValueError, "No circuit-specific"):
            select_entity_clouds(self.root, "9L66", ["9L66", "9L137"])

    def test_partial_id_not_a_match(self):
        self.create("9L660_0018.laz")
        self.create("9L66_0018.laz")
        got, _ = select_entity_clouds(self.root, "9L66", ["9L66", "9L660"])
        self.assertEqual([p.name for p in got], ["9L66_0018.laz"])

    def test_nested_filename_match(self):
        owned = self.create("production/9L66_0018_v1.laz")
        self.create("production/9L137_0018_v1.laz")
        got, _ = select_entity_clouds(self.root, "9L66", ["9L66", "9L137"])
        self.assertEqual(got, [owned])


if __name__ == "__main__":
    unittest.main()
