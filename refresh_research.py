"""Run the public valuation and news refreshes used by the web dashboard."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


KST = timezone(timedelta(hours=9))


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh public valuation data and keyword news for the dashboard")
    parser.add_argument("--mode", choices=("all", "valuation", "news"), default="all")
    parser.add_argument("--universe", required=True, help="CSV with ticker,company,sector columns")
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--delay", type=float, default=1.2)
    parser.add_argument("--history-count", type=int, default=4000)
    return parser.parse_args()


def run(command: list[str]) -> None:
    print("Running:", " ".join(command))
    subprocess.run(command, check=True)


def optional_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def keyword_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split(";") if part.strip()][:3]


def update_valuation_json(csv_path: Path, output_path: Path, universe_path: Path) -> None:
    existing = {"items": []}
    if output_path.exists():
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    old_by_ticker = {str(item.get("ticker")): item for item in existing.get("items", [])}
    items: list[dict[str, object]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            ticker = row.get("ticker", "")
            prior = old_by_ticker.get(ticker, {})
            items.append({
                "ticker": ticker,
                "company": row.get("company", ""),
                "sector": row.get("sector", ""),
                "business_overview": row.get("business_overview", "") or None,
                "business_keywords": keyword_list(row.get("business_keywords")),
                "close": optional_float(row.get("close")),
                "trailing_per": optional_float(row.get("trailing_per")),
                "trailing_eps": optional_float(row.get("trailing_eps")),
                "forward_per": optional_float(row.get("forward_per_consensus")),
                "forward_eps": optional_float(row.get("forward_eps_consensus")),
                "pbr": optional_float(row.get("current_pbr")),
                "roe_current": optional_float(row.get("roe_current")),
                "roe_estimate": optional_float(row.get("roe_estimate")),
                "score": prior.get("score"),
                "rank": prior.get("rank"),
                "quality": row.get("quality_flag") or "PARTIAL",
                "source_url": row.get("source_url", ""),
            })
    items.sort(key=lambda item: (item.get("score") is None, -(item.get("score") or 0)))
    for index, item in enumerate(items, start=1):
        if item.get("score") is not None:
            item["rank"] = index
    payload = {
        "as_of": datetime.now(KST).date().isoformat(),
        "status": "LIVE",
        "source": "refresh_public_data.py normalized public financials",
        "universe": str(universe_path),
        "items": items,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    options = args()
    output_dir = Path(options.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent
    financial_csv = output_dir / "public_financials.csv"
    if options.mode in ("all", "valuation"):
        run([sys.executable, str(root / "refresh_public_data.py"), "--input", options.universe, "--output", str(financial_csv), "--price-history-output", str(output_dir / "price_history.csv"), "--history-count", str(options.history_count), "--delay", str(options.delay)])
        update_valuation_json(financial_csv, output_dir / "valuation_data.json", Path(options.universe).resolve())
    if options.mode in ("all", "news"):
        run([sys.executable, str(root / "refresh_news.py"), "--input", options.universe, "--output", str(output_dir / "news_data.json"), "--delay", str(options.delay)])
    print(f"Dashboard {options.mode} refresh completed:", output_dir)


if __name__ == "__main__":
    main()
