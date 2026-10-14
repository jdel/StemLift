import argparse
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Sequence

PROGRESS_PATTERN = re.compile(r"(\d{1,3})%")
ETA_PATTERN = re.compile(r"\[[^<\]]+<(\d+:\d{2}(?::\d{2})?)")
STEMS = ("vocals", "drums", "bass", "other")


def _positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def _positive_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def _report_progress(
    output: str, progress_callback: Callable[[int, str | None], None]
) -> None:
    matches = PROGRESS_PATTERN.findall(output)
    if not matches:
        return
    eta_match = ETA_PATTERN.search(output)
    progress_callback(
        min(int(matches[-1]), 100),
        eta_match.group(1) if eta_match else None,
    )


def _run_demucs(
    command: list[str], progress_callback: Callable[[int, str | None], None]
) -> None:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    assert process.stdout is not None

    pending = ""
    while character := process.stdout.read(1):
        pending += character
        if character not in "\r\n":
            continue

        sys.stderr.write(pending)
        sys.stderr.flush()
        _report_progress(pending, progress_callback)
        pending = ""

    if pending:
        sys.stderr.write(pending)
        sys.stderr.flush()
        _report_progress(pending, progress_callback)

    returncode = process.wait()
    if returncode:
        raise subprocess.CalledProcessError(returncode, command)


def _write_backing_track(source_paths: Sequence[Path], output_path: Path) -> None:
    if len(source_paths) == 1:
        shutil.copy2(source_paths[0], output_path)
        return

    import sphn
    import torch
    from demucs.audio import save_audio

    mixed = None
    sample_rate = None
    for source_path in source_paths:
        audio, current_sample_rate = sphn.read(str(source_path))
        if sample_rate is None:
            sample_rate = int(current_sample_rate)
        elif sample_rate != int(current_sample_rate):
            raise RuntimeError("Demucs stems have mismatched sample rates")
        source = torch.from_numpy(audio)
        if mixed is not None and source.shape != mixed.shape:
            raise RuntimeError("Demucs stems have mismatched lengths")
        mixed = source if mixed is None else mixed + source

    assert mixed is not None and sample_rate is not None
    save_audio(mixed, output_path, sample_rate, bitrate=320)


def extract(
    input_path: Path,
    output_dir: Path,
    output_format: str = "mp3",
    *,
    selected_stems: Sequence[str],
    output_mode: str,
    segment: int | None = None,
    progress_callback: Callable[[int, str | None], None] | None = None,
) -> tuple[Path, ...]:
    selected_stems = tuple(dict.fromkeys(selected_stems))
    unknown_stems = set(selected_stems) - set(STEMS)
    if unknown_stems:
        raise ValueError(f"unknown stem: {sorted(unknown_stems)[0]}")
    if not selected_stems:
        raise ValueError("at least one stem must be selected")
    if output_mode not in {"stems", "backing"}:
        raise ValueError("output mode must be stems or backing")
    if output_mode == "backing" and len(selected_stems) == len(STEMS):
        raise ValueError("a backing track must leave at least one stem")

    input_path = input_path.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="stem-extract-") as work_dir:
        command = [
            sys.executable,
            "-m",
            "demucs",
            "-n",
            "htdemucs",
        ]
        if len(selected_stems) == 1:
            command.append(f"--two-stems={selected_stems[0]}")
        command.extend(("--out", work_dir))
        if segment is not None:
            command.extend(("--segment", str(segment)))
        if output_format == "mp3":
            command.append("--mp3")
        command.append(str(input_path))
        if progress_callback is None:
            subprocess.run(command, check=True)
        else:
            _run_demucs(command, progress_callback)

        stem_dir = Path(work_dir) / "htdemucs" / input_path.stem
        if len(selected_stems) == 1:
            stem = selected_stems[0]
            source_paths = {stem: stem_dir / f"{stem}.{output_format}"}
            backing_source = stem_dir / f"no_{stem}.{output_format}"
        else:
            source_paths = {
                stem: stem_dir / f"{stem}.{output_format}" for stem in STEMS
            }
            backing_source = None

        missing = [stem for stem, path in source_paths.items() if not path.is_file()]
        if output_mode == "backing" and backing_source is not None:
            if not backing_source.is_file():
                missing.append(f"no_{selected_stems[0]}")
        if missing:
            raise RuntimeError(
                f"Demucs did not produce the expected stem: {missing[0]}"
            )

        if output_mode == "stems":
            outputs = []
            for stem in selected_stems:
                output = output_dir / f"{input_path.stem} - {stem}.{output_format}"
                shutil.copy2(source_paths[stem], output)
                outputs.append(output)
            return tuple(outputs)

        backing_output = (
            output_dir / f"{input_path.stem} - backing track.{output_format}"
        )
        if backing_source is not None:
            shutil.copy2(backing_source, backing_output)
        else:
            remaining_paths = [
                source_paths[stem] for stem in STEMS if stem not in selected_stems
            ]
            _write_backing_track(remaining_paths, backing_output)

        return (backing_output,)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract audio stems and backing tracks with Demucs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser(
        "extract", help="extract selected stems or create a backing track"
    )
    extract_parser.add_argument("audio_file", type=Path)
    extract_parser.add_argument(
        "output_directory", nargs="?", type=Path, default=Path("stem_extracts")
    )
    extract_parser.add_argument("--format", choices=("mp3", "wav"), default="mp3")
    extract_parser.add_argument(
        "--stem",
        action="append",
        choices=STEMS,
        dest="stems",
        required=True,
        help="stem to extract or remove (repeatable)",
    )
    extract_parser.add_argument(
        "--mode",
        choices=("stems", "backing"),
        required=True,
        help="extract the selected stems or remove them from a backing track",
    )

    serve_parser = subparsers.add_parser("serve", help="serve the extraction API")
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--format", choices=("mp3", "wav"), default="mp3")
    serve_parser.add_argument(
        "--max-upload-mb",
        type=_positive_integer,
        default=100,
        help="maximum upload size in MB (default: 100)",
    )
    serve_parser.add_argument(
        "--upload-timeout",
        type=_positive_integer,
        default=300,
        metavar="SECONDS",
        help="overall upload timeout in seconds (default: 300)",
    )
    serve_parser.add_argument(
        "--storage-quota-gb",
        type=_positive_number,
        default=1.0,
        help="maximum stored ZIP archive size in GB (default: 1)",
    )
    serve_parser.add_argument(
        "--segment",
        type=int,
        choices=range(1, 8),
        metavar="SECONDS",
        help="Demucs chunk length in seconds (1-7; lower uses less memory)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    parser = build_parser()
    args = parser.parse_args(arguments)

    if args.command == "serve":
        from stemlift.server import serve

        return serve(
            args.host,
            args.port,
            args.format,
            args.segment,
            max_upload_mb=args.max_upload_mb,
            upload_timeout=args.upload_timeout,
            storage_quota_gb=args.storage_quota_gb,
        )

    if not args.audio_file.is_file():
        parser.error(f"audio file not found: {args.audio_file}")

    selected_stems = tuple(dict.fromkeys(args.stems))
    try:
        outputs = extract(
            args.audio_file,
            args.output_directory,
            args.format,
            selected_stems=selected_stems,
            output_mode=args.mode,
        )
    except subprocess.CalledProcessError as error:
        return error.returncode
    except (RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    if args.mode == "stems":
        for stem, output in zip(selected_stems, outputs, strict=True):
            print(f"{stem}: {output}")
    else:
        print(f"backing track: {outputs[0]}")
    return 0
