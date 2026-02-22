# 네이버 증권 종목 뉴스 크롤링 모듈
import asyncio
import logging
import time
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, list[dict]]] = {}
_TTL = 300 # 5분
_last_request: float = 0
_REQUEST_INTERVAL = 1.0 # 요청 간격

# 네이버 증권 종목 뉴스 크롤링
async def fetch_news(code: str, count: int = 10) -> list[dict]:
    global _last_request

    key = f"{code}:{count}"
    cached = _cache.get(key)
    if cached and time.time() < cached[0]:
        return cached[1]

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

    _cache[key] = (time.time() + _TTL, articles)
    return articles
