import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from plans import load_plans


class PlanTests(unittest.TestCase):
    def test_live_reads_and_limits_preserve_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / "focus.md"
            plan.write_text("A" * 400)
            (root / "plans.json").write_text(json.dumps({"sources": [
                {"label": "Focus", "path": str(plan), "max_chars": 200}]}))
            first = load_plans(root)["sources"][0]["documents"][0]
            self.assertEqual(first["path"], str(plan))
            self.assertTrue(first["truncated"])
            self.assertEqual(len(first["text"]), 200)
            plan.write_text("New priority")
            second = load_plans(root)["sources"][0]["documents"][0]
            self.assertEqual(second["text"], "New priority")
            self.assertFalse(second["truncated"])

    def test_worker_never_reads_personal_sources(self):
        with patch("plans.subprocess.run") as run:
            self.assertEqual(load_plans("/missing", worker=True)["sources"], [])
            run.assert_not_called()

    def test_missing_and_invalid_sources_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "plans.json"
            config.write_text(json.dumps({"sources": [{"path": str(root / "missing.md")}]}))
            self.assertEqual(load_plans(root)["sources"][0]["error"], "No matching plan files")
            config.write_text('{"sources": [null]}')
            self.assertIn("error", load_plans(root))

    def test_remote_failure_does_not_use_stale_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plans.json").write_text(json.dumps({"sources": [
                {"host": "user@host", "path": "/plans/*.md"}]}))
            with patch("plans.subprocess.run", side_effect=OSError()) as run:
                self.assertEqual(load_plans(root)["sources"][0]["error"], "Plan source unavailable")
                command = run.call_args.args[0]
                self.assertIn("ForwardAgent=no", command)
                self.assertIn("StrictHostKeyChecking=yes", command)
