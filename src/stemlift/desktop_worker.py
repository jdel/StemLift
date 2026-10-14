import argparse
import json
import multiprocessing
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from stemlift.cli import STEMS, extract


def _emit(event: dict[str, object]) -> None:
    print(json.dumps(event), flush=True)


def _configure_frozen_environment() -> None:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is None:
        return

    bundle_path = Path(bundle_root)
    os.environ.setdefault("TORCH_HOME", str(bundle_path / "torch"))
    os.environ.setdefault("HF_HOME", str(bundle_path / "huggingface"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    bundled_bin = bundle_path / "bin"
    if bundled_bin.is_dir():
        os.environ["PATH"] = os.pathsep.join(
            (str(bundled_bin), os.environ.get("PATH", ""))
        )


def _run_frozen_demucs(arguments: Sequence[str]) -> bool:
    if not getattr(sys, "frozen", False) or list(arguments[:2]) != ["-m", "demucs"]:
        return False

    from demucs.separate import main as demucs_main

    demucs_main(list(arguments[2:]))
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="StemLift desktop worker")
    parser.add_argument("audio_file", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--format", choices=("mp3", "wav"), default="mp3")
    parser.add_argument("--mode", choices=("stems", "backing"), required=True)
    parser.add_argument(
        "--stem",
        action="append",
        choices=STEMS,
        dest="stems",
        required=True,
    )
    parser.add_argument("--segment", type=int, choices=range(1, 8))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    _configure_frozen_environment()
    if _run_frozen_demucs(arguments):
        return 0

    args = build_parser().parse_args(arguments)
    if not args.audio_file.is_file():
        _emit({"type": "error", "message": "Audio file not found"})
        return 2

    try:
        outputs = extract(
            args.audio_file,
            args.output_directory,
            args.format,
            selected_stems=args.stems,
            output_mode=args.mode,
            segment=args.segment,
            progress_callback=lambda progress, eta: _emit(
                {"type": "progress", "progress": progress, "eta": eta}
            ),
        )
    except subprocess.CalledProcessError as error:
        _emit(
            {
                "type": "error",
                "message": f"Extraction process exited with status {error.returncode}",
            }
        )
        return error.returncode or 1
    except (OSError, RuntimeError, ValueError) as error:
        _emit({"type": "error", "message": str(error)})
        return 1

    _emit(
        {
            "type": "complete",
            "outputs": [str(output) for output in outputs],
        }
    )
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
