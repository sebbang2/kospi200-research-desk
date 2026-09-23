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


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def build_peer_stats(rows: list[dict[str, str]]) -> dict[str, dict[str, float]]:
    """산업별 PER·PBR·ROE 중앙값을 계산한다."""
    from statistics import median

    fields = {"per": "forward_per_consensus", "pbr": "current_pbr", "roe": "roe_estimate"}
    global_values: dict[str, list[float]] = {key: [] for key in fields}
    grouped: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        sector = str(row.get("sector") or "기타").strip() or "기타"
        grouped.setdefault(sector, {key: [] for key in fields})
        for key, field in fields.items():
            value = optional_float(row.get(field))
            if value is not None and value > 0:
                grouped[sector][key].append(value)
                global_values[key].append(value)

    fallback = {
        key: median(values) if values else 1.0
        for key, values in global_values.items()
    }
    stats: dict[str, dict[str, float]] = {}
    for sector, values_by_field in grouped.items():
        stats[sector] = {
            key: median(values) if len(values) >= 3 else fallback[key]
            for key, values in values_by_field.items()
        }
    return stats


def valuation_score(row: dict[str, str], peer_stats: dict[str, dict[str, float]]) -> float | None:
    """산업 상대가치와 PBR-ROE 품질을 결합한 0~100점 점수."""
    f = optional_float(row.get("forward_per_consensus"))
    t = optional_float(row.get("trailing_per"))
    p = optional_float(row.get("current_pbr"))
    current_roe = optional_float(row.get("roe_current"))
    expected_roe = optional_float(row.get("roe_estimate"))
    r = expected_roe if expected_roe is not None else current_roe
    per = f if f is not None and f > 0 else t
    if per is None or p is None or p <= 0 or r is None or r <= 0:
        return None

    sector = str(row.get("sector") or "기타").strip() or "기타"
    peers = peer_stats.get(sector, {})
    industry_per = peers.get("per", per)
    industry_pbr = peers.get("pbr", p)
    industry_roe = max(peers.get("roe", r), 0.01)

    # 산업 평균 대비 PER 할인: 산업 중앙값보다 50% 낮으면 최고점.
    industry_per_score = clamp((industry_per - per) / (industry_per * 0.5))
    # TTM PER 대비 Forward PER이 10% 이상 낮아질 때부터 가점을 시작한다.
    drop = ((t - f) / t) if t and t > 0 and f and f > 0 else 0.0
    forward_improvement = clamp((drop - 0.10) / 0.20)
    # PBR은 산업 대비 할인만 반영하고, ROE 품질과 곱해 가치함정을 억제한다.
    pbr_discount = clamp((industry_pbr - p) / (industry_pbr * 0.5))
    roe_level = clamp(r / industry_roe)
    roe_direction = clamp(0.5 + ((expected_roe - current_roe) / 0.10)) if expected_roe is not None and current_roe is not None else 0.5
    roe_quality = 0.7 * roe_level + 0.3 * roe_direction
    pbr_roe_score = pbr_discount * roe_quality
    return round((0.40 * industry_per_score + 0.20 * forward_improvement + 0.40 * pbr_roe_score) * 100.0, 2)

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
        source_rows = list(csv.DictReader(handle))
    peer_stats = build_peer_stats(source_rows)
    for row in source_rows:
        ticker = row.get("ticker", "")
        prior = old_by_ticker.get(ticker, {})
        items.append({
            "ticker": ticker,
            "company": row.get("company", ""),
            "sector": row.get("sector", ""),
            "business_overview": row.get("business_overview", "") or None,
            "business_keywords": keyword_list(row.get("business_keywords")),
            "close": optional_float(row.get("close")),
            "market_cap": optional_float(row.get("market_cap")),
            "trailing_per": optional_float(row.get("trailing_per")),
            "trailing_eps": optional_float(row.get("trailing_eps")),
            "forward_per": optional_float(row.get("forward_per_consensus")),
            "forward_eps": optional_float(row.get("forward_eps_consensus")),
            "pbr": optional_float(row.get("current_pbr")),
            "pbr_band_low": optional_float(row.get("pbr_band_low")),
            "pbr_band_high": optional_float(row.get("pbr_band_high")),
            "pbr_5y_min": optional_float(row.get("pbr_5y_min")),
            "roe_current": optional_float(row.get("roe_current")),
            "roe_estimate": optional_float(row.get("roe_estimate")),
            "score": valuation_score(row, peer_stats),
            "rank": prior.get("rank"),
            "market_cap_rank": prior.get("market_cap_rank"),
            "quality": row.get("quality_flag") or "PARTIAL",
            "source_url": row.get("source_url", ""),
        })
    items.sort(key=lambda item: (item.get("score") is None, -(item.get("score") or 0)))
    for index, item in enumerate(items, start=1):
        if item.get("score") is not None:
            item["rank"] = index
    market_cap_items = sorted(items, key=lambda item: (item.get("market_cap") is None, -(item.get("market_cap") or 0)))
    for index, item in enumerate(market_cap_items, start=1):
        if item.get("market_cap") is not None:
            item["market_cap_rank"] = index
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
