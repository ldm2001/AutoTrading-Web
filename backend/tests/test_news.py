import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.ai.news as news_module


_ROWS_HTML = """
<html><body><table class="type5">
<tr><td><a href="/item/news_read.naver?id=1">첫 기사</a></td><td>언론사</td><td>2026.07.10</td></tr>
</table></body></html>
"""

_EMPTY_TABLE_HTML = '<html><body><table class="type5"><tr><td>x</td></tr></table></body></html>'

_BLOCKED_HTML = "<html><body>blocked</body></html>"


# 고정 HTML을 돌려주는 httpx.AsyncClient 스텁
class _Client:
    html = ""
    boom = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        if type(self).boom:
            raise RuntimeError("down")

        class _Resp:
            text = type(self).html

            def raise_for_status(self):
                return None

        return _Resp()


# 인메모리 캐시 강제 + 요청 간격 초기화 후 headlines 실행
def fetch(code: str):
    news_module._cache._redis = None
    news_module._last_request = 0
    with mock.patch.object(news_module.httpx, "AsyncClient", _Client):
        return asyncio.run(news_module.headlines(code))


class HeadlinesTest(unittest.TestCase):
    def setUp(self):
        news_module._cache._local.clear()
        _Client.boom = False

    # 정상 파싱은 기사 반환 + 기본 TTL 캐시
    def test_parse_rows(self):
        _Client.html = _ROWS_HTML
        articles = fetch("005930")
        self.assertEqual(articles[0]["title"], "첫 기사")
        expires_at, _ = news_module._cache._local["news:005930:10"]
        self.assertGreater(expires_at - time.time(), news_module._TTL_EMPTY)

    # 테이블은 있고 기사만 없으면 정상 빈 결과로 기본 TTL 캐시 (경고 없음)
    def test_empty_table_normal(self):
        _Client.html = _EMPTY_TABLE_HTML
        articles = fetch("068270")
        self.assertEqual(articles, [])
        expires_at, _ = news_module._cache._local["news:068270:10"]
        self.assertGreater(expires_at - time.time(), news_module._TTL_EMPTY)

    # 목록 테이블 부재는 구조 변경/차단 의심 — 경고 + 짧은 TTL
    def test_structure_missing(self):
        _Client.html = _BLOCKED_HTML
        with self.assertLogs(news_module.logger, level="WARNING"):
            articles = fetch("000660")
        self.assertEqual(articles, [])
        expires_at, _ = news_module._cache._local["news:000660:10"]
        self.assertLessEqual(expires_at - time.time(), news_module._TTL_EMPTY)

    # HTTP 실패는 캐시 없이 빈 목록
    def test_fetch_error(self):
        _Client.boom = True
        self.assertEqual(fetch("035420"), [])
        self.assertNotIn("news:035420:10", news_module._cache._local)


if __name__ == "__main__":
    unittest.main()
