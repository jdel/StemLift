import http.client
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from stemlift.server import create_server, serve


class ServerTest(unittest.TestCase):
    def test_zero_host_redirects_download_to_localhost(self) -> None:
        with tempfile.TemporaryDirectory() as asset_dir:
            server = create_server(
                "127.0.0.1", 0, "mp3", asset_directory=Path(asset_dir)
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                port = server.server_address[1]
                path = "/assets/archive.zip?filename=Song.zip"
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", path, headers={"Host": f"0.0.0.0:{port}"})
                response = connection.getresponse()
                body = response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

        self.assertEqual(response.status, 307)
        self.assertEqual(
            response.getheader("Location"), f"http://localhost:{port}{path}"
        )
        self.assertEqual(body, b"")

    def test_serve_displays_localhost_for_wildcard_bind(self) -> None:
        with (
            patch("stemlift.server.create_server") as create_server,
            patch("builtins.print") as print_output,
        ):
            create_server.return_value.serve_forever.side_effect = KeyboardInterrupt
            result = serve("0.0.0.0", 8000, "mp3")

        self.assertEqual(result, 0)
        self.assertIn("http://localhost:8000", print_output.call_args.args[0])
        create_server.return_value.server_close.assert_called_once_with()

    def test_existing_archives_are_pruned_to_quota_at_startup(self) -> None:
        with tempfile.TemporaryDirectory() as asset_dir:
            asset_path = Path(asset_dir)
            oldest = asset_path / "oldest.zip"
            newest = asset_path / "newest.zip"
            oldest.write_bytes(b"x" * 700)
            newest.write_bytes(b"x" * 700)
            os.utime(oldest, (1, 1))
            os.utime(newest, (2, 2))

            server = create_server(
                "127.0.0.1",
                0,
                "mp3",
                asset_directory=asset_path,
                storage_quota_gb=0.000001,
            )
            server.server_close()

            self.assertFalse(oldest.exists())
            self.assertTrue(newest.exists())

    def test_upload_larger_than_limit_is_rejected_before_body_is_read(self) -> None:
        with (
            tempfile.TemporaryDirectory() as asset_dir,
            patch("stemlift.server.extract") as extract,
        ):
            server = create_server(
                "127.0.0.1",
                0,
                "mp3",
                asset_directory=Path(asset_dir),
                max_upload_mb=1,
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.putrequest("PUT", "/extract?mode=stems&stem=bass")
                connection.putheader("Content-Length", "1000001")
                connection.endheaders()
                response = connection.getresponse()
                body = response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

        self.assertEqual(response.status, 413)
        self.assertEqual(body, b"upload exceeds 1 MB limit\n")
        extract.assert_not_called()

    def test_upload_timeout_is_reported_before_extraction_starts(self) -> None:
        with (
            tempfile.TemporaryDirectory() as asset_dir,
            patch("stemlift.server.extract") as extract,
        ):
            server = create_server(
                "127.0.0.1",
                0,
                "mp3",
                asset_directory=Path(asset_dir),
                upload_timeout=0.05,
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection(
                    *server.server_address, timeout=2
                )
                connection.putrequest("PUT", "/extract?mode=stems&stem=bass")
                connection.putheader("Content-Length", "10")
                connection.endheaders(b"partial")
                response = connection.getresponse()
                body = response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

        self.assertEqual(response.status, 408)
        self.assertEqual(body, b"upload timed out\n")
        extract.assert_not_called()

    def test_archive_larger_than_storage_quota_is_not_stored(self) -> None:
        def extract(
            input_path: Path,
            output_dir: Path,
            output_format: str,
            *,
            selected_stems: tuple[str, ...],
            output_mode: str,
            segment: int | None,
            progress_callback,
        ):
            self.assertEqual(selected_stems, ("bass",))
            self.assertEqual(output_mode, "stems")
            output_dir.mkdir()
            bass_path = output_dir / "upload - bass.mp3"
            bass_path.write_bytes(b"bass")
            return (bass_path,)

        with (
            tempfile.TemporaryDirectory() as asset_dir,
            patch("stemlift.server.extract", side_effect=extract),
        ):
            server = create_server(
                "127.0.0.1",
                0,
                "mp3",
                asset_directory=Path(asset_dir),
                storage_quota_gb=0.0000001,
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "PUT",
                    "/extract?mode=stems&stem=bass",
                    body=b"source audio",
                    headers={"Content-Type": "audio/mpeg"},
                )
                response = connection.getresponse()
                body = response.read()
                connection.close()
                stored_archives = list(Path(asset_dir).glob("*.zip"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

        self.assertEqual(response.status, 507)
        self.assertEqual(body, b"generated archive exceeds storage quota\n")
        self.assertEqual(stored_archives, [])

    def test_progress_is_available_and_second_extraction_is_rejected(self) -> None:
        started = threading.Event()
        finish = threading.Event()

        def extract(
            input_path: Path,
            output_dir: Path,
            output_format: str,
            *,
            selected_stems: tuple[str, ...],
            output_mode: str,
            segment: int | None,
            progress_callback,
        ):
            self.assertEqual(selected_stems, ("bass",))
            self.assertEqual(output_mode, "stems")
            progress_callback(37, "01:23")
            started.set()
            self.assertTrue(finish.wait(2))
            output_dir.mkdir()
            bass_path = output_dir / "upload - bass.mp3"
            bass_path.touch()
            return (bass_path,)

        with (
            tempfile.TemporaryDirectory() as asset_dir,
            patch("stemlift.server.extract", side_effect=extract),
        ):
            server = create_server(
                "127.0.0.1",
                0,
                "mp3",
                segment=4,
                asset_directory=Path(asset_dir),
            )
            server_thread = threading.Thread(target=server.serve_forever)
            server_thread.start()

            upload_status: list[int] = []

            def upload() -> None:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "PUT",
                    "/extract?mode=stems&stem=bass",
                    body=b"source audio",
                    headers={"Content-Type": "audio/mpeg"},
                )
                response = connection.getresponse()
                upload_status.append(response.status)
                response.read()
                connection.close()

            upload_thread = threading.Thread(target=upload)
            upload_thread.start()
            try:
                self.assertTrue(started.wait(2))

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", "/progress")
                progress_response = connection.getresponse()
                progress = json.loads(progress_response.read())
                connection.close()

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "PUT",
                    "/extract?mode=stems&stem=bass",
                    body=b"another track",
                    headers={"Content-Type": "audio/mpeg"},
                )
                busy_response = connection.getresponse()
                busy_body = busy_response.read()
                connection.close()
            finally:
                finish.set()
                upload_thread.join()
                server.shutdown()
                server.server_close()
                server_thread.join()

        self.assertEqual(progress_response.status, 200)
        self.assertEqual(
            progress,
            {"running": True, "progress": 37, "eta": "01:23"},
        )
        self.assertEqual(busy_response.status, 409)
        self.assertEqual(busy_body, b"another extraction is already running\n")
        self.assertEqual(upload_status, [200])

    def test_demucs_sigkill_returns_plain_text_error(self) -> None:
        error = subprocess.CalledProcessError(-9, ["demucs"])

        with (
            tempfile.TemporaryDirectory() as asset_dir,
            patch("stemlift.server.extract", side_effect=error),
        ):
            server = create_server(
                "127.0.0.1", 0, "mp3", asset_directory=Path(asset_dir)
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "PUT",
                    "/extract?mode=stems&stem=bass",
                    body=b"source audio",
                    headers={"Content-Type": "audio/mpeg"},
                )
                response = connection.getresponse()
                body = response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

        self.assertEqual(response.status, 500)
        self.assertEqual(
            response.getheader("Content-Type"), "text/plain; charset=utf-8"
        )
        self.assertEqual(
            body,
            b"Demucs was killed by SIGKILL, usually because the system or "
            b"container ran out of memory\n",
        )

    def test_backing_track_mix_error_identifies_ffmpeg(self) -> None:
        error = subprocess.CalledProcessError(1, ["ffmpeg"])

        with (
            tempfile.TemporaryDirectory() as asset_dir,
            patch("stemlift.server.extract", side_effect=error),
        ):
            server = create_server(
                "127.0.0.1", 0, "mp3", asset_directory=Path(asset_dir)
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "PUT",
                    "/extract?mode=backing&stem=vocals",
                    body=b"source audio",
                )
                response = connection.getresponse()
                body = response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

        self.assertEqual(response.status, 500)
        self.assertEqual(body, b"FFmpeg exited with status 1\n")

    def test_put_extract_honors_format_override_and_caches_result(self) -> None:
        received_audio: list[bytes] = []

        def extract(
            input_path: Path,
            output_dir: Path,
            output_format: str,
            *,
            selected_stems: tuple[str, ...],
            output_mode: str,
            segment: int | None,
            progress_callback,
        ):
            received_audio.append(input_path.read_bytes())
            self.assertEqual(output_format, "wav")
            self.assertEqual(selected_stems, ("bass",))
            self.assertEqual(output_mode, "stems")
            self.assertEqual(segment, 4)
            progress_callback(60, "00:12")
            output_dir.mkdir()
            bass_path = output_dir / f"{input_path.stem} - bass.wav"
            bass_path.write_bytes(b"bass")
            return (bass_path,)

        with (
            tempfile.TemporaryDirectory() as asset_dir,
            patch("stemlift.server.extract", side_effect=extract),
        ):
            old_archive = Path(asset_dir) / "old.zip"
            old_archive.write_bytes(b"x" * 900)
            server = create_server(
                "127.0.0.1",
                0,
                "mp3",
                segment=4,
                asset_directory=Path(asset_dir),
                storage_quota_gb=0.000001,
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", "/")
                page_response = connection.getresponse()
                page = page_response.read()
                connection.close()

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "PUT",
                    "/extract?format=wav&mode=stems&stem=bass"
                    f"&filename={quote('Beyoncé 🎸.mp3')}",
                    body=b"source audio",
                    headers={"Content-Type": "audio/mpeg"},
                )
                response = connection.getresponse()
                body = response.read()
                connection.close()

                result = json.loads(body)
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", result["url"])
                asset_response = connection.getresponse()
                asset_body = asset_response.read()
                connection.close()

                asset_path = result["url"].split("?", 1)[0]
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", f"{asset_path}?filename=..%5Cunsafe.zip")
                invalid_name_response = connection.getresponse()
                invalid_name_body = invalid_name_response.read()
                connection.close()

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "PUT",
                    "/extract?format=wav&mode=stems&stem=bass"
                    f"&filename={quote('Beyoncé 🎸.mp3')}",
                    body=b"source audio",
                    headers={"Content-Type": "audio/mpeg"},
                )
                cached_response = connection.getresponse()
                cached_result = json.loads(cached_response.read())
                connection.close()

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", "/progress")
                progress_response = connection.getresponse()
                progress = json.loads(progress_response.read())
                connection.close()
                old_archive_exists = old_archive.exists()
                stored_archive_bytes = sum(
                    path.stat().st_size for path in Path(asset_dir).glob("*.zip")
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

        self.assertEqual(page_response.status, 200)
        self.assertEqual(
            page_response.getheader("Content-Type"), "text/html; charset=utf-8"
        )
        self.assertIn(b"StemLift", page)
        self.assertIn(b'method: "PUT"', page)
        self.assertIn(b'parameters.append("filename", file.name)', page)
        self.assertNotIn(b'"X-Filename": file.name', page)
        self.assertIn(b'<progress id="progress"', page)
        self.assertIn(b'id="download"', page)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/json")
        self.assertEqual(result["cached"], False)
        self.assertEqual(result["filename"], "Beyoncé 🎸.zip")
        self.assertTrue(result["url"].startswith("/assets/"))
        self.assertEqual(asset_response.status, 200)
        self.assertEqual(asset_response.getheader("Content-Type"), "application/zip")
        self.assertEqual(invalid_name_response.status, 400)
        self.assertEqual(invalid_name_body, b"invalid download filename\n")
        self.assertIn(
            "filename*=UTF-8''Beyonc%C3%A9%20%F0%9F%8E%B8.zip",
            asset_response.getheader("Content-Disposition"),
        )
        self.assertEqual(received_audio, [b"source audio"])
        with zipfile.ZipFile(io.BytesIO(asset_body)) as archive:
            self.assertEqual(archive.namelist(), ["Beyoncé 🎸 - bass.wav"])
            self.assertEqual(archive.read("Beyoncé 🎸 - bass.wav"), b"bass")
        self.assertEqual(cached_response.status, 200)
        self.assertEqual(
            cached_result,
            {
                "url": result["url"],
                "filename": "Beyoncé 🎸.zip",
                "cached": True,
            },
        )
        self.assertEqual(progress_response.status, 200)
        self.assertEqual(progress["progress"], 100)
        self.assertFalse(old_archive_exists)
        self.assertLessEqual(stored_archive_bytes, 1000)

    def test_put_extract_returns_selected_stems_and_backing_tracks(self) -> None:
        calls: list[tuple[tuple[str, ...], str]] = []

        def extract(
            input_path: Path,
            output_dir: Path,
            output_format: str,
            *,
            selected_stems: tuple[str, ...],
            output_mode: str,
            segment: int | None,
            progress_callback,
        ) -> tuple[Path, ...]:
            self.assertEqual(output_format, "wav")
            self.assertIsNone(segment)
            calls.append((selected_stems, output_mode))
            output_dir.mkdir()
            if output_mode == "backing":
                output = output_dir / f"{input_path.stem} - backing track.wav"
                output.write_bytes(b"backing")
                return (output,)

            outputs = []
            for stem in selected_stems:
                output = output_dir / f"{input_path.stem} - {stem}.wav"
                output.write_bytes(stem.encode())
                outputs.append(output)
            return tuple(outputs)

        with (
            tempfile.TemporaryDirectory() as asset_dir,
            patch("stemlift.server.extract", side_effect=extract),
        ):
            server = create_server(
                "127.0.0.1", 0, "mp3", asset_directory=Path(asset_dir)
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", "/")
                page_response = connection.getresponse()
                page = page_response.read()
                connection.close()

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "PUT",
                    "/extract?format=wav&mode=stems&stem=bass&stem=vocals",
                    body=b"source audio",
                    headers={"X-Filename": "song.mp3"},
                )
                stems_response = connection.getresponse()
                stems_result = json.loads(stems_response.read())
                connection.close()

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", stems_result["url"])
                stems_archive_response = connection.getresponse()
                stems_archive = stems_archive_response.read()
                connection.close()

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "PUT",
                    "/extract?format=wav&mode=backing&stem=vocals&stem=bass",
                    body=b"source audio",
                    headers={"X-Filename": "song.mp3"},
                )
                backing_response = connection.getresponse()
                backing_result = json.loads(backing_response.read())
                connection.close()

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", backing_result["url"])
                backing_archive_response = connection.getresponse()
                backing_archive = backing_archive_response.read()
                connection.close()

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "PUT",
                    "/extract?mode=stems&stem=piano",
                    body=b"source audio",
                )
                invalid_response = connection.getresponse()
                invalid_body = invalid_response.read()
                connection.close()

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "PUT",
                    "/extract?stem=bass",
                    body=b"source audio",
                )
                missing_mode_response = connection.getresponse()
                missing_mode_body = missing_mode_response.read()
                connection.close()

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "PUT",
                    "/extract?mode=backing&stem=vocals&stem=drums&stem=bass&stem=other",
                    body=b"source audio",
                )
                empty_backing_response = connection.getresponse()
                empty_backing_body = empty_backing_response.read()
                connection.close()

                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("PUT", "/bass", body=b"source audio")
                removed_route_response = connection.getresponse()
                removed_route_body = removed_route_response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

        self.assertEqual(page_response.status, 200)
        self.assertIn(b">Vocals<", page)
        self.assertIn(b">Drums<", page)
        self.assertIn(b">Bass<", page)
        self.assertIn(b">Other", page)
        self.assertIn(b'data-mode="stems"', page)
        self.assertIn(b'data-mode="backing"', page)
        self.assertNotIn(
            b'name="backing-stem" type="checkbox" value="bass" checked',
            page,
        )
        self.assertIn(b"fetch(`/extract?", page)
        self.assertEqual(stems_response.status, 200)
        self.assertNotEqual(stems_result["url"], backing_result["url"])
        with zipfile.ZipFile(io.BytesIO(stems_archive)) as archive:
            self.assertEqual(
                archive.namelist(),
                ["song - vocals.wav", "song - bass.wav"],
            )
        self.assertEqual(backing_response.status, 200)
        with zipfile.ZipFile(io.BytesIO(backing_archive)) as archive:
            self.assertEqual(archive.namelist(), ["song - backing track.wav"])
            self.assertEqual(archive.read("song - backing track.wav"), b"backing")
        self.assertEqual(
            calls,
            [(("vocals", "bass"), "stems"), (("vocals", "bass"), "backing")],
        )
        self.assertEqual(invalid_response.status, 400)
        self.assertEqual(invalid_body, b"unknown stem: piano\n")
        self.assertEqual(missing_mode_response.status, 400)
        self.assertEqual(missing_mode_body, b"mode is required\n")
        self.assertEqual(empty_backing_response.status, 400)
        self.assertEqual(
            empty_backing_body,
            b"a backing track must leave at least one stem\n",
        )
        self.assertEqual(removed_route_response.status, 404)
        self.assertEqual(removed_route_body, b"Not Found\n")


if __name__ == "__main__":
    unittest.main()
