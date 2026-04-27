# syntax=docker/dockerfile:1.7

# Stage 1: build Stockfish 18 from source. We pin the source tag rather than
# apt-get install (Debian Bookworm ships Stockfish 15) to keep analysis output
# reproducible across host and production. Same pattern as tactician.
FROM debian:bookworm-slim AS sf-builder
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN git clone --depth 1 --branch sf_18 https://github.com/official-stockfish/Stockfish.git
WORKDIR /build/Stockfish/src
RUN if [ "$TARGETARCH" = "arm64" ]; then ARCH=armv8; else ARCH=x86-64-modern; fi \
    && make -j"$(nproc)" build ARCH="$ARCH" \
    && strip stockfish

# Stage 2: runtime image with uv + project + bundled Stockfish.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
COPY --from=sf-builder /build/Stockfish/src/stockfish /usr/local/bin/stockfish

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    STOCKFISH_PATH=/usr/local/bin/stockfish

COPY pyproject.toml ./
RUN uv sync --no-install-project --no-dev

COPY README.md ./
COPY src/ src/
RUN uv sync --no-dev

ENTRYPOINT ["uv", "run", "--no-sync", "python", "-m", "deep_thought.main"]
