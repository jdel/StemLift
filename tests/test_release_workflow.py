import unittest
from pathlib import Path


class ReleaseWorkflowTest(unittest.TestCase):
    def test_macos_worker_is_not_resigned_with_an_ad_hoc_identity(self) -> None:
        workflow = (
            Path(__file__).parents[1] / ".github" / "workflows" / "release.yaml"
        ).read_text()

        self.assertNotIn("APPLE_SIGNING_IDENTITY", workflow)
        self.assertNotIn("signing_identity:", workflow)


if __name__ == "__main__":
    unittest.main()
