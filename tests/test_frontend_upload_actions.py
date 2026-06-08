import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class FrontendUploadActionsTests(unittest.TestCase):
    def test_upload_page_exposes_preprocess_action_after_upload(self):
        page = ROOT_DIR / "frontend" / "app" / "upload" / "page.tsx"
        text = page.read_text(encoding="utf-8")

        self.assertIn("/api/v1/tasks/preprocess", text)
        self.assertIn("Start preprocessing", text)


if __name__ == "__main__":
    unittest.main()
