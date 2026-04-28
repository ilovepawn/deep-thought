import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class RabbitConfig:
    url: str
    request_queue: str
    dlx: str
    dlq: str


@dataclass(frozen=True)
class S3Config:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket_analyses: str
    region: str


@dataclass(frozen=True)
class StockfishConfig:
    path: str
    threads: int
    hash_mb: int
    depth: int


@dataclass(frozen=True)
class MetricsConfig:
    port: int


@dataclass(frozen=True)
class Config:
    rabbit: RabbitConfig
    s3: S3Config
    stockfish: StockfishConfig
    metrics: MetricsConfig


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"required env var missing: {key}")
    return value


def load_config() -> Config:
    load_dotenv()
    return Config(
        rabbit=RabbitConfig(
            url=_require("RABBITMQ_URL"),
            request_queue=_require("RABBITMQ_REQUEST_QUEUE"),
            dlx=_require("RABBITMQ_DLX"),
            dlq=_require("RABBITMQ_DLQ"),
        ),
        s3=S3Config(
            endpoint_url=_require("S3_ENDPOINT_URL"),
            access_key=_require("S3_ACCESS_KEY"),
            secret_key=_require("S3_SECRET_KEY"),
            bucket_analyses=_require("S3_BUCKET_ANALYSES"),
            region=os.environ.get("S3_REGION", "us-east-1"),
        ),
        stockfish=StockfishConfig(
            path=_require("STOCKFISH_PATH"),
            threads=int(os.environ.get("STOCKFISH_THREADS", "4")),
            hash_mb=int(os.environ.get("STOCKFISH_HASH_MB", "256")),
            depth=int(os.environ.get("STOCKFISH_DEPTH", "18")),
        ),
        metrics=MetricsConfig(
            port=int(os.environ.get("METRICS_PORT", "9100")),
        ),
    )
