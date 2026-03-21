# Prometheus 커스텀 메트릭 정의
from prometheus_client import Counter, Gauge, Histogram

# 틱 큐
tick_queue_size = Gauge(
    "tick_queue_size",
    "Current tick queue depth",
)
tick_queue_drops = Counter(
    "tick_queue_drops_total",
    "Ticks dropped due to queue full",
)
candle_ingest = Counter(
    "candle_ingest_total",
    "Candles ingested into store",
    ["interval"],
)

# WebSocket
ws_reconnect = Counter(
    "ws_reconnect_total",
    "KIS WebSocket reconnection count",
)
ws_clients = Gauge(
    "ws_clients_connected",
    "Connected WebSocket clients",
    ["channel"],
)

# 주문
order_latency = Histogram(
    "order_latency_seconds",
    "Order execution latency",
    ["side"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)
order_result = Counter(
    "order_result_total",
    "Order results",
    ["side", "success"],
)

# 봇
bot_scan_duration = Histogram(
    "bot_scan_duration_seconds",
    "Strategy scan loop duration",
    buckets=[1, 5, 10, 30, 60],
)
bot_holdings = Gauge(
    "bot_holdings_count",
    "Current bot holding positions",
)

# 캐시
cache_hit = Counter(
    "cache_hit_total",
    "Cache hits",
)
cache_miss = Counter(
    "cache_miss_total",
    "Cache misses",
)
