"""Every adversarial fixture of the parent design names unit tests that exist (plan task 14)."""

import unittest
from pathlib import Path

import yaml

INDEX = Path(__file__).resolve().parents[1] / "evals" / "adversarial" / "index.yaml"


def _test_ids(suite) -> set[str]:
    ids = set()
    for item in suite:
        ids |= _test_ids(item) if isinstance(item, unittest.TestSuite) else {item.id()}
    return ids


class AdversarialIndexTest(unittest.TestCase):
    def test_each_fixture_names_existing_tests(self):
        fixtures = yaml.safe_load(INDEX.read_text(encoding="utf-8"))["fixtures"]
        self.assertEqual(len(fixtures), 15, "the design lists 15 adversarial fixtures")
        loader = unittest.TestLoader()
        for entry in fixtures:
            with self.subTest(fixture=entry["fixture"]):
                self.assertTrue(entry["tests"])
                for name in entry["tests"]:
                    module, cls, method = name.rsplit(".", 2)
                    self.assertIn(name, _test_ids(loader.loadTestsFromName(f"{module}.{cls}")), name)


if __name__ == "__main__":
    unittest.main()
