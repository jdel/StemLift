import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stemlift.desktop_worker import main


class DesktopWorkerTest(unittest.TestCase):
    def test_worker_emits_progress_and_completed_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio_file = root / "song.mp3"
            output_directory = root / "output"
            output = output_directory / "song - bass.mp3"
            audio_file.touch()

            def run(*args, progress_callback, **kwargs):
                self.assertEqual(args, (audio_file, output_directory, "wav"))
                self.assertEqual(kwargs["selected_stems"], ["bass"])
                self.assertEqual(kwargs["output_mode"], "stems")
                self.assertEqual(kwargs["segment"], 4)
                progress_callback(42, "00:11")
                return (output,)

            with (
                patch("stemlift.desktop_worker.extract", side_effect=run),
                patch("sys.stdout", new=io.StringIO()) as stdout,
            ):
                result = main(
                    [
                        str(audio_file),
                        str(output_directory),
                        "--format",
                        "wav",
                        "--mode",
                        "stems",
                        "--stem",
                        "bass",
                        "--segment",
                        "4",
                    ]
                )

        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(result, 0)
        self.assertEqual(
            events,
            [
                {"type": "progress", "progress": 42, "eta": "00:11"},
                {"type": "complete", "outputs": [str(output)]},
            ],
        )

    def test_worker_reports_missing_input_as_json(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("sys.stdout", new=io.StringIO()) as stdout,
        ):
            result = main(
                [
                    str(Path(temp_dir) / "missing.mp3"),
                    temp_dir,
                    "--mode",
                    "stems",
                    "--stem",
                    "bass",
                ]
            )

        self.assertEqual(result, 2)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {"type": "error", "message": "Audio file not found"},
        )


if __name__ == "__main__":
    unittest.main()
