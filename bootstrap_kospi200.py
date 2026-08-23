"""Build the current KOSPI200 universe CSV from Naver's public index constituent pages.

Naver currently exposes the public list in 20 pages. The script keeps the
source snapshot date and URL so the universe can be audited before each run.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen


KST = timezone(timedelta(hours=9))
BASE_URL = "https://finance.naver.com/sise/entryJongmok.naver?code=KOSPI200&page={}"
ROW_RE = re.compile(r'<a href="/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a current KOSPI200 universe CSV")
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "kospi200_universe.csv"))
    parser.add_argument("--valuation-output", default="", help="Optional valuation_data.json initialization path")
    parser.add_argument("--pages", type=int, default=20)
    parser.add_argument("--delay", type=float, default=0.5)
    return parser.parse_args()


def fetch_page(page: int) -> str:
    url = BASE_URL.format(page)
    request = Request(url, headers={"User-Agent": "KOSPI200-Research-Dashboard/1.0"})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("euc-kr", errors="replace")


def load_rows(pages: int, delay: float) -> list[dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for page in range(1, pages + 1):
        html = fetch_page(page)
        for ticker, company in ROW_RE.findall(html):
            rows[ticker] = {"ticker": ticker, "company": re.sub(r"\s+", " ", company).strip(), "sector": "", "in_kospi200": "Y"}
        if page < pages:
            time.sleep(max(delay, 0))
    return list(rows.values())


def write_csv(rows: list[dict[str, str]], path: Path, as_of: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["position", "ticker", "company", "sector", "in_kospi200", "source_as_of", "source_url"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for position, row in enumerate(rows, start=1):
            writer.writerow({**row, "position": position, "source_as_of": as_of, "source_url": BASE_URL.format("1")})


def write_valuation(rows: list[dict[str, str]], path: Path, as_of: str) -> None:
    payload = {
        "as_of": as_of,
        "status": "LIVE_SETUP",
        "source": "Naver public KOSPI200 constituent snapshot; first financial refresh pending",
        "items": [
            {
                "ticker": row["ticker"], "company": row["company"], "sector": row["sector"],
                "in_kospi200": "Y", "close": None, "trailing_per": None, "trailing_eps": None,
                "forward_per": None, "forward_eps": None, "pbr": None, "pbr_band_low": None,
                "pbr_band_high": None, "roe_current": None, "roe_estimate": None,
                "score": None, "rank": None, "quality": "PENDING_INITIAL_PULL",
                "source_url": "https://finance.naver.com/item/main.naver?code=" + row["ticker"],
            }
            for row in rows
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    options = parse_args()
    as_of = datetime.now(KST).date().isoformat()
    rows = load_rows(options.pages, options.delay)
    output = Path(options.output).resolve()
    write_csv(rows, output, as_of)
    if options.valuation_output:
        write_valuation(rows, Path(options.valuation_output).resolve(), as_of)
    print(f"Wrote {len(rows)} unique public KOSPI200 constituents to {output}")
    if len(rows) != 200:
        print(f"WARNING: public page returned {len(rows)} constituents; keep the 200-slot workbook and verify the remaining slot with KRX before live scoring.")


if __name__ == "__main__":
    main()
