from dataclasses import dataclass
from urllib.parse import urlparse

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from deep_thought.config import S3Config


@dataclass(frozen=True)
class S3Uri:
    bucket: str
    key: str

    def to_str(self) -> str:
        return f"s3://{self.bucket}/{self.key}"

    @classmethod
    def parse(cls, uri: str) -> "S3Uri":
        parsed = urlparse(uri)
        if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
            raise ValueError(f"invalid s3 uri: {uri}")
        return cls(bucket=parsed.netloc, key=parsed.path.lstrip("/"))


class S3Client:
    def __init__(self, cfg: S3Config) -> None:
        self._client: BaseClient = boto3.client(
            "s3",
            endpoint_url=cfg.endpoint_url,
            aws_access_key_id=cfg.access_key,
            aws_secret_access_key=cfg.secret_key,
            region_name=cfg.region,
        )
        self._bucket_analyses = cfg.bucket_analyses

    def get_object_text(self, uri: S3Uri) -> str:
        resp = self._client.get_object(Bucket=uri.bucket, Key=uri.key)
        return resp["Body"].read().decode("utf-8")

    def put_object_text(self, uri: S3Uri, body: str) -> None:
        self._client.put_object(
            Bucket=uri.bucket,
            Key=uri.key,
            Body=body.encode("utf-8"),
            ContentType="application/x-chess-pgn",
        )

    def object_exists(self, uri: S3Uri) -> bool:
        try:
            self._client.head_object(Bucket=uri.bucket, Key=uri.key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            raise

    def analysis_uri(self, game_id: str, played_yyyy: str, played_mm: str, played_dd: str) -> S3Uri:
        key = f"{played_yyyy}/{played_mm}/{played_dd}/{game_id}.pgn"
        return S3Uri(bucket=self._bucket_analyses, key=key)
