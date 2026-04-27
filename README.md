# Deep Thought

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![RabbitMQ](https://img.shields.io/badge/RabbitMQ-3.13-FF6600?logo=rabbitmq&logoColor=white)](https://www.rabbitmq.com/)
[![MinIO](https://img.shields.io/badge/MinIO-S3--Compatible-C72E49?logo=minio&logoColor=white)](https://min.io/)
[![Stockfish](https://img.shields.io/badge/Stockfish-18-000000?logo=lichess&logoColor=white)](https://stockfishchess.org/)
[![License](https://img.shields.io/badge/License-GPL--3.0--or--later-blue)](LICENSE)

[한국어](README.ko.md)

> *Deep Thought (n.)* — IBM's late-1980s chess computer that paved the way for Deep Blue, and Douglas Adams's supercomputer that spent 7.5 million years computing the Answer. Ours analyzes chess games considerably faster.

**Game Analysis Service** for the [ilovepawn](https://github.com/ilovepawn) chess platform. Equivalent to chess.com's "Game Review": per-move evaluation, move classification (best / good / inaccuracy / mistake / blunder), and per-side accuracy and ACPL summaries.

Unlike the sibling services, deep-thought has no HTTP API. It is a RabbitMQ worker — the main platform publishes analysis-request messages, deep-thought consumes them, runs Stockfish, and publishes responses back via the AMQP `reply_to` convention.

---

## How It Works

1. A user requests analysis on the main platform
2. The platform publishes an AMQP message containing the raw PGN's `s3Uri`, with `correlation_id` and `reply_to` set
3. deep-thought downloads the raw PGN and parses its date header
4. If an annotated PGN already exists at the deterministic output key → reuse it (cache hit, no Stockfish)
5. Otherwise: run Stockfish per position, inject `%eval` comments, upload the annotated PGN to S3
6. Re-derive classifications and per-side accuracy/ACPL from the eval values (cheap, ms-scale)
7. Publish the response to the inbound `reply_to` with the original `correlation_id`

The annotated PGNs deep-thought writes are the same artifacts [tactician](https://github.com/ilovepawn/tactician) consumes daily for puzzle mining — bucket layout is a cross-service contract.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Chess Engine | Stockfish 18 (UCI) |
| Chess Logic | python-chess |
| Message Bus | RabbitMQ (pika, sync client) |
| Object Storage | MinIO (S3-compatible) / AWS S3 |
| Schema | Pydantic |
| Package Manager | uv |
| Infrastructure | Docker Compose |

No database. The annotated PGN in S3 is the single source of truth; classifications and summaries are re-derived from `%eval` values on every request.

---

## Getting Started

### Prerequisites

- Docker & Docker Compose

The worker image bundles Python 3.13, all dependencies, and Stockfish 18 (compiled from source for reproducibility), so no host-side Python or Stockfish install is required.

### Run

```bash
# Copy environment config (only needed for host-mode runs; compose ignores .env)
cp .env.example .env

# Start RabbitMQ + MinIO + worker
docker compose up -d

# Build the worker image (one-time / when Dockerfile or deps change)
docker compose build

# Tail worker logs
docker compose logs -f worker
```

Management consoles:
- RabbitMQ UI: <http://localhost:15672> (`guest` / `guest`)
- MinIO console: <http://localhost:9001> (`admin` / `changeme123`)

#### Host-mode (developer convenience)

If you'd rather iterate without rebuilding the image, run the worker on the host. You'll need Python 3.13, [uv](https://docs.astral.sh/uv/), and Stockfish (`brew install stockfish`):

```bash
uv sync
uv run python -m deep_thought.main
```

---

## Message Contract

### Inbound (consumed from `RABBITMQ_REQUEST_QUEUE`)

JSON body:

```json
{
  "requestId": "req-abc",
  "gameId": "game-123",
  "s3Uri": "s3://ilovepawn-games-raw/game-123.pgn"
}
```

AMQP properties:
- `correlation_id`: response correlation identifier
- `reply_to`: queue to publish the response on

### Outbound (published to `properties.reply_to`)

Success:

```json
{
  "requestId": "req-abc",
  "gameId": "game-123",
  "status": "success",
  "analysisS3Uri": "s3://ilovepawn-games-analyzed/2026/04/28/game-123.pgn",
  "moves": [
    {"ply": 1, "san": "e4", "uci": "e2e4", "cp": 25, "mate": null, "classification": "best"}
  ],
  "summary": {
    "white": {"accuracy": 87.3, "acpl": 24},
    "black": {"accuracy": 72.1, "acpl": 51}
  }
}
```

Failure:

```json
{
  "requestId": "req-abc",
  "gameId": "game-123",
  "status": "failed",
  "error": {"code": "INVALID_PGN", "message": "..."}
}
```

`cp` and `mate` are White-POV (positive = White better, mate as `±N`). Classifications are: `best` / `good` / `inaccuracy` / `mistake` / `blunder` based on cp-loss thresholds.

---

## S3 Layout & Ownership

Production uses two buckets, with write permissions scoped per service:

| Bucket | Writes | Reads | Layout |
|---|---|---|---|
| `ilovepawn-games-raw` | main platform only | any platform service | owned by main platform |
| `ilovepawn-games-analyzed` | deep-thought only | any platform service | `<YYYY>/<MM>/<DD>/<gameId>.pgn` |

Read access is platform-wide so any service can pull either kind of PGN; write access is restricted to the producing service to keep blast radius small. deep-thought never assumes a specific shape for the raw bucket — it only follows the `s3Uri` in each request. The analyzed-bucket layout is a contract with [tactician](https://github.com/ilovepawn/tactician), which scans `<YYYY>/<MM>/<DD>/` prefixes daily.

---

## Project Structure

```
deep-thought/
├── src/deep_thought/
│   ├── main.py          # Entry point: load config, start consumer
│   ├── consumer.py      # RabbitMQ topology + consume loop (prefetch=1, manual ack, DLQ)
│   ├── analysis.py      # Pipeline orchestrator (S3 → cache check → Stockfish → upload)
│   ├── stockfish.py     # UCI engine wrapper
│   ├── pgn.py           # PGN parsing, date extraction, %eval injection / readback
│   ├── classifier.py    # cp-loss thresholds → best / good / inaccuracy / mistake / blunder
│   ├── summary.py       # Lichess-style accuracy + ACPL per side
│   ├── s3.py            # boto3 client + S3Uri parser + deterministic key builder
│   ├── messages.py      # Pydantic schemas (camelCase JSON)
│   └── config.py        # Environment configuration
├── tests/               # Smoke and E2E test scripts
├── Dockerfile           # Multi-stage: Stockfish 18 builder + Python runtime
├── docker-compose.yml   # RabbitMQ + MinIO + worker
└── pyproject.toml
```

Worker concurrency model: **one worker = one game = one Stockfish**, strict serial. Stockfish itself is multi-threaded (`STOCKFISH_THREADS`); worker-level concurrency would just thrash CPU. Scale horizontally via more replicas, not by raising `prefetch_count`.

---

## License

This project is licensed under the **GPL-3.0-or-later License** — see the [LICENSE](LICENSE) file for details.

GPL-3.0-or-later is required because we depend on `python-chess` (GPL).
