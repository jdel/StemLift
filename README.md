# StemLift

> **Disclaimer:** These 3 lines are the only thing written by a human on this project
> While not an advocate of vibe coding, I wanted to try it on a low risk, low impact project
> The process took some trial and error, but ultimately the goal of this app has been reached

Extract vocals, drums, bass, and other instruments with Demucs, or remove any
combination of those stems to create a backing track. StemLift is available as
a native desktop app, a command-line tool, an HTTP service, and a Docker image.

**What it does:**

- Separates audio with Demucs' `htdemucs` model.
- Extracts one or more individual stems: vocals, drums, bass, or other.
- Creates backing tracks by removing selected stems.
- Writes MP3 or WAV output.
- Runs locally without uploading music to a third-party service.
- Bundles the model in desktop and Docker builds for offline extraction.
- Caches identical HTTP requests and reports live extraction progress.

---

## Install

### Desktop app

Download the installer for your platform from the
[releases page](https://github.com/jdel/StemLift/releases): `.zip` containing a
macOS app, `.exe` for Windows, or `.AppImage`/`.deb`/`.rpm` for Linux.

The desktop app runs extraction locally, writes files directly to the folder
containing the source audio by default, and does not start an HTTP server. You
can choose a different output folder before starting. The first launch can
take a moment while the bundled worker starts.

> The desktop builds are unsigned, so macOS Gatekeeper and Windows SmartScreen
> may warn on first launch.

### Standalone CLI

The releases page also provides self-contained CLI archives for Apple Silicon
macOS, AMD64 Linux, and AMD64 Windows. Extract the archive and place `stemlift`
or `stemlift.exe` somewhere on your `PATH`. These archives include Python,
Torch, Demucs, and the model, so they do not require a separate Python install.

### Homebrew (macOS)

The standalone CLI and desktop app are available for Apple Silicon from
[`jdel/homebrew-tap`](https://github.com/jdel/homebrew-tap):

```sh
# CLI
brew install --cask jdel/tap/stemlift

# Desktop app
brew install --cask jdel/tap/stemlift-desktop
```

Both casks remove the quarantine flag after installation so Gatekeeper does
not block the unsigned executables on first launch.

### Python CLI and server

Install [uv 0.13.0 or newer](https://docs.astral.sh/uv/), clone the repository,
and create the locked Python environment:

```sh
git clone https://github.com/jdel/StemLift
cd StemLift
uv sync --locked
```

Linux ARM64 builds `sphn` locally. With CMake 4 or newer, use:

```sh
CMAKE_POLICY_VERSION_MINIMUM=3.5 uv sync --locked
```

### Docker

Pull the published multi-architecture image:

```sh
docker pull ghcr.io/jdel/stemlift:latest
docker run --rm -p 8000:8000 \
  -v "$PWD/assets:/work/stem_extracts" \
  ghcr.io/jdel/stemlift:latest
```

Images are published for AMD64 and ARM64. Versioned image tags match the Git
tag, for example `v0.1.0`; preview releases do not replace `latest`.

Alternatively, build the image locally:

```sh
docker build -t stemlift .
```

Or start the HTTP service with Compose:

```sh
docker compose up --build
```

Open [http://localhost:8000](http://localhost:8000). Completed archives are
stored under `assets/` on the host.

---

## Get started

Open the desktop app, then:

1. Select an MP3, WAV, FLAC, or OGG audio file.
2. Optionally choose a different output folder.
3. Choose MP3 or WAV output.
4. Select instruments to extract or remove.
5. Click **Extract selected stems** or **Create backing track**.

The app writes results beside the source audio unless you select another
folder. A backing-track request must leave at least one stem unselected. While
an extraction runs, **Cancel extraction** stops the worker and its subprocesses.
Use **View worker logs** to open diagnostic output in a separate window.

---

## Command line

### Extract stems

```sh
uv run --locked stemlift extract input.mp3 \
  --mode stems \
  --stem vocals \
  --stem bass
```

Output is written to `stem_extracts/` by default:

```text
stem_extracts/input - vocals.mp3
stem_extracts/input - bass.mp3
```

Choose another directory or WAV output:

```sh
uv run --locked stemlift extract input.mp3 ./output \
  --format wav \
  --mode stems \
  --stem drums
```

### Create a backing track

```sh
uv run --locked stemlift extract input.mp3 \
  --mode backing \
  --stem vocals \
  --stem bass
```

### Commands

| Command | Description |
|---------|-------------|
| `stemlift extract` | Extract selected stems or create a backing track |
| `stemlift serve` | Start the browser interface and HTTP API |

### `extract` options

| Option | Description |
|--------|-------------|
| `AUDIO_FILE` | Source audio file |
| `OUTPUT_DIRECTORY` | Destination directory (default: `stem_extracts`) |
| `--mode stems\|backing` | Extract selected stems or remove them from a backing track |
| `--stem vocals\|drums\|bass\|other` | Instrument to extract or remove; repeatable |
| `--format mp3\|wav` | Output format (default: `mp3`) |

Both `--mode` and at least one `--stem` are required.

### `serve` options

| Option | Description |
|--------|-------------|
| `--host` | Bind address (default: `0.0.0.0`) |
| `--port` | HTTP port (default: `8000`) |
| `--format mp3\|wav` | Default output format (default: `mp3`) |
| `--segment 1..7` | Demucs chunk length in seconds; smaller values use less memory |
| `--max-upload-mb` | Maximum upload size in MB (default: `100`) |
| `--upload-timeout` | Upload deadline in seconds (default: `300`) |
| `--storage-quota-gb` | Maximum stored archive size in GB (default: `1`) |

---

## HTTP API

Start the service:

```sh
uv run --locked stemlift serve
```

Send an audio file as the body of `PUT /extract`. Repeat `stem` to request
multiple instruments:

```sh
curl --request PUT \
  --header "Content-Type: audio/mpeg" \
  --data-binary @input.mp3 \
  "http://localhost:8000/extract?mode=stems&stem=vocals&stem=bass"
```

The response points to a stored ZIP archive:

```json
{
  "url": "/assets/abc123-mp3-sdefault-stems-vocals-bass.zip?filename=input.zip",
  "filename": "input.zip",
  "cached": false
}
```

Download it separately:

```sh
curl "http://localhost:8000/assets/abc123-mp3-sdefault-stems-vocals-bass.zip?filename=input.zip" \
  --output stems.zip
```

Use `mode=backing` to remove the requested stems, and `format=wav` for WAV
output. If the content type does not identify the source format, send its name
in `X-Filename`.

Requests with identical audio, format, segment length, mode, and stems reuse
the existing archive and return `"cached": true`. Uploads are limited to
100 MB and five minutes by default. Stored archives share a 1 GB quota; the
oldest archives are removed when space is needed.

---

## Docker

Extract a local file:

```sh
mkdir -p stem_extracts
docker run --rm --entrypoint stemlift --user "$(id -u):$(id -g)" \
  -v "$PWD:/input:ro" \
  -v "$PWD/stem_extracts:/output" \
  stemlift extract "/input/input.mp3" /output \
  --mode stems --stem bass
```

Run the API directly:

```sh
docker run --rm -p 8000:8000 stemlift
```

The image includes `htdemucs`; containers do not download the model at
runtime. Rust, CMake, compilers, and uv remain in the builder stage and are not
present in the runtime image.

---

## Building

### Prerequisites

- Node.js **26+** and npm
- Rust **1.99+**
- [uv](https://docs.astral.sh/uv/)
- `make`
- Native [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/) for the target platform

Desktop installers must be built on their target operating system:

```sh
make build-macos    # macOS .app
make build-windows  # Windows NSIS installer
make build-linux    # AppImage, .deb, and .rpm
```

Build only the standalone CLI and desktop worker for the current platform:

```sh
make cli-build
```

The first native build downloads the Demucs model and freezes Python, Torch,
and Demucs into one large executable. The same executable is published as the
standalone CLI and copied into the desktop app as its worker. Make tracks the
relevant inputs, so unchanged frontend and worker artifacts are reused on
later builds. The build automatically runs the pinned uv 0.13.0 through `uvx`
and installs Python 3.14.7 when needed.

Build the multi-architecture Docker image with:

```sh
make docker-build
```

### Make targets

| Target | Description |
|--------|-------------|
| `make dependencies` | Install locked npm dependencies when required |
| `make frontend-build` | Build the frontend when its inputs change |
| `make cli-build` | Build the standalone CLI for the current platform |
| `make sidecar-build` | Freeze the Python worker when its inputs change |
| `make build-macos` | Build the macOS desktop app |
| `make build-windows` | Build the Windows NSIS installer |
| `make build-linux` | Build Linux AppImage, Debian, and RPM packages |
| `make docker-build` | Build the Linux AMD64/ARM64 Docker image |
| `make test` | Run static checks and tests |
| `make clean` | Remove generated build artifacts |
| `make help` | List available targets |

---

## Development

Install dependencies:

```sh
uv sync --locked
npm ci --include=optional
```

Run the desktop app with live frontend reload:

```sh
npm run desktop:dev
```

Run only the frontend development server:

```sh
npm run frontend:dev
```

Run the test and build checks:

```sh
make test
```

The locked environment uses Python 3.14.7, Demucs 4.1.0, NumPy 2.5.4,
CPU-only Torch 2.14.1, and PyInstaller 6.22.3. Python 3.15 is not yet
supported because Demucs' `lameenc` dependency does not publish a macOS ARM64
CPython 3.15 wheel.

---

## License

[BSD 2-Clause](LICENSE)
