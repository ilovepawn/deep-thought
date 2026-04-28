# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

deep-thought is the chess **game analysis** service for the ilovepawn platform. It is the equivalent of chess.com's "Game Review" feature: a user requests a post-game analysis and receives per-move evaluations, classifications (best/good/inaccuracy/mistake/blunder), and per-side accuracy/ACPL summaries.

deep-thought is **not** an HTTP API. It is a RabbitMQ worker. The main platform publishes analysis-request messages; deep-thought consumes them, runs Stockfish, writes an `%eval`-annotated PGN to S3, and publishes a response message via the inbound message's AMQP `reply_to`.

The annotated PGNs deep-thought writes are the same artifacts that **tactician** consumes daily for puzzle mining. PGN format and S3 path layout are part of the platform contract — changes here ripple to tactician.

## Commands

The local dev stack (RabbitMQ, MinIO, this worker, sibling services, Prometheus/Grafana) is managed by the sibling [`infra` repo](../infra). All `docker compose` commands run from there, not from this repo.

```bash
# In ../infra/compose:
docker compose up -d                                    # core infra + apps
docker compose --profile observability up -d            # add Prometheus + Grafana
docker compose logs -f deep-thought                     # tail this worker

# Host-mode worker (developer convenience — needs uv + host Stockfish,
# and infra compose running so RabbitMQ/MinIO are reachable)
uv sync
uv run python -m deep_thought.main
```

## Architecture

**Pipeline:** AMQP analysis request → S3 raw PGN download → Stockfish per-move analysis → annotated PGN to S3 → AMQP response

### Core modules
- `src/deep_thought/main.py` — entrypoint. Loads config, builds S3 client, starts the consumer.
- `src/deep_thought/consumer.py` — RabbitMQ topology (DLX/DLQ + main queue) and the consume loop. `prefetch=1`, manual ack only after the response is published.
- `src/deep_thought/analysis.py` — orchestrator. S3 fetch → parse → cache check → Stockfish (or skip on cache hit) → annotate → upload → derive payload.
- `src/deep_thought/stockfish.py` — UCI engine wrapper. `analyse_position` returns one eval per call from the side-to-move's POV.
- `src/deep_thought/pgn.py` — parse PGN, extract date headers, inject `%eval` comments, read them back on cache hit.
- `src/deep_thought/classifier.py` — cp-loss thresholds (`<=0` best, `<50` good, `<100` inaccuracy, `<300` mistake, else blunder).
- `src/deep_thought/summary.py` — Lichess-style accuracy approximation + ACPL per side.
- `src/deep_thought/s3.py` — boto3 client + `S3Uri.parse` for `s3://bucket/key` URIs + deterministic analysis key builder.
- `src/deep_thought/messages.py` — Pydantic schemas for inbound `AnalysisRequest` and outbound `AnalysisResponse`. camelCase JSON via `to_camel` alias generator.
- `src/deep_thought/config.py` — env loader returning a frozen `Config` dataclass.

### Message contract

Inbound (consumed from `RABBITMQ_REQUEST_QUEUE`):
```json
{
  "requestId": "uuid",
  "gameId": "abc123",
  "s3Uri": "s3://raw-games/.../abc123.pgn"
}
```
AMQP properties: `correlation_id` for response matching, `reply_to` for response routing.

Outbound (published to `properties.reply_to`):
```json
{
  "requestId": "uuid",
  "gameId": "abc123",
  "status": "success",
  "analysisS3Uri": "s3://games/2026/04/28/abc123.pgn",
  "moves": [{"ply": 1, "san": "e4", "uci": "e2e4", "cp": 25, "mate": null, "classification": "best"}, ...],
  "summary": {"white": {"accuracy": 87.3, "acpl": 24}, "black": {"accuracy": 72.1, "acpl": 51}}
}
```
Failed responses keep `requestId` / `gameId` and include `error: {code, message}` instead of `moves` / `summary`.

## Key Technical Details

- **One worker = one game = one Stockfish.** Strict serial processing per worker. Stockfish is itself multi-threaded (`STOCKFISH_THREADS`); worker-level concurrency would just thrash CPU. Scale horizontally via more replicas, not by raising `prefetch_count`.
- **No deep-thought DB.** S3 holds the eval-annotated PGN as the single source of truth. Classifications and summaries are re-derived from `%eval` comments on every request — cheap, ms-scale.
- **Idempotency by deterministic S3 key.** The cache check is `head_object` on `<bucket>/<YYYY>/<MM>/<DD>/<gameId>.pgn`. Hit → skip Stockfish, re-derive payload from cached PGN. Miss → run Stockfish, upload, derive.
- **Cross-service S3 contract.** The annotated-PGN bucket (`S3_BUCKET_ANALYSES`, default `games`) and date-partitioned key layout match what tactician's puzzle pipeline reads. Don't change the bucket name or path layout without coordinating with tactician.
- **S3 bucket ownership and IAM.** Production uses two buckets with write permissions scoped per service:

  | Bucket | Writes | Reads |
  |---|---|---|
  | `ilovepawn-games-raw` | main platform only | any platform service |
  | `ilovepawn-games-analyzed` | deep-thought only | any platform service |

  Read access is platform-wide so any service can pull either kind of PGN; write access is restricted to the producing service to contain blast radius. The raw-bucket key layout is owned by the main platform — deep-thought just follows the `s3Uri` in each request message and never assumes a specific shape there. The analyzed-bucket layout (`<YYYY>/<MM>/<DD>/<gameId>.pgn`) is owned by deep-thought and is a contract with tactician.
- **Eval POV in PGN.** `%eval` comments are stored from White's POV (Lichess convention): positive = White better, mate as `#N` (positive = White mates).
- **Eval POV in response.** `move.cp` and `move.mate` are also White-POV — the frontend uses these to render an eval bar that flows in a single direction across the game.
- **Classification POV.** Internally we flip evals to the moving side's POV before applying cp-loss thresholds, so a 300cp drop is "blunder" regardless of which side moved.
- **Stockfish on terminal positions.** We don't call the engine on positions that are already checkmate/stalemate. Mate is synthesized as `Eval(mate=±1)`; stalemate/insufficient as `Eval(cp=0)`.
- **AMQP RPC pattern.** Responses go to `properties.reply_to` with the original `correlation_id`. Main platform owns the reply queue lifecycle; deep-thought is stateless about routing.
- **DLX/DLQ.** Decode failures are nacked with `requeue=False` → routed to DLQ via `x-dead-letter-exchange`. Handler exceptions ack the message (response body carries the failure) — re-running won't fix it.
- **Prefetch=1.** Critical. Combined with manual ack, this gives at-least-once delivery with backpressure tied to actual analysis throughput.
- **GPL-3.0-or-later.** Forced by python-chess.
- **Branch workflow.** Work on `dev`, merge to `main` only at deployment.
- **Commit messages.** Single-line conventional commits (`feat:`, `fix:`, `chore:`, `refactor:`, `docs:`). English.
