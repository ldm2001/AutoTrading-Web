# 네이버 증권 종목 뉴스 크롤링 모듈
import asyncio
import logging
import time
import httpx
from bs4 import BeautifulSoup
from service.infra.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

_cache = TTLCache()
_TTL = 300 # 5분
_TTL_EMPTY = 30 # 구조 변경/차단 의심 시 짧은 재시도 간격
_last_request: float = 0
_REQUEST_INTERVAL = 1.0 # 요청 간격

# 네이버 증권 종목 뉴스 크롤링
async def headlines(code: str, count: int = 10) -> list[dict]:
    global _last_request

    key = f"news:{code}:{count}"
    cached = _cache.get(key)
    if cached is not None:
        return cached

    # 요청 간격 제한
    now = time.time()
    wait = _REQUEST_INTERVAL - (now - _last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request = time.time()

    url = (
        f"https://finance.naver.com/item/news_news.naver"
        f"?code={code}&page=1&sm=title_entity_id.basic&clusterId="
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "Referer": f"https://finance.naver.com/item/news.naver?code={code}",
                },
            )
            resp.raise_for_status()
    except Exception as e:
        logger.error(f"News fetch failed for {code}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    articles: list[dict] = []
    seen_titles: set[str] = set()

    for row in soup.select("table.type5 tr"):
        tds = row.select("td")
        if len(tds) < 3:
            continue

        title_el = tds[0].select_one("a")
        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)

        href = title_el.get("href", "")
        if href and not href.startswith("http"):
            href = f"https://finance.naver.com{href}"

        press = tds[1].get_text(strip=True) if len(tds) > 1 else ""
        date = tds[2].get_text(strip=True) if len(tds) > 2 else ""

        articles.append({
            "title": title,
            "summary": "",
            "url": href,
            "press": press,
            "date": date,
        })

        if len(articles) >= count:
            break

    # 기사 0건 + 목록 테이블 부재는 페이지 구조 변경/차단 의심 — 경고 후 짧게 캐시
    if not articles and soup.select_one("table.type5") is None:
        logger.warning(f"News parse empty for {code}: page structure changed or blocked (html {len(resp.text)}b)")
        _cache.set(key, articles, _TTL_EMPTY)
        return articles

    _cache.set(key, articles, _TTL)
    return articles
