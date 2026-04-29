# Deep Thought

[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![RabbitMQ](https://img.shields.io/badge/RabbitMQ-3.13-FF6600?logo=rabbitmq&logoColor=white)](https://www.rabbitmq.com/)
[![MinIO](https://img.shields.io/badge/MinIO-S3--Compatible-C72E49?logo=minio&logoColor=white)](https://min.io/)
[![Stockfish](https://img.shields.io/badge/Stockfish-18-000000?logo=lichess&logoColor=white)](https://stockfishchess.org/)
[![License](https://img.shields.io/badge/License-GPL--3.0--or--later-blue)](LICENSE)

[English](README.md)

> *딥 소트 (Deep Thought)* — Deep Blue의 길을 닦은 1980년대 후반 IBM 체스 컴퓨터, 그리고 더글러스 애덤스의 *은하수를 여행하는 히치하이커를 위한 안내서*에 등장하는 답을 7백 5십만 년 동안 계산한 슈퍼컴퓨터. 우리 것은 그보다 훨씬 빠릅니다.

[ilovepawn](https://github.com/ilovepawn) 체스 플랫폼의 **게임 분석 서비스**. chess.com의 "Game Review"에 해당 — 수별 평가, 분류 (best / good / inaccuracy / mistake / blunder), 진영별 정확도 및 ACPL 요약을 제공합니다.

자매 서비스들과 달리 deep-thought는 HTTP API가 없습니다. RabbitMQ 워커로 동작 — 메인 플랫폼이 분석 요청 메시지를 발행하면, deep-thought가 그것을 소비하여 Stockfish를 돌리고 AMQP `reply_to` 컨벤션으로 응답을 발행합니다.

---

## 동작 방식

1. 사용자가 메인 플랫폼에서 분석을 요청
2. 플랫폼이 raw PGN의 `s3Uri`를 담은 AMQP 메시지를 발행 (`correlation_id`, `reply_to` 설정)
3. deep-thought가 raw PGN을 다운로드하고 Date 헤더를 파싱
4. 결정적 출력 경로에 이미 분석된 PGN이 있으면 → 그대로 재사용 (캐시 히트, Stockfish 미실행)
5. 없으면 → 매 포지션마다 Stockfish 실행, `%eval` 주석 주입, S3에 업로드
6. eval 값으로부터 분류/정확도/ACPL을 재도출 (cheap, ms 단위)
7. 인바운드의 `reply_to`로 원래의 `correlation_id`와 함께 응답 발행

deep-thought가 쓰는 annotated PGN은 [tactician](https://github.com/ilovepawn/tactician)이 일배치로 소비하는 그 산출물 — 버킷 레이아웃은 cross-service 계약입니다.

---

## 기술 스택

| 레이어 | 기술 |
|---|---|
| 체스 엔진 | Stockfish 18 (UCI) |
| 체스 로직 | python-chess |
| 메시지 버스 | RabbitMQ (pika, sync 클라이언트) |
| 오브젝트 스토리지 | MinIO (S3 호환) / AWS S3 |
| 스키마 | Pydantic |
| 패키지 매니저 | uv |
| 옵저버빌리티 | prometheus_client (`:9100/metrics`) |

DB 없음. S3에 저장된 annotated PGN이 단일 소스 오브 트루스이며, 분류와 요약은 매 요청마다 `%eval` 값으로부터 재도출합니다.

---

## 시작하기

deep-thought는 더 이상 자체 `docker-compose.yml`을 두지 않습니다. RabbitMQ / MinIO / 워커 + 자매 서비스 + 옵저버빌리티 스택은 [`ilovepawn/infra`](https://github.com/ilovepawn/infra) 레포에서 통합 관리됩니다. 이 레포의 형제 폴더로 클론한 뒤 인프라 레포에서 띄우세요:

```bash
# infra 레포에서
cd compose
docker compose up -d                                    # 코어 인프라 + 앱
docker compose --profile observability up -d            # Prometheus + Grafana 추가
```

인프라 컴포즈가 `../../deep-thought`를 빌드 컨텍스트로 잡고 있어, 이 레포의 변경은 리빌드만 하면 반영됩니다.

### 호스트 모드 (개발 편의용)

이미지 리빌드 없이 빠르게 반복하려면 인프라가 띄운 RabbitMQ/MinIO에 호스트에서 직접 붙어 실행할 수 있습니다. Python 3.13, [uv](https://docs.astral.sh/uv/), Stockfish (`brew install stockfish`) 필요. 인프라 컴포즈와 같은 환경변수를 (또는 자체 `.env`로) 주입해 주세요:

```bash
uv sync
uv run python -m deep_thought.main
```

### 메트릭

워커는 `:${METRICS_PORT}/metrics` (기본 `9100`)에 Prometheus 메트릭을 노출합니다. 인프라 컴포즈로 띄우면 같은 도커 네트워크의 Prometheus가 자동 스크레이프합니다. 노출 시리즈:

- `dt_messages_consumed_total{status}` — `success` / `failure` / `decode_error`
- `dt_message_processing_seconds` — 메시지 처리 종단 히스토그램
- `dt_analysis_cache_total{result}` — `hit` / `miss`
- `dt_stockfish_analyse_seconds` — 포지션당 엔진 평가 시간
- `dt_s3_operations_seconds{op}` 및 `dt_s3_operations_total{op,result}` — `get` / `put` / `head`

추가로 `prometheus_client` 표준의 `python_*` 시리즈, 그리고 Linux에서 실행 시 `process_*` (CPU·RSS·FD) 시리즈가 함께 제공됩니다.

---

## 메시지 계약

### 인바운드 (`RABBITMQ_REQUEST_QUEUE`에서 소비)

JSON 본문:

```json
{
  "requestId": "req-abc",
  "gameId": "game-123",
  "s3Uri": "s3://ilovepawn-games-raw/game-123.pgn"
}
```

AMQP 속성:
- `correlation_id`: 응답 매칭용 식별자
- `reply_to`: 응답을 발행할 큐

### 아웃바운드 (`properties.reply_to`로 발행)

성공:

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

실패:

```json
{
  "requestId": "req-abc",
  "gameId": "game-123",
  "status": "failed",
  "error": {"code": "INVALID_PGN", "message": "..."}
}
```

`cp`, `mate`는 White-POV (양수 = 백 우세, 메이트는 `±N`). 분류는 cp-loss 임계값 기반: `best` / `good` / `inaccuracy` / `mistake` / `blunder`.

---

## S3 레이아웃 및 소유권

운영 환경은 버킷 2개를 사용하며 쓰기 권한이 서비스별로 분리됩니다:

| 버킷 | 쓰기 | 읽기 | 레이아웃 |
|---|---|---|---|
| `ilovepawn-games-raw` | 메인 플랫폼만 | 플랫폼 내 모든 서비스 | 메인 플랫폼 소유 |
| `ilovepawn-games-analyzed` | deep-thought만 | 플랫폼 내 모든 서비스 | `<YYYY>/<MM>/<DD>/<gameId>.pgn` |

읽기 권한은 플랫폼 전체에 열려 있어 어느 서비스든 PGN을 가져갈 수 있고, 쓰기 권한은 생산자 서비스에만 좁혀 있어 권한 사고의 영향 범위를 최소화합니다. deep-thought는 raw 버킷의 레이아웃을 가정하지 않습니다 — 매 요청 메시지의 `s3Uri`만 따릅니다. analyzed 버킷의 레이아웃은 [tactician](https://github.com/ilovepawn/tactician)과의 계약 — tactician이 일배치로 `<YYYY>/<MM>/<DD>/` 프리픽스를 스캔합니다.

---

## 프로젝트 구조

```
deep-thought/
├── src/deep_thought/
│   ├── main.py          # 진입점: config 로드, 메트릭 서버 기동, 컨슈머 실행
│   ├── consumer.py      # RabbitMQ 토폴로지 + 소비 루프 (prefetch=1, manual ack, DLQ)
│   ├── analysis.py      # 파이프라인 오케스트레이터 (S3 → 캐시 체크 → Stockfish → 업로드)
│   ├── stockfish.py     # UCI 엔진 래퍼
│   ├── pgn.py           # PGN 파싱, 날짜 추출, %eval 주입/읽기
│   ├── classifier.py    # cp-loss 임계값 → best / good / inaccuracy / mistake / blunder
│   ├── summary.py       # Lichess 스타일 정확도 + 진영별 ACPL
│   ├── s3.py            # boto3 클라이언트 + S3Uri 파서 + 결정적 키 빌더
│   ├── messages.py      # Pydantic 스키마 (camelCase JSON)
│   ├── metrics.py       # Prometheus 카운터/히스토그램 + HTTP 서버
│   └── config.py        # 환경 설정
├── Dockerfile           # 멀티스테이지: Stockfish 18 빌더 + Python 런타임
└── pyproject.toml
```

워커 동시성 모델: **워커 1개 = 게임 1개 = Stockfish 1개**, 엄격한 직렬 처리. Stockfish 자체가 멀티스레드(`STOCKFISH_THREADS`)라 워커 단위 동시성을 추가하면 CPU 경합만 생깁니다. 처리량은 `prefetch_count`를 올리는 게 아니라 워커 레플리카 수로 수평 확장합니다.

---

## 라이선스

이 프로젝트는 **GPL-3.0-or-later 라이선스**로 배포됩니다 — 자세한 내용은 [LICENSE](LICENSE) 파일 참고.

`python-chess` (GPL) 의존성으로 인해 GPL-3.0-or-later가 강제됩니다.
