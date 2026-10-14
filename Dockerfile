FROM rust:1.99.0-slim-bookworm AS rust

FROM python:3.14.7-slim-bookworm AS builder

ARG TARGETARCH

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/
COPY --from=rust /usr/local/cargo /usr/local/cargo
COPY --from=rust /usr/local/rustup /usr/local/rustup

ENV CARGO_HOME=/usr/local/cargo \
    CMAKE=/usr/bin/cmake \
    PATH=/usr/local/cargo/bin:$PATH \
    RUSTUP_HOME=/usr/local/rustup \
    TORCH_HOME=/opt/torch-cache \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN if [ "$TARGETARCH" = amd64 ]; then export UV_COMPILE_BYTECODE=1; fi \
    && uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN if [ "$TARGETARCH" = amd64 ]; then export UV_COMPILE_BYTECODE=1; fi \
    && uv sync --locked --no-dev --no-editable \
    && mkdir -p "$TORCH_HOME" /root/.cache/huggingface \
    && .venv/bin/python -c "from demucs.pretrained import get_model; get_model('htdemucs')" \
    && chmod -R a+rX "$TORCH_HOME" /root/.cache/huggingface

FROM python:3.14.7-slim-bookworm AS runtime

# The runtime stage contains no Rust, C/C++ compiler, CMake, or uv tooling.

ENV HOME=/tmp \
    HF_HUB_OFFLINE=1 \
    PATH=/app/.venv/bin:$PATH \
    TORCH_HOME=/opt/torch-cache

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /opt/torch-cache /opt/torch-cache
COPY --from=builder /root/.cache/huggingface /opt/huggingface-cache

ENV HF_HOME=/opt/huggingface-cache

WORKDIR /work
EXPOSE 8000
ENTRYPOINT ["stemlift", "serve"]
