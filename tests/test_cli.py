import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from stemlift.cli import build_parser, extract, main


class ExtractTest(unittest.TestCase):
    def test_cli_requires_command_mode_and_stem(self) -> None:
        parser = build_parser()
        invalid_arguments = (
            ["song.mp3", "--mode", "stems", "--stem", "bass"],
            ["extract", "song.mp3", "--stem", "bass"],
            ["extract", "song.mp3", "--mode", "stems"],
        )

        for arguments in invalid_arguments:
            with (
                self.subTest(arguments=arguments),
                patch("sys.stderr", new=io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                parser.parse_args(arguments)

    def test_cli_accepts_multiple_stems(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mp3"
            input_path.touch()
            outputs = (
                root / "song - vocals.mp3",
                root / "song - bass.mp3",
            )

            with (
                patch("stemlift.cli.extract", return_value=outputs) as run,
                patch("stemlift.cli.sys.stdout", new=io.StringIO()) as stdout,
            ):
                result = main(
                    [
                        "extract",
                        str(input_path),
                        "--mode",
                        "stems",
                        "--stem",
                        "vocals",
                        "--stem",
                        "bass",
                        "--stem",
                        "bass",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(
                run.call_args.kwargs["selected_stems"],
                ("vocals", "bass"),
            )
            self.assertEqual(run.call_args.kwargs["output_mode"], "stems")
            self.assertEqual(
                stdout.getvalue().splitlines(),
                [
                    f"vocals: {outputs[0]}",
                    f"bass: {outputs[1]}",
                ],
            )

    def test_output_format_defaults_to_mp3(self) -> None:
        parser = build_parser()
        extract_defaults = parser.parse_args(
            ["extract", "song.wav", "--mode", "stems", "--stem", "bass"]
        )
        self.assertEqual(extract_defaults.format, "mp3")
        self.assertEqual(extract_defaults.stems, ["bass"])
        serve_defaults = parser.parse_args(["serve"])
        self.assertEqual(serve_defaults.host, "0.0.0.0")
        self.assertEqual(serve_defaults.port, 8000)
        self.assertEqual(serve_defaults.format, "mp3")
        self.assertIsNone(serve_defaults.segment)
        self.assertEqual(serve_defaults.max_upload_mb, 100)
        self.assertEqual(serve_defaults.upload_timeout, 300)
        self.assertEqual(serve_defaults.storage_quota_gb, 1.0)
        self.assertEqual(parser.parse_args(["serve", "--segment", "4"]).segment, 4)
        configured = parser.parse_args(
            [
                "serve",
                "--max-upload-mb",
                "250",
                "--upload-timeout",
                "600",
                "--storage-quota-gb",
                "2.5",
            ]
        )
        self.assertEqual(configured.max_upload_mb, 250)
        self.assertEqual(configured.upload_timeout, 600)
        self.assertEqual(configured.storage_quota_gb, 2.5)
        self.assertEqual(
            parser.parse_args(
                [
                    "extract",
                    "song.wav",
                    "--mode",
                    "stems",
                    "--stem",
                    "vocals",
                    "--stem",
                    "drums",
                ]
            ).stems,
            ["vocals", "drums"],
        )

    def test_extract_reports_progress_and_passes_segment_to_demucs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mp3"
            input_path.touch()
            progress: list[tuple[int, str | None]] = []

            process = Mock()
            process.stdout = io.StringIO(
                "  0%|start [00:00<?, ?]\r"
                " 42%|working [00:08<00:11, 1.23s/s]\r"
                "100%|done [00:19<00:00, 1.23s/s]\n"
            )
            process.wait.return_value = 0

            def start_demucs(command: list[str], **kwargs):
                self.assertEqual(command[command.index("--segment") + 1], "4")
                self.assertEqual(command[command.index("-n") + 1], "htdemucs")
                self.assertTrue(kwargs["text"])
                stem_dir = (
                    Path(command[command.index("--out") + 1]) / "htdemucs" / "song"
                )
                stem_dir.mkdir(parents=True)
                (stem_dir / "bass.mp3").touch()
                (stem_dir / "no_bass.mp3").touch()
                return process

            with (
                patch("stemlift.cli.subprocess.Popen", side_effect=start_demucs),
                patch("stemlift.cli.sys.stderr", new=io.StringIO()),
            ):
                extract(
                    input_path,
                    root / "output",
                    selected_stems=("bass",),
                    output_mode="stems",
                    segment=4,
                    progress_callback=lambda percent, eta: progress.append(
                        (percent, eta)
                    ),
                )

            self.assertEqual(
                progress,
                [(0, None), (42, "00:11"), (100, "00:00")],
            )

    def test_extract_copies_only_the_selected_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mp3"
            output_dir = root / "output"
            input_path.touch()

            work_dirs: list[Path] = []

            def run_demucs(command: list[str], *, check: bool) -> None:
                self.assertTrue(check)
                self.assertIn("--mp3", command)
                work_dir = Path(command[command.index("--out") + 1])
                work_dirs.append(work_dir)
                stem_dir = work_dir / "htdemucs" / "song"
                stem_dir.mkdir(parents=True)
                (stem_dir / "bass.mp3").write_bytes(b"bass")

            with patch("stemlift.cli.subprocess.run", side_effect=run_demucs):
                outputs = extract(
                    input_path,
                    output_dir,
                    selected_stems=("bass",),
                    output_mode="stems",
                )

            self.assertEqual(outputs[0].read_bytes(), b"bass")
            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                ["song - bass.mp3"],
            )
            self.assertFalse(work_dirs[0].exists())

    def test_extract_can_keep_wav_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mp3"
            input_path.touch()

            def run_demucs(command: list[str], *, check: bool) -> None:
                self.assertTrue(check)
                self.assertNotIn("--mp3", command)
                self.assertEqual(command[command.index("-n") + 1], "htdemucs")
                stem_dir = (
                    Path(command[command.index("--out") + 1]) / "htdemucs" / "song"
                )
                stem_dir.mkdir(parents=True)
                (stem_dir / "bass.wav").touch()
                (stem_dir / "no_bass.wav").touch()

            with patch("stemlift.cli.subprocess.run", side_effect=run_demucs):
                outputs = extract(
                    input_path,
                    root / "output",
                    "wav",
                    selected_stems=("bass",),
                    output_mode="backing",
                )

            self.assertEqual(outputs[0].name, "song - backing track.wav")

    def test_extract_selection_copies_only_requested_stems(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.mp3"
            output_dir = root / "output"
            input_path.touch()

            def run_demucs(command: list[str], *, check: bool) -> None:
                self.assertTrue(check)
                self.assertNotIn("--two-stems=bass", command)
                stem_dir = (
                    Path(command[command.index("--out") + 1]) / "htdemucs" / "song"
                )
                stem_dir.mkdir(parents=True)
                for stem in ("vocals", "drums", "bass", "other"):
                    (stem_dir / f"{stem}.mp3").write_bytes(stem.encode())

            with patch("stemlift.cli.subprocess.run", side_effect=run_demucs):
                outputs = extract(
                    input_path,
                    output_dir,
                    selected_stems=("vocals", "bass"),
                    output_mode="stems",
                )

            self.assertEqual(
                [path.name for path in outputs],
                ["song - vocals.mp3", "song - bass.mp3"],
            )
            self.assertEqual(outputs[0].read_bytes(), b"vocals")
            self.assertEqual(outputs[1].read_bytes(), b"bass")

    def test_extract_selection_mixes_unselected_stems_for_backing_track(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.wav"
            output_dir = root / "output"
            input_path.touch()
            demucs_commands: list[list[str]] = []

            def run(command: list[str], *, check: bool) -> None:
                self.assertTrue(check)
                demucs_commands.append(command)

                stem_dir = (
                    Path(command[command.index("--out") + 1]) / "htdemucs" / "song"
                )
                stem_dir.mkdir(parents=True)
                for stem in ("vocals", "drums", "bass", "other"):
                    (stem_dir / f"{stem}.wav").touch()

            class Audio:
                shape = (2, 10)

                def __init__(self, sources: tuple[str, ...]):
                    self.sources = sources

                def __add__(self, other):
                    return Audio(self.sources + other.sources)

            def read_audio(path: str):
                return Audio((Path(path).stem,)), 44_100

            def save_audio(audio, output, sample_rate, *, bitrate):
                self.assertEqual(audio.sources, ("drums", "other"))
                self.assertEqual(sample_rate, 44_100)
                self.assertEqual(bitrate, 320)
                Path(output).write_bytes(b"mixed")

            with (
                patch("stemlift.cli.subprocess.run", side_effect=run),
                patch.dict(
                    "sys.modules",
                    {
                        "sphn": Mock(read=read_audio),
                        "torch": Mock(from_numpy=lambda audio: audio),
                        "demucs.audio": Mock(save_audio=save_audio),
                    },
                ),
            ):
                outputs = extract(
                    input_path,
                    output_dir,
                    "wav",
                    selected_stems=("vocals", "bass"),
                    output_mode="backing",
                )

            self.assertEqual(len(outputs), 1)
            self.assertEqual(outputs[0].name, "song - backing track.wav")
            self.assertEqual(outputs[0].read_bytes(), b"mixed")
            self.assertEqual(len(demucs_commands), 1)

    def test_backing_track_must_leave_at_least_one_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "song.wav"
            input_path.touch()

            with self.assertRaisesRegex(ValueError, "leave at least one stem"):
                extract(
                    input_path,
                    root / "output",
                    selected_stems=("vocals", "drums", "bass", "other"),
                    output_mode="backing",
                )


if __name__ == "__main__":
    unittest.main()
