# Elasticsearch 주문/거래 로그 인덱서
import datetime
import logging
from typing import Any
from config import settings

logger = logging.getLogger(__name__)

_client = None

def _es():
    global _client
    if _client is not None:
        return _client
    try:
        from elasticsearch import Elasticsearch
        _client = Elasticsearch(
            settings.elasticsearch_url,
            request_timeout=5,
            max_retries=1,
            verify_certs=False,
            ssl_show_warn=False,
        )
        if _client.ping():
            logger.info("Elasticsearch connected: %s", settings.elasticsearch_url)
        else:
            logger.warning("Elasticsearch ping failed")
            _client = None
    except Exception as e:
        logger.warning("Elasticsearch unavailable: %s", e)
        _client = None
    return _client

# 주문 로그를 ES에 인덱싱
def index_order(entry: dict[str, Any]) -> None:
    es = _es()
    if es is None:
        return
    try:
        doc = dict(entry)
        doc["@timestamp"] = doc.get("time") or datetime.datetime.now().isoformat()
        es.index(index="orders", document=doc)
    except Exception as e:
        logger.debug("ES index_order failed: %s", e)

# 틱 데이터를 ES에 벌크 인덱싱
def index_tick(code: str, price: int, volume: int, ts: datetime.datetime) -> None:
    es = _es()
    if es is None:
        return
    try:
        es.index(index="ticks", document={
            "@timestamp": ts.isoformat(),
            "code": code,
            "price": price,
            "volume": volume,
        })
    except Exception:
        pass

# 봇 이벤트 로그 (시작/종료/에러 등)
def index_event(event_type: str, detail: str = "", **extra: Any) -> None:
    es = _es()
    if es is None:
        return
    try:
        doc = {
            "@timestamp": datetime.datetime.now().isoformat(),
            "event": event_type,
            "detail": detail,
            **extra,
        }
        es.index(index="bot-events", document=doc)
    except Exception:
        pass
