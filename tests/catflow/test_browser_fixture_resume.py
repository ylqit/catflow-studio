"""Offline resume crash-window validation; run with unittest, no PostgreSQL needed."""

import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from browser_edit_fixture import ROOT, validate_resume_receipts
from catflow_worker.provider_receipts import ReceiptJournal


class ResumeReceiptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / "output/playwright")
        self.addCleanup(self.directory.cleanup)
        self.journal = ReceiptJournal(Path(self.directory.name))
        self.job = SimpleNamespace(
            id=uuid.uuid4(),
            status="submission_unknown",
            execution_json={},
            provider_task_id=None,
            provider_response_id=None,
        )

    def test_unknown_unregistered_receipts_are_preserved_and_rejected(self):
        for document in (
            {"taskId": "fixture-task"},
            {"complete": True},
            {"responseId": "response", "store": True},
            {"serverRequestId": "trace"},
        ):
            with self.subTest(document=document):
                self.job.id = uuid.uuid4()
                receipt = self.journal.append(self.job.id, document)
                with self.assertRaisesRegex(ValueError, "explicit recovery required"):
                    validate_resume_receipts([self.job], self.journal, {})
                self.assertIn(receipt, self.journal.read(self.job.id))

    def test_registered_nonrecoverable_history_is_allowed(self):
        receipt = self.journal.append(self.job.id, {"taskId": None, "complete": False})
        reference = receipt["reference"]
        registered = {(self.job.id, reference["id"]): reference["sha256"]}
        validate_resume_receipts([self.job], self.journal, registered)
        self.assertEqual(self.journal.read(self.job.id), [receipt])

    def test_changed_registered_bytes_are_rejected(self):
        receipt = self.journal.append(self.job.id, {"complete": False})
        with self.assertRaisesRegex(ValueError, "changed receipt"):
            validate_resume_receipts(
                [self.job], self.journal, {(self.job.id, receipt["reference"]["id"]): "wrong-hash"}
            )

    def test_already_recoverable_unknown_facts_are_rejected(self):
        self.job.provider_task_id = "existing-task"
        with self.assertRaisesRegex(ValueError, "recoverable facts"):
            validate_resume_receipts([self.job], self.journal, {})


if __name__ == "__main__":
    unittest.main()
