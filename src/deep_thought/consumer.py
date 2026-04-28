import json
import logging
import time
from collections.abc import Callable

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic, BasicProperties

from deep_thought import metrics
from deep_thought.config import Config
from deep_thought.messages import AnalysisError, AnalysisRequest, AnalysisResponse

log = logging.getLogger(__name__)

Handler = Callable[[AnalysisRequest], AnalysisResponse]


def declare_topology(channel: BlockingChannel, cfg: Config) -> None:
    """Declare DLX/DLQ first, then the request queue with dead-letter routing.

    Topology is idempotent — safe to re-run on every worker startup.
    """
    channel.exchange_declare(exchange=cfg.rabbit.dlx, exchange_type="direct", durable=True)
    channel.queue_declare(queue=cfg.rabbit.dlq, durable=True)
    channel.queue_bind(queue=cfg.rabbit.dlq, exchange=cfg.rabbit.dlx, routing_key="")

    channel.queue_declare(
        queue=cfg.rabbit.request_queue,
        durable=True,
        arguments={
            "x-dead-letter-exchange": cfg.rabbit.dlx,
            "x-dead-letter-routing-key": "",
        },
    )


def run(cfg: Config, handler: Handler) -> None:
    """Block forever consuming analysis requests. One message at a time per worker."""
    params = pika.URLParameters(cfg.rabbit.url)
    connection = pika.BlockingConnection(params)
    channel = connection.channel()

    declare_topology(channel, cfg)
    channel.basic_qos(prefetch_count=1)

    log.info("worker ready; consuming queue=%s", cfg.rabbit.request_queue)

    for method, properties, body in channel.consume(
        queue=cfg.rabbit.request_queue, auto_ack=False, inactivity_timeout=None
    ):
        if method is None:
            continue
        _process_one(channel, method, properties, body, handler)


def _process_one(
    channel: BlockingChannel,
    method: Basic.Deliver,
    properties: BasicProperties,
    body: bytes,
    handler: Handler,
) -> None:
    """Decode → handle → publish reply → ack. On unexpected failures, nack to DLQ."""
    started = time.perf_counter()
    try:
        payload = json.loads(body)
        req = AnalysisRequest.model_validate(payload)
    except Exception:
        log.exception("failed to decode message; dead-lettering")
        metrics.messages_consumed_total.labels(status="decode_error").inc()
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    log.info("processing request_id=%s game_id=%s", req.request_id, req.game_id)

    try:
        response = handler(req)
    except Exception as exc:
        log.exception("handler raised; sending failed response and acking")
        response = AnalysisResponse(
            request_id=req.request_id,
            game_id=req.game_id,
            status="failed",
            error=AnalysisError(code="UNEXPECTED", message=str(exc)),
        )

    _publish_reply(channel, properties, response)
    channel.basic_ack(delivery_tag=method.delivery_tag)

    metrics.message_processing_seconds.observe(time.perf_counter() - started)
    metrics.messages_consumed_total.labels(
        status="success" if response.status == "success" else "failure"
    ).inc()


def _publish_reply(
    channel: BlockingChannel,
    properties: BasicProperties,
    response: AnalysisResponse,
) -> None:
    """Publish via the inbound message's reply_to (AMQP RPC convention).

    If reply_to is missing we log and drop; main platform should always set it.
    """
    if not properties.reply_to:
        log.warning("inbound message has no reply_to; response dropped")
        return

    body = response.model_dump_json(by_alias=True).encode("utf-8")
    channel.basic_publish(
        exchange="",
        routing_key=properties.reply_to,
        body=body,
        properties=BasicProperties(
            content_type="application/json",
            correlation_id=properties.correlation_id,
            delivery_mode=2,  # persistent
        ),
    )
