import logging

from deep_thought import analysis, consumer
from deep_thought.config import load_config
from deep_thought.messages import AnalysisRequest, AnalysisResponse
from deep_thought.metrics import start_metrics_server
from deep_thought.s3 import S3Client


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config()
    start_metrics_server(cfg.metrics.port)
    s3 = S3Client(cfg.s3)

    def handle(req: AnalysisRequest) -> AnalysisResponse:
        return analysis.run_analysis(req, cfg, s3)

    consumer.run(cfg, handle)


if __name__ == "__main__":
    main()
