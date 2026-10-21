import unittest
from unittest.mock import patch

from stemlift.frozen import main


class FrozenEntrypointTest(unittest.TestCase):
    def test_multiprocessing_helpers_are_handled_before_dispatch(self) -> None:
        calls = []

        def run_demucs(_arguments):
            calls.append("demucs")
            return False

        with (
            patch(
                "stemlift.frozen.multiprocessing.freeze_support",
                side_effect=lambda: calls.append("freeze_support"),
            ),
            patch(
                "stemlift.frozen._configure_frozen_environment",
                side_effect=lambda: calls.append("environment"),
            ),
            patch("stemlift.frozen._run_frozen_demucs", side_effect=run_demucs),
            patch(
                "stemlift.frozen.cli_main",
                side_effect=lambda _arguments: calls.append("cli") or 0,
            ),
        ):
            result = main(["--help"])

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["freeze_support", "environment", "demucs", "cli"])

    def test_stemlift_name_runs_cli(self) -> None:
        with (
            patch("stemlift.frozen.sys.executable", "/tmp/stemlift"),
            patch("stemlift.frozen._configure_frozen_environment"),
            patch("stemlift.frozen._run_frozen_demucs", return_value=False),
            patch("stemlift.frozen.cli_main", return_value=7) as cli,
            patch("stemlift.frozen.worker_main") as worker,
        ):
            result = main(["extract"])

        self.assertEqual(result, 7)
        cli.assert_called_once_with(["extract"])
        worker.assert_not_called()

    def test_worker_name_runs_desktop_protocol(self) -> None:
        with (
            patch(
                "stemlift.frozen.sys.executable",
                "/tmp/stemlift-worker-aarch64-apple-darwin",
            ),
            patch("stemlift.frozen._configure_frozen_environment"),
            patch("stemlift.frozen._run_frozen_demucs", return_value=False),
            patch("stemlift.frozen.cli_main") as cli,
            patch("stemlift.frozen.worker_main", return_value=8) as worker,
        ):
            result = main(["song.mp3"])

        self.assertEqual(result, 8)
        worker.assert_called_once_with(["song.mp3"])
        cli.assert_not_called()

    def test_frozen_demucs_invocation_is_handled_first(self) -> None:
        with (
            patch("stemlift.frozen._configure_frozen_environment"),
            patch("stemlift.frozen._run_frozen_demucs", return_value=True),
            patch("stemlift.frozen.cli_main") as cli,
            patch("stemlift.frozen.worker_main") as worker,
        ):
            result = main(["-m", "demucs"])

        self.assertEqual(result, 0)
        cli.assert_not_called()
        worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
