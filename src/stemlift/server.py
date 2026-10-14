import hashlib
import json
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from stemlift.cli import STEMS, extract

CONTENT_TYPE_SUFFIXES = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


class StemServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        default_format: str,
        segment: int | None,
        asset_directory: Path,
        max_upload_mb: int,
        upload_timeout: float,
        storage_quota_gb: float,
    ) -> None:
        super().__init__(server_address, StemRequestHandler)
        self.default_format = default_format
        self.segment = segment
        self.asset_directory = asset_directory.resolve()
        self.asset_directory.mkdir(parents=True, exist_ok=True)
        self.max_upload_bytes = max_upload_mb * 1_000_000
        self.upload_timeout = upload_timeout
        self.storage_quota_bytes = int(storage_quota_gb * 1_000_000_000)
        self.asset_lock = threading.Lock()
        self.extraction_lock = threading.Lock()
        self.progress_lock = threading.Lock()
        self.running = False
        self.progress = 0
        self.eta: str | None = None
        with self.asset_lock:
            self._make_storage_space(0)

    def _make_storage_space(self, required_bytes: int) -> bool:
        if required_bytes > self.storage_quota_bytes:
            return False

        archives = sorted(
            (path for path in self.asset_directory.glob("*.zip") if path.is_file()),
            key=lambda path: path.stat().st_mtime_ns,
        )
        stored_bytes = sum(path.stat().st_size for path in archives)
        for archive in archives:
            if stored_bytes + required_bytes <= self.storage_quota_bytes:
                break
            archive_size = archive.stat().st_size
            archive.unlink()
            stored_bytes -= archive_size
        return stored_bytes + required_bytes <= self.storage_quota_bytes

    def store_archive(self, temporary_path: Path, archive_path: Path) -> bool:
        with self.asset_lock:
            if not self._make_storage_space(temporary_path.stat().st_size):
                return False
            temporary_path.replace(archive_path)
        return True

    def begin_extraction(self) -> bool:
        if not self.extraction_lock.acquire(blocking=False):
            return False
        with self.progress_lock:
            self.running = True
            self.progress = 0
            self.eta = None
        return True

    def set_progress(self, progress: int, eta: str | None = None) -> None:
        with self.progress_lock:
            self.progress = max(self.progress, progress)
            if progress >= 100:
                self.eta = "00:00"
            elif eta is not None:
                self.eta = eta

    def finish_extraction(self) -> None:
        with self.progress_lock:
            self.running = False
        self.extraction_lock.release()

    def progress_status(self) -> dict[str, int | bool | str | None]:
        with self.progress_lock:
            return {
                "running": self.running,
                "progress": self.progress,
                "eta": self.eta,
            }


class StemRequestHandler(BaseHTTPRequestHandler):
    server: StemServer

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        if urlsplit(self.path).path != "/progress":
            super().log_request(code, size)

    def send_error(
        self, code: int, message: str | None = None, explain: str | None = None
    ) -> None:
        del explain
        if message is None:
            message = self.responses.get(code, ("Unknown error",))[0]
        body = f"{message}\n".encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        request_host, separator, request_port = self.headers.get("Host", "").partition(
            ":"
        )
        if request_host == "0.0.0.0":
            localhost = "localhost"
            if separator:
                localhost += f":{request_port}"
            self.send_response(307)
            self.send_header("Location", f"http://{localhost}{self.path}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        request = urlsplit(self.path)
        request_path = request.path
        if request_path == "/progress":
            body = json.dumps(self.server.progress_status()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if request_path.startswith("/assets/"):
            asset_name = request_path.removeprefix("/assets/")
            if not asset_name.endswith(".zip") or Path(asset_name).name != asset_name:
                self.send_error(404)
                return
            download_name = parse_qs(request.query).get("filename", ["stems.zip"])[-1]
            if (
                not download_name.endswith(".zip")
                or "/" in download_name
                or "\\" in download_name
                or any(ord(character) < 32 for character in download_name)
            ):
                self.send_error(400, "invalid download filename")
                return
            fallback_name = (
                download_name.encode("ascii", "ignore")
                .decode("ascii")
                .replace('"', "_")
                .replace("\\", "_")
                or "stems.zip"
            )

            asset_path = self.server.asset_directory / asset_name
            with self.server.asset_lock:
                if not asset_path.is_file():
                    self.send_error(404)
                    return
                asset = asset_path.open("rb")
                asset_size = asset_path.stat().st_size
            try:
                with asset:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header(
                        "Content-Disposition",
                        f'attachment; filename="{fallback_name}"; '
                        f"filename*=UTF-8''{quote(download_name, safe='')}",
                    )
                    self.send_header(
                        "Cache-Control", "public, max-age=31536000, immutable"
                    )
                    self.send_header("Content-Length", str(asset_size))
                    self.end_headers()
                    shutil.copyfileobj(asset, self.wfile)
            except BrokenPipeError, ConnectionResetError:
                pass
            return

        if request_path != "/":
            self.send_error(404)
            return

        body = files("stemlift").joinpath("static/index.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self) -> None:
        request = urlsplit(self.path)
        if request.path != "/extract":
            self.send_error(404)
            return

        parameters = parse_qs(request.query)
        modes = parameters.get("mode", [])
        if not modes:
            self.send_error(400, "mode is required")
            return
        extraction_mode = modes[-1]
        if extraction_mode not in {"stems", "backing"}:
            self.send_error(400, "mode must be stems or backing")
            return

        requested_stems = parameters.get("stem", [])
        if not requested_stems:
            self.send_error(400, "at least one stem must be selected")
            return
        unknown_stems = set(requested_stems) - set(STEMS)
        if unknown_stems:
            self.send_error(400, f"unknown stem: {sorted(unknown_stems)[0]}")
            return
        selected_stems = tuple(stem for stem in STEMS if stem in requested_stems)
        if extraction_mode == "backing" and len(selected_stems) == len(STEMS):
            self.send_error(400, "a backing track must leave at least one stem")
            return

        formats = parameters.get("format", [self.server.default_format])
        output_format = formats[-1]
        if output_format not in {"mp3", "wav"}:
            self.send_error(400, "format must be mp3 or wav")
            return

        content_length = self.headers.get("Content-Length")
        if content_length is None:
            self.send_error(411, "Content-Length is required")
            return

        response: bytes | None = None
        try:
            size = int(content_length)
        except ValueError:
            self.send_error(400, "invalid Content-Length")
            return
        if size <= 0:
            self.send_error(400, "request body is empty")
            return
        if size > self.server.max_upload_bytes:
            self.send_error(
                413,
                f"upload exceeds {self.server.max_upload_bytes // 1_000_000} MB limit",
            )
            return

        supplied_filename = parameters.get(
            "filename", [self.headers.get("X-Filename", "upload")]
        )[-1]
        filename = Path(supplied_filename.replace("\\", "/")).name or "upload"
        if not Path(filename).suffix:
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            filename += CONTENT_TYPE_SUFFIXES.get(content_type, ".audio")

        extraction_started = False
        try:
            with tempfile.TemporaryDirectory(prefix="stem-api-") as temp_dir:
                temp_path = Path(temp_dir)
                input_path = temp_path / filename
                digest = hashlib.sha256()
                upload_deadline = time.monotonic() + self.server.upload_timeout
                with input_path.open("wb") as destination:
                    remaining = size
                    while remaining:
                        timeout = upload_deadline - time.monotonic()
                        if timeout <= 0:
                            raise TimeoutError
                        self.connection.settimeout(timeout)
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise OSError("request body ended before Content-Length")
                        destination.write(chunk)
                        digest.update(chunk)
                        remaining -= len(chunk)
                self.connection.settimeout(None)

                if not self.server.begin_extraction():
                    self.send_error(409, "another extraction is already running")
                    return
                extraction_started = True

                segment_key = (
                    "default"
                    if self.server.segment is None
                    else str(self.server.segment)
                )
                archive_key = f"{digest.hexdigest()}-{output_format}-s{segment_key}"
                archive_key += f"-{extraction_mode}-{'-'.join(selected_stems)}"
                archive_name = f"{archive_key}.zip"
                download_name = f"{input_path.stem}.zip"
                archive_path = self.server.asset_directory / archive_name
                with self.server.asset_lock:
                    cached = archive_path.is_file()

                if not cached:
                    output_dir = temp_path / "stems"
                    output_paths = extract(
                        input_path,
                        output_dir,
                        output_format,
                        selected_stems=selected_stems,
                        output_mode=extraction_mode,
                        segment=self.server.segment,
                        progress_callback=self.server.set_progress,
                    )
                    with tempfile.NamedTemporaryFile(
                        dir=self.server.asset_directory,
                        prefix=f".{archive_name}.",
                        suffix=".tmp",
                        delete=False,
                    ) as temporary_archive:
                        temporary_archive_path = Path(temporary_archive.name)
                    try:
                        with zipfile.ZipFile(
                            temporary_archive_path, "w", zipfile.ZIP_STORED
                        ) as archive:
                            for output_path in output_paths:
                                archive.write(output_path, output_path.name)
                        if not self.server.store_archive(
                            temporary_archive_path, archive_path
                        ):
                            self.send_error(
                                507,
                                "generated archive exceeds storage quota",
                            )
                            return
                    finally:
                        temporary_archive_path.unlink(missing_ok=True)

                self.server.set_progress(100)
                response = json.dumps(
                    {
                        "url": (
                            f"/assets/{archive_name}"
                            f"?filename={quote(download_name, safe='')}"
                        ),
                        "filename": download_name,
                        "cached": cached,
                    }
                ).encode("utf-8")
        except TimeoutError:
            self.send_error(408, "upload timed out")
        except subprocess.CalledProcessError as error:
            if error.returncode == -signal.SIGKILL:
                message = (
                    "Demucs was killed by SIGKILL, usually because the system or "
                    "container ran out of memory"
                )
            elif error.cmd and Path(str(error.cmd[0])).name == "ffmpeg":
                message = f"FFmpeg exited with status {error.returncode}"
            else:
                message = f"Demucs exited with status {error.returncode}"
            self.send_error(500, message)
        except (OSError, RuntimeError) as error:
            self.send_error(500, str(error))
        finally:
            self.connection.settimeout(None)
            if extraction_started:
                self.server.finish_extraction()

        if response is None:
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        try:
            self.wfile.write(response)
        except BrokenPipeError, ConnectionResetError:
            pass


def create_server(
    host: str,
    port: int,
    output_format: str,
    segment: int | None = None,
    asset_directory: Path = Path("stem_extracts"),
    max_upload_mb: int = 100,
    upload_timeout: float = 300,
    storage_quota_gb: float = 1.0,
) -> StemServer:
    return StemServer(
        (host, port),
        output_format,
        segment,
        asset_directory,
        max_upload_mb,
        upload_timeout,
        storage_quota_gb,
    )


def serve(
    host: str,
    port: int,
    output_format: str,
    segment: int | None = None,
    *,
    max_upload_mb: int = 100,
    upload_timeout: float = 300,
    storage_quota_gb: float = 1.0,
) -> int:
    server = create_server(
        host,
        port,
        output_format,
        segment,
        max_upload_mb=max_upload_mb,
        upload_timeout=upload_timeout,
        storage_quota_gb=storage_quota_gb,
    )
    segment_label = "model default" if segment is None else f"{segment}s"
    browser_host = "localhost" if host in {"0.0.0.0", "::"} else host
    print(
        f"Serving PUT /extract on http://{browser_host}:{port} "
        f"(format: {output_format}, segment: {segment_label})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
