"""Basic no-dependency checks for task ownership and review gates."""
import tempfile
import unittest
from pathlib import Path
from registry import TaskRegistry


class RegistryTests(unittest.TestCase):
    def test_persistence_duplicate_and_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.db"
            first = TaskRegistry(path)
            task = first.add("HVAC", "landing:hero-copy", "Create hero copy")
            same = first.add("HVAC", "landing:hero-copy", "Create hero copy again")
            self.assertEqual(task["id"], same["id"])
            second = TaskRegistry(path)
            self.assertTrue(second.claim(task["id"], "grok"))
            self.assertFalse(first.claim(task["id"], "claude"))
            self.assertFalse(second.submit_for_review(task["id"], "claude", "PR #1"))
            self.assertFalse(second.submit_for_review(task["id"], "grok", ""))
            self.assertTrue(second.submit_for_review(task["id"], "grok", "PR #1"))
            self.assertFalse(first.verify_completion(task["id"], "grok"))
            self.assertTrue(first.verify_completion(task["id"], "reviewer"))
            self.assertEqual(second.list("done")[0]["id"], task["id"])


if __name__ == "__main__":
    unittest.main()
