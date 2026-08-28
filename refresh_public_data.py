"""Login-free public data refresh for the KOSPI200 valuation dashboard.

Primary feed: Naver Finance public pages and public chart XML.
This intentionally leaves history-dependent fields blank unless a BPS history CSV
is supplied, because a PBR band must be built from price and BPS on a consistent basis.

Input universe CSV columns:
    ticker,company,sector

Example:
    python refresh_public_data.py --input universe.csv \
        --output public_financials.csv \
        --price-history-output price_history.csv \
        --bps-history bps_history.csv

The output is an auditable CSV, not an investment recommendation. Respect the
source site's terms, robots rules, rate limits, and redistribution restrictions.
"""

from __future__ import annotations

import argparse
from html import unescape
import io
import re
import ssl
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pandas as pd


USER_AGENT = "Mozilla/5.0 (compatible; KOSPI200ValueDashboard/1.0)"
NAVER_MAIN = "https://finance.naver.com/item/main.naver?code={ticker}"
NAVER_CHART = "https://fchart.stock.naver.com/sise.nhn?symbol={ticker}&timeframe=week&count={count}&requestType=0"

BUSINESS_KEYWORD_RULES = [
    ("반도체", ("반도체", "메모리", "파운드리", "foundry")),
    ("디스플레이", ("디스플레이", "oled", "lcd", "패널")),
    ("스마트폰", ("스마트폰", "모바일", "휴대폰")),
    ("가전·TV", ("가전", "텔레비전", "tv", "생활가전")),
    ("전장부품", ("전장", "자동차 부품", "자동차부품", "오디오")),
    ("배터리", ("배터리", "이차전지", "전지")),
    ("자동차", ("자동차", "완성차", "차량")),
    ("조선·해양", ("조선", "선박", "해양플랜트")),
    ("방산", ("방산", "방위산업", "무기체계")),
    ("건설·인프라", ("건설", "인프라", "플랜트", "토목")),
    ("금융·보험", ("은행", "금융", "보험", "증권")),
    ("바이오·제약", ("바이오", "제약", "의약품", "신약")),
    ("플랫폼·인터넷", ("플랫폼", "인터넷", "검색", "콘텐츠")),
    ("유통·물류", ("유통", "물류", "백화점", "온라인 쇼핑")),
    ("화학·소재", ("화학", "소재", "정밀화학", "석유화학")),
    ("전력·에너지", ("전력", "에너지", "발전", "가스")),
]


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
        return response.read()


def decode_html(payload: bytes) -> str:
    for encoding in ("euc-kr", "utf-8", "cp949"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def numeric(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).replace("\xa0", " ").strip()
    if not text or text.lower() == "nan":
        return None
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def numbers_in(value) -> list[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).replace("\xa0", " ").strip()
    values = []
    for token in re.findall(r"-?\d[\d,]*(?:\.\d+)?", text):
        try:
            values.append(float(token.replace(",", "")))
        except ValueError:
            pass
    return values


def normalized_tables(html: str) -> list[pd.DataFrame]:
    return pd.read_html(io.StringIO(html))


def visible_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def extract_business_info(html: str) -> tuple[str | None, list[str]]:
    text = visible_text(html)
    marker = text.find("기업개요")
    if marker < 0:
        return None, []
    overview = text[marker + len("기업개요"):]
    stop_positions = [position for position in (overview.find("출처 :"), overview.find("출처:")) if position >= 0]
    if stop_positions:
        overview = overview[:min(stop_positions)]
    overview = re.sub(r"\s+", " ", overview).strip(" -:|")
    lowered = overview.lower()
    found: list[tuple[int, int, str]] = []
    for label, terms in BUSINESS_KEYWORD_RULES:
        positions = [lowered.find(term.lower()) for term in terms if lowered.find(term.lower()) >= 0]
        if positions:
            found.append((min(positions), -max(len(term) for term in terms), label))
    found.sort()
    return overview[:1000] or None, [label for _, _, label in found[:3]]


def parse_main_page(ticker: str) -> dict:
    url = NAVER_MAIN.format(ticker=ticker)
    html = decode_html(fetch_bytes(url))
    tables = normalized_tables(html)
    result = {
        "trailing_per": None,
        "trailing_eps": None,
        "forward_per_consensus": None,
        "forward_eps_consensus": None,
        "current_pbr": None,
        "current_bps": None,
        "close": None,
        "business_overview": None,
        "business_keywords": [],
    }
    result["business_overview"], result["business_keywords"] = extract_business_info(html)

    for table in tables:
        if table.empty:
            continue
        # Naver's compact investment table is stable in shape even when the
        # Korean label encoding changes: trailing PER/EPS, estimated PER/EPS,
        # PBR/BPS, dividend yield.
        if table.shape[0] >= 3 and table.shape[1] >= 2:
            first_labels = [str(v) for v in table.iloc[:3, 0].tolist()]
            if "PER" in first_labels[0] and "PER" in first_labels[1] and "PBR" in first_labels[2]:
                row_values = []
                for row_idx in range(3):
                    values = []
                    for cell in table.iloc[row_idx, 1:].tolist():
                        values.extend(numbers_in(cell))
                    row_values.append(values)
                if row_values[0]:
                    result["trailing_per"] = row_values[0][0]
                    if len(row_values[0]) > 1:
                        result["trailing_eps"] = row_values[0][1]
                if row_values[1]:
                    result["forward_per_consensus"] = row_values[1][0]
                    if len(row_values[1]) > 1:
                        result["forward_eps_consensus"] = row_values[1][1]
                if row_values[2]:
                    result["current_pbr"] = row_values[2][0]
                    if len(row_values[2]) > 1:
                        result["current_bps"] = row_values[2][1]
        labels = table.iloc[:, 0].astype(str).tolist()
        for row_idx, label in enumerate(labels):
            label = str(label)
            cells = [str(v) for v in table.iloc[row_idx, 1:].tolist()]
            cell_text = " ".join(cells)
            values = [numeric(v) for v in cells]
            values = [v for v in values if v is not None]
            if not values:
                continue
            if "추정PER/EPS" in label or "추정 PER/EPS" in label:
                result["forward_per_consensus"] = values[0]
                if len(values) > 1:
                    result["forward_eps_consensus"] = values[1]
            elif "PER/EPS" in label and "추정" not in label:
                result["trailing_per"] = values[0]
                if len(values) > 1:
                    result["trailing_eps"] = values[1]
            elif "PBR/BPS" in label:
                result["current_pbr"] = values[0]
                if len(values) > 1:
                    result["current_bps"] = values[1]
            elif "현재가" in label and result["close"] is None:
                result["close"] = values[0]

    if result["forward_eps_consensus"] is not None and result["current_bps"] is not None and result["current_bps"] > 0:
        result["roe_estimate"] = round(result["forward_eps_consensus"] / result["current_bps"] * 100, 2)
    elif result["trailing_eps"] is not None and result["current_bps"] is not None and result["current_bps"] > 0:
        result["roe_current"] = round(result["trailing_eps"] / result["current_bps"] * 100, 2)

    result["source_url"] = url
    result["source_id"] = "NAVER_PUBLIC"
    result["quality_flag"] = "OK" if result["close"] is not None else "FAILED"
    return result


def parse_price_history(ticker: str, count: int) -> list[dict]:
    url = NAVER_CHART.format(ticker=ticker, count=count)
    payload = fetch_bytes(url)
    root = ET.fromstring(decode_html(payload))
    rows = []
    for item in root.findall(".//item"):
        data = item.attrib.get("data", "").split("|")
        if len(data) != 6:
            continue
        rows.append({
            "ticker": ticker,
            "date": data[0],
            "open": numeric(data[1]),
            "high": numeric(data[2]),
            "low": numeric(data[3]),
            "close": numeric(data[4]),
            "volume": numeric(data[5]),
            "source_id": "NAVER_CHART_PUBLIC",
            "source_url": url,
        })
    return rows



def build_pbr_bands(price_history: pd.DataFrame, bps_history_path: str | None, current_rows: list[dict] | None = None) -> pd.DataFrame:
    columns = ["ticker", "pbr_5y_min", "pbr_band_low", "pbr_band_high", "current_pbr_from_history"]
    if price_history.empty:
        return pd.DataFrame(columns=columns)
    if bps_history_path:
        bps = pd.read_csv(bps_history_path, dtype={"ticker": str})
    else:
        bps = pd.DataFrame([
            {"ticker": row.get("ticker"), "date": "1900-01-01", "bps": row.get("current_bps")}
            for row in (current_rows or [])
            if row.get("current_bps") not in (None, "")
        ])
    required = {"ticker", "date", "bps"}
    if bps.empty or required.difference(bps.columns):
        return pd.DataFrame(columns=columns)
    prices = price_history.copy()
    prices["date"] = pd.to_datetime(prices["date"])
    bps["date"] = pd.to_datetime(bps["date"])
    prices = prices.sort_values(["date", "ticker"])
    bps = bps.sort_values(["date", "ticker"])
    merged = pd.merge_asof(prices, bps, on="date", by="ticker", direction="backward")
    merged = merged[merged["bps"].notna() & (merged["bps"] > 0)].copy()
    if merged.empty:
        return pd.DataFrame(columns=columns)
    merged["pbr"] = merged["close"] / merged["bps"]
    return merged.groupby("ticker", as_index=False).agg(
        pbr_5y_min=("pbr", "min"),
        pbr_band_low=("pbr", lambda s: s.quantile(0.20)),
        pbr_band_high=("pbr", lambda s: s.quantile(0.80)),
        current_pbr_from_history=("pbr", "last"),
    )

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="CSV with ticker,company,sector")
    parser.add_argument("--output", required=True, help="Normalized financial output CSV")
    parser.add_argument("--price-history-output", required=True, help="Weekly Naver price history CSV")
    parser.add_argument("--bps-history", default=None, help="Optional CSV with ticker,date,bps")
    parser.add_argument("--history-count", type=int, default=4000)
    parser.add_argument("--delay", type=float, default=1.2)
    args = parser.parse_args()

    universe = pd.read_csv(args.input, dtype={"ticker": str})
    required = {"ticker", "company", "sector"}
    missing = required.difference(universe.columns)
    if missing:
        raise ValueError(f"input missing columns: {sorted(missing)}")
    universe["ticker"] = universe["ticker"].str.replace(r"\D", "", regex=True).str.zfill(6)

    financial_rows = []
    price_rows = []
    run_date = date.today().isoformat()
    for _, row in universe.iterrows():
        ticker = row["ticker"]
        try:
            metrics = parse_main_page(ticker)
            if metrics.get("current_bps") in (None, ""):
                close = metrics.get("close")
                current_pbr = metrics.get("current_pbr")
                if close not in (None, "") and current_pbr not in (None, "") and float(current_pbr) > 0:
                    metrics["current_bps"] = round(float(close) / float(current_pbr), 2)
            if metrics.get("roe_estimate") is None and metrics.get("forward_eps_consensus") not in (None, "") and metrics.get("current_bps") not in (None, "") and float(metrics["current_bps"]) > 0:
                metrics["roe_estimate"] = round(float(metrics["forward_eps_consensus"]) / float(metrics["current_bps"]) * 100, 2)
            metrics.update({
                "as_of": run_date,
                "ticker": ticker,
                "company": row["company"],
                "sector": row["sector"],
                "in_kospi200": "Y",
                "py_end_per": None,
                "py_end_eps": None,
                "roe_2y": None,
                "roe_1y": None,
                "roe_current": None,
                "roe_estimate": None,
                "business_overview": metrics.get("business_overview"),
                "business_keywords": ";".join(metrics.get("business_keywords", [])),
                "pbr_band_low": None,
                "pbr_band_high": None,
                "update_note": "Public Naver pull; history-dependent fields require BPS history input.",
            })
        except Exception as exc:  # noqa: BLE001
            metrics = {
                "as_of": run_date,
                "ticker": ticker,
                "company": row["company"],
                "sector": row["sector"],
                "in_kospi200": "Y",
                "quality_flag": "FAILED",
                "source_id": "NAVER_PUBLIC",
                "source_url": NAVER_MAIN.format(ticker=ticker),
                "business_overview": None,
                "business_keywords": "",
                "update_note": f"Fetch failed: {type(exc).__name__}",
            }
        financial_rows.append(metrics)
        try:
            price_rows.extend(parse_price_history(ticker, args.history_count))
        except Exception:
            pass
        time.sleep(max(args.delay, 0.2))

    price_history = pd.DataFrame(price_rows)
    bands = build_pbr_bands(price_history, args.bps_history, financial_rows)
    if not bands.empty:
        financial = pd.DataFrame(financial_rows).merge(bands, on="ticker", how="left", suffixes=("", "_derived"))
        financial["pbr_band_low"] = financial["pbr_band_low_derived"].combine_first(financial["pbr_band_low"])
        financial["pbr_band_high"] = financial["pbr_band_high_derived"].combine_first(financial["pbr_band_high"])
        derived_min = financial.get("pbr_5y_min_derived", pd.Series(index=financial.index, dtype="float64"))
        existing_min = financial.get("pbr_5y_min", pd.Series(index=financial.index, dtype="float64"))
        financial["pbr_5y_min"] = derived_min.combine_first(existing_min)
        financial = financial.drop(columns=[c for c in financial.columns if c.endswith("_derived")])
    else:
        financial = pd.DataFrame(financial_rows)
    financial.to_csv(args.output, index=False, encoding="utf-8-sig")
    price_history.to_csv(args.price_history_output, index=False, encoding="utf-8-sig")
    print(f"wrote {len(financial)} financial rows to {args.output}")
    print(f"wrote {len(price_history)} price rows to {args.price_history_output}")
    print("Note: this collector does not scrape CompanyGuide in bulk; use it only as a manual/contracted validation source.")


if __name__ == "__main__":
    main()
