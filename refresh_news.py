"""Collect keyword-matched company news from a public Google News RSS search.

Input CSV columns: ticker,company,sector[,in_kospi200]
Output JSON: news_data.json with one normalized article list for the dashboard.

This collector is intentionally conservative: it stores title, link, source,
published time, matched keywords and a transparent keyword score. It does not
copy article bodies.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


KST = timezone(timedelta(hours=9))
MAJOR_SOURCE_HINTS = (
    "연합뉴스", "뉴시스", "뉴스1", "한국경제", "한경", "매일경제", "머니투데이",
    "서울경제", "이데일리", "파이낸셜뉴스", "전자신문", "조선비즈", "아시아경제",
    "뉴스핌", "비즈워치", "헤럴드경제", "아이뉴스24", "디지털타임스", "더벨",
    "딜사이트", "서울신문", "한국일보", "동아일보", "조선일보", "중앙일보",
    "경향신문", "한겨레", "데일리안"
)
MIN_MAJOR_ARTICLES = 3


DEFAULT_KEYWORDS = [
    {"label": "흑자", "weight": 4, "group": "실적"},
    {"label": "대폭개선", "weight": 4, "group": "실적"},
    {"label": "단독", "weight": 2, "group": "독점성"},
    {"label": "독점", "weight": 4, "group": "독점성"},
    {"label": "상용화", "weight": 3, "group": "사업화"},
    {"label": "진출", "weight": 2, "group": "확장"},
    {"label": "수주", "weight": 3, "group": "수익화"},
    {"label": "공급계약", "weight": 3, "group": "수익화"},
    {"label": "실적개선", "weight": 3, "group": "실적"},
    {"label": "신규고객", "weight": 2, "group": "확장"},
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect public keyword-matched news for KOSPI200 companies")
    parser.add_argument("--input", required=True, help="CSV with ticker,company,sector columns")
    parser.add_argument("--output", default="news_data.json")
    parser.add_argument("--days", type=int, default=3, help="Keep articles newer than this many days")
    parser.add_argument("--max-items", type=int, default=20, help="Maximum RSS items per company query")
    parser.add_argument("--delay", type=float, default=1.2, help="Delay between company queries")
    return parser.parse_args()


def load_universe(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"ticker", "company", "sector"}
    missing = required.difference(rows[0].keys() if rows else set())
    if missing:
        raise ValueError(f"Input CSV is missing columns: {', '.join(sorted(missing))}")
    return [row for row in rows if row.get("company", "").strip()]


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def child_text(item: ET.Element, name: str) -> str:
    for child in list(item):
        if local_name(child.tag) == name:
            return " ".join("".join(child.itertext()).split())
    return ""


def normalize_date(value: str) -> tuple[str, str]:
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(KST)
        return parsed.date().isoformat(), parsed.strftime("%H:%M")
    except (TypeError, ValueError, OverflowError):
        now = datetime.now(KST)
        return now.date().isoformat(), now.strftime("%H:%M")


def fetch_feed(company: str, keywords: list[dict[str, object]], max_items: int) -> tuple[str, list[dict[str, str]]]:
    query = f'"{company}" (' + " OR ".join(keyword["label"] for keyword in keywords) + ")"
    url = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=ko&gl=KR&ceid=KR:ko"
    request = Request(url, headers={"User-Agent": "KOSPI200-Research-Dashboard/1.0"})
    with urlopen(request, timeout=20) as response:
        payload = response.read()
    root = ET.fromstring(payload)
    items = []
    for node in root.iter():
        if local_name(node.tag) != "item":
            continue
        title = child_text(node, "title")
        link = child_text(node, "link")
        published = child_text(node, "pubDate")
        source = child_text(node, "source") or "Google News"
        description = child_text(node, "description")
        # 핵심 키워드는 기사 요약이 아니라 제목에 직접 등장한 경우만 인정한다.
        title_text = title.lower()
        matched = [keyword for keyword in keywords if keyword["label"].lower() in title_text]
        if not matched:
            continue
        date, clock = normalize_date(published)
        items.append({
            "title": re.sub(r"\s+", " ", title).strip(),
            "link": link,
            "source": source,
            "published_raw": published,
            "date": date,
            "time": clock,
            "matched_keywords": [keyword["label"] for keyword in matched],
            "news_score": sum(int(keyword["weight"]) for keyword in matched),
        })
        if len(items) >= max_items:
            break
    return url, items


def article_id(article: dict[str, object], ticker: str) -> str:
    basis = f"{ticker}|{article.get('link')}|{article.get('title')}|{article.get('date')}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    args = parse_args()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    universe = load_universe(input_path)
    keywords = DEFAULT_KEYWORDS
    cutoff = datetime.now(KST).date() - timedelta(days=max(args.days, 0))
    articles: list[dict[str, object]] = []
    source_urls: dict[str, str] = {}
    failures: list[dict[str, str]] = []

    for index, row in enumerate(universe):
        ticker = row.get("ticker", "").strip()
        company = row.get("company", "").strip()
        try:
            source_url, found = fetch_feed(company, keywords, args.max_items)
            source_urls[ticker] = source_url
            for article in found:
                try:
                    article_date = datetime.fromisoformat(str(article["date"])).date()
                except ValueError:
                    article_date = datetime.now(KST).date()
                if article_date < cutoff:
                    continue
                article.update({
                    "id": article_id(article, ticker),
                    "ticker": ticker,
                    "company": company,
                    "sector": row.get("sector", "").strip(),
                })
                articles.append(article)
        except Exception as exc:  # keep one failed feed from stopping the universe
            failures.append({"ticker": ticker, "company": company, "error": str(exc)[:240]})
        if index + 1 < len(universe):
            time.sleep(max(args.delay, 0))

    def normalized_title(value: object) -> str:
        text = re.sub(r"\([^)]*(?:종합|속보|단독)[^)]*\)", "", str(value or "").lower())
        return re.sub(r"[^0-9a-z가-힣]+", "", text)

    # 같은 제목이 여러 피드에 반복되어도 하나의 기사로 합친다.
    unique: dict[str, dict[str, object]] = {}
    for article in articles:
        key = f"{article.get('ticker')}|{normalized_title(article.get('title'))}"
        current = unique.get(key)
        if current is None:
            current = dict(article)
            current["matched_keywords"] = list(article.get("matched_keywords", []))
            current["sources"] = [article.get("source", "")]
            unique[key] = current
        else:
            current["matched_keywords"] = sorted(set(current.get("matched_keywords", [])) | set(article.get("matched_keywords", [])))
            current["sources"] = sorted(set(current.get("sources", [])) | {article.get("source", "")})
            current["news_score"] = sum(int(keyword.get("weight", 0)) for keyword in keywords if keyword["label"] in current["matched_keywords"])
    articles = sorted(unique.values(), key=lambda item: (str(item.get("date", "")), str(item.get("time", ""))), reverse=True)

    signal_counts: dict[tuple[str, str], set[str]] = {}
    for article in articles:
        source_text = " ".join(str(source) for source in article.get("sources", []))
        if not any(hint in source_text for hint in MAJOR_SOURCE_HINTS):
            continue
        title_key = normalized_title(article.get("title"))
        for keyword in article.get("matched_keywords", []):
            signal_counts.setdefault((str(article.get("ticker")), str(keyword)), set()).add(title_key)

    signal_keywords: dict[str, list[str]] = {}
    for (ticker, keyword), titles in signal_counts.items():
        if len(titles) >= MIN_MAJOR_ARTICLES:
            signal_keywords.setdefault(ticker, []).append(keyword)
    signal_keywords = {ticker: sorted(values) for ticker, values in signal_keywords.items()}

    for article in articles:
        article["signal_keywords"] = [
            keyword for keyword in article.get("matched_keywords", [])
            if keyword in signal_keywords.get(str(article.get("ticker")), [])
        ]
        article["signal_major_article_count"] = {
            keyword: len(signal_counts.get((str(article.get("ticker")), keyword), set()))
            for keyword in article.get("signal_keywords", [])
        }

    result = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "status": "LIVE" if not failures else "PARTIAL",
        "source": "Google News RSS public search; article body not copied",
        "keywords": [keyword["label"] for keyword in keywords],
        "keyword_definitions": keywords,
        "signal_rule": {
            "min_major_articles": MIN_MAJOR_ARTICLES,
            "match_scope": "title",
            "deduplication": "company + normalized title",
            "major_sources": list(MAJOR_SOURCE_HINTS),
        },
        "signal_keywords_by_ticker": signal_keywords,
        "source_urls": source_urls,
        "failures": failures,
        "articles": articles,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(articles)} matched articles for {len(universe)} companies to {output_path}")
    if failures:
        print(f"Partial failures: {len(failures)}")


if __name__ == "__main__":
    main()
