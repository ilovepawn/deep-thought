import logging

from prometheus_client import Counter, Histogram, start_http_server

log = logging.getLogger(__name__)


# Per-message lifecycle. `status` is the terminal disposition of an inbound
# delivery: success / failure (handler returned a `failed` response or raised) /
# decode_error (json or pydantic validation failed before the handler ran).
messages_consumed_total = Counter(
    "dt_messages_consumed_total",
    "AMQP messages consumed by terminal status.",
    labelnames=("status",),
)

# End-to-end processing latency for a single message: from decode through
# handler return through reply publish. Buckets sized for full-game analysis,
# which can run from sub-second (cache hit) to a few minutes (long game, deep
# search).
message_processing_seconds = Histogram(
    "dt_message_processing_seconds",
    "End-to-end time to process one analysis request.",
    buckets=(0.05, 0.25, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
)

# Cache effectiveness. `result` is hit (annotated PGN already at the
# deterministic key, no Stockfish run) or miss.
analysis_cache_total = Counter(
    "dt_analysis_cache_total",
    "Annotated-PGN cache lookups by outcome.",
    labelnames=("result",),
)

# Stockfish per-position evaluation latency. At depth 18, single positions
# typically land in the 0.1–2 s range; the long tail is what we care about for
# tuning depth/threads.
stockfish_analyse_seconds = Histogram(
    "dt_stockfish_analyse_seconds",
    "Time for a single engine analyse call (one position).",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# S3 operations. Latency split by op (get/put/head); a separate counter tracks
# ok vs error so error rate is queryable without caring about latency buckets.
s3_operations_seconds = Histogram(
    "dt_s3_operations_seconds",
    "Latency of S3 client operations.",
    labelnames=("op",),
    buckets=(0.005, 0.025, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
s3_operations_total = Counter(
    "dt_s3_operations_total",
    "S3 client operations by outcome.",
    labelnames=("op", "result"),
)


def start_metrics_server(port: int) -> None:
    start_http_server(port)
    log.info("metrics server listening on :%d/metrics", port)
