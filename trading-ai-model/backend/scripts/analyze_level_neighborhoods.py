"""
scripts/analyze_level_neighborhoods.py

For every level in price_levels, find the 30 nearest neighbor levels
above and 30 below (same symbol), then compute a neighborhood
hold-rate profile, plus touch_count vs EITHER-neighbor correlation.

Usage:
    python scripts/analyze_level_neighborhoods.py --symbols MES,EURUSD,BTCUSD
    python scripts/analyze_level_neighborhoods.py --symbols ALL --min-touches 5
    python scripts/analyze_level_neighborhoods.py --symbols MES --export-csv
"""
from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("level_neighborhoods")


@dataclass
class NeighborhoodProfile:
    symbol: str
    level_price: float
    role: str
    hold_rate: float
    touch_count: int
    strength_score: float
    neighbors_above_count: int
    neighbors_below_count: int
    neighbor_avg_hold_rate: float
    neighbor_avg_hold_rate_above: float
    neighbor_avg_hold_rate_below: float
    neighbor_high_hold_pct: float
    nearest_neighbor_distance_pct: float
    avg_neighbor_distance_pct: float
    role_match_pct: float
    either_neighbors_count: int
    either_neighbors_avg_hold_rate: float


def _get_conn():
    dsn = os.environ["DATABASE_URL"]
    return psycopg2.connect(dsn)


def load_levels(symbol: str, min_touches: int) -> list[dict]:
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT level_price, role, hold_rate, touch_count, strength_score
        FROM price_levels
        WHERE symbol = %s AND touch_count >= %s
        ORDER BY level_price ASC
        """,
        (symbol, min_touches),
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def compute_neighborhood(levels: list[dict], idx: int, symbol: str, k: int = 30) -> NeighborhoodProfile:
    target = levels[idx]
    target_price = target["level_price"]

    below = levels[max(0, idx - k):idx]
    above = levels[idx + 1: idx + 1 + k]
    neighbors = below + above

    def hold_rates(rows):
        return [float(r["hold_rate"] or 0) for r in rows]

    all_hold = hold_rates(neighbors)
    above_hold = hold_rates(above)
    below_hold = hold_rates(below)
    all_dist = [abs(float(r["level_price"]) - target_price) / target_price * 100 for r in neighbors]

    high_hold = [h for h in all_hold if h >= 0.55]
    same_role = [r for r in neighbors if r["role"] == target["role"]]
    either_neighbors = [r for r in neighbors if r["role"] == "MIXED"]

    return NeighborhoodProfile(
        symbol=symbol,
        level_price=target_price,
        role=target["role"],
        hold_rate=float(target["hold_rate"] or 0),
        touch_count=int(target["touch_count"] or 0),
        strength_score=float(target["strength_score"] or 0),
        neighbors_above_count=len(above),
        neighbors_below_count=len(below),
        neighbor_avg_hold_rate=round(sum(all_hold) / len(all_hold), 4) if all_hold else 0.0,
        neighbor_avg_hold_rate_above=round(sum(above_hold) / len(above_hold), 4) if above_hold else 0.0,
        neighbor_avg_hold_rate_below=round(sum(below_hold) / len(below_hold), 4) if below_hold else 0.0,
        neighbor_high_hold_pct=round(len(high_hold) / len(all_hold) * 100, 2) if all_hold else 0.0,
        nearest_neighbor_distance_pct=round(min(all_dist), 4) if all_dist else 0.0,
        avg_neighbor_distance_pct=round(sum(all_dist) / len(all_dist), 4) if all_dist else 0.0,
        role_match_pct=round(len(same_role) / len(neighbors) * 100, 2) if neighbors else 0.0,
        either_neighbors_count=len(either_neighbors),
        either_neighbors_avg_hold_rate=round(
            sum(hold_rates(either_neighbors)) / len(either_neighbors), 4
        ) if either_neighbors else 0.0,
    )


def analyze_symbol(symbol: str, min_touches: int, k: int = 30) -> list[NeighborhoodProfile]:
    levels = load_levels(symbol, min_touches)
    if len(levels) < 2:
        logger.warning("%s: only %d levels with touch_count>=%d — skipping", symbol, len(levels), min_touches)
        return []

    logger.info("%s: analyzing %d levels (k=%d neighbors each side)", symbol, len(levels), k)
    profiles = [compute_neighborhood(levels, i, symbol, k) for i in range(len(levels))]
    return profiles


def print_touch_either_correlation(profiles: list) -> None:
    """Correlation between touch_count and either_neighbors_count / either_neighbors_avg_hold_rate."""
    import statistics

    if len(profiles) < 2:
        return

    touches = [p.touch_count for p in profiles]
    either_count = [p.either_neighbors_count for p in profiles]
    either_hold = [p.either_neighbors_avg_hold_rate for p in profiles]

    try:
        corr_count = statistics.correlation(touches, either_count)
        print(f"Correlation(touch_count, either_neighbors_count)      = {corr_count:.4f}")
    except Exception:
        pass

    try:
        corr_hold = statistics.correlation(touches, either_hold)
        print(f"Correlation(touch_count, either_neighbors_avg_hold)   = {corr_hold:.4f}")
    except Exception:
        pass

    sorted_by_touch = sorted(profiles, key=lambda p: p.touch_count)
    n = len(sorted_by_touch)
    low = sorted_by_touch[: n // 3]
    mid = sorted_by_touch[n // 3: 2 * n // 3]
    high = sorted_by_touch[2 * n // 3:]

    def avg(lst, attr):
        vals = [getattr(p, attr) for p in lst]
        return sum(vals) / len(vals) if vals else 0.0

    print("\nTouch tercile breakdown:")
    print(
        f"  Low  touches (avg={avg(low, 'touch_count'):.1f}):  "
        f"avg either_neighbors={avg(low, 'either_neighbors_count'):.2f}  "
        f"avg either_hold={avg(low, 'either_neighbors_avg_hold_rate'):.3f}"
    )
    print(
        f"  Mid  touches (avg={avg(mid, 'touch_count'):.1f}):  "
        f"avg either_neighbors={avg(mid, 'either_neighbors_count'):.2f}  "
        f"avg either_hold={avg(mid, 'either_neighbors_avg_hold_rate'):.3f}"
    )
    print(
        f"  High touches (avg={avg(high, 'touch_count'):.1f}): "
        f"avg either_neighbors={avg(high, 'either_neighbors_count'):.2f}  "
        f"avg either_hold={avg(high, 'either_neighbors_avg_hold_rate'):.3f}"
    )


def print_summary(profiles: list[NeighborhoodProfile]) -> None:
    if not profiles:
        return

    top = sorted(profiles, key=lambda p: -p.strength_score)[:20]

    print(
        f"\n{'price':>12} {'role':>10} {'hold':>6} {'touches':>7} "
        f"{'nbr_hold':>9} {'high%':>6} {'role%':>6} {'near_d%':>8} {'either_hold':>11}"
    )
    print("-" * 100)
    for p in top:
        print(
            f"{p.level_price:>12.5f} {p.role:>10} {p.hold_rate:>6.2f} {p.touch_count:>7d} "
            f"{p.neighbor_avg_hold_rate:>9.3f} {p.neighbor_high_hold_pct:>6.1f} "
            f"{p.role_match_pct:>6.1f} {p.nearest_neighbor_distance_pct:>8.3f} "
            f"{p.either_neighbors_avg_hold_rate:>11.3f}"
        )

    import statistics

    own = [p.hold_rate for p in profiles]
    nbr = [p.neighbor_avg_hold_rate for p in profiles]
    if len(own) > 1:
        try:
            corr = statistics.correlation(own, nbr)
            print(f"\nCorrelation(own hold_rate, neighbor avg hold_rate) = {corr:.4f}")
        except Exception:
            pass

    near_either = [p for p in profiles if p.either_neighbors_count >= 5]
    far_either = [p for p in profiles if p.either_neighbors_count < 5]
    if near_either and far_either:
        avg_hold_near = sum(p.hold_rate for p in near_either) / len(near_either)
        avg_hold_far = sum(p.hold_rate for p in far_either) / len(far_either)
        print(
            f"\nLevels near >=5 EITHER neighbors: avg own hold_rate = "
            f"{avg_hold_near:.4f} (n={len(near_either)})"
        )
        print(
            f"Levels near <5 EITHER neighbors:  avg own hold_rate = "
            f"{avg_hold_far:.4f} (n={len(far_either)})"
        )

    print_touch_either_correlation(profiles)


def export_csv(all_profiles: list[NeighborhoodProfile], path: Path) -> None:
    import csv

    if not all_profiles:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(all_profiles[0]).keys()))
        writer.writeheader()
        for p in all_profiles:
            writer.writerow(asdict(p))
    logger.info("Exported %d rows to %s", len(all_profiles), path)


def write_to_db(all_profiles: list[NeighborhoodProfile]) -> None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS level_neighborhood_stats (
            symbol TEXT NOT NULL,
            level_price NUMERIC(18,8) NOT NULL,
            role TEXT,
            hold_rate NUMERIC(8,6),
            touch_count INTEGER,
            strength_score NUMERIC(8,6),
            neighbors_above_count INTEGER,
            neighbors_below_count INTEGER,
            neighbor_avg_hold_rate NUMERIC(8,6),
            neighbor_avg_hold_rate_above NUMERIC(8,6),
            neighbor_avg_hold_rate_below NUMERIC(8,6),
            neighbor_high_hold_pct NUMERIC(8,4),
            nearest_neighbor_distance_pct NUMERIC(10,6),
            avg_neighbor_distance_pct NUMERIC(10,6),
            role_match_pct NUMERIC(8,4),
            either_neighbors_count INTEGER,
            either_neighbors_avg_hold_rate NUMERIC(8,6),
            computed_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (symbol, level_price)
        )
        """
    )
    conn.commit()

    for p in all_profiles:
        cur.execute(
            """
            INSERT INTO level_neighborhood_stats (
                symbol, level_price, role, hold_rate, touch_count, strength_score,
                neighbors_above_count, neighbors_below_count,
                neighbor_avg_hold_rate, neighbor_avg_hold_rate_above, neighbor_avg_hold_rate_below,
                neighbor_high_hold_pct, nearest_neighbor_distance_pct, avg_neighbor_distance_pct,
                role_match_pct, either_neighbors_count, either_neighbors_avg_hold_rate, computed_at
            ) VALUES (
                %(symbol)s, %(level_price)s, %(role)s, %(hold_rate)s, %(touch_count)s, %(strength_score)s,
                %(neighbors_above_count)s, %(neighbors_below_count)s,
                %(neighbor_avg_hold_rate)s, %(neighbor_avg_hold_rate_above)s, %(neighbor_avg_hold_rate_below)s,
                %(neighbor_high_hold_pct)s, %(nearest_neighbor_distance_pct)s, %(avg_neighbor_distance_pct)s,
                %(role_match_pct)s, %(either_neighbors_count)s, %(either_neighbors_avg_hold_rate)s, NOW()
            )
            ON CONFLICT (symbol, level_price) DO UPDATE SET
                role = EXCLUDED.role,
                hold_rate = EXCLUDED.hold_rate,
                touch_count = EXCLUDED.touch_count,
                strength_score = EXCLUDED.strength_score,
                neighbors_above_count = EXCLUDED.neighbors_above_count,
                neighbors_below_count = EXCLUDED.neighbors_below_count,
                neighbor_avg_hold_rate = EXCLUDED.neighbor_avg_hold_rate,
                neighbor_avg_hold_rate_above = EXCLUDED.neighbor_avg_hold_rate_above,
                neighbor_avg_hold_rate_below = EXCLUDED.neighbor_avg_hold_rate_below,
                neighbor_high_hold_pct = EXCLUDED.neighbor_high_hold_pct,
                nearest_neighbor_distance_pct = EXCLUDED.nearest_neighbor_distance_pct,
                avg_neighbor_distance_pct = EXCLUDED.avg_neighbor_distance_pct,
                role_match_pct = EXCLUDED.role_match_pct,
                either_neighbors_count = EXCLUDED.either_neighbors_count,
                either_neighbors_avg_hold_rate = EXCLUDED.either_neighbors_avg_hold_rate,
                computed_at = NOW()
            """,
            asdict(p),
        )

    conn.commit()
    cur.close()
    conn.close()
    logger.info("Wrote %d rows to level_neighborhood_stats", len(all_profiles))


def main():
    p = argparse.ArgumentParser(description="Analyze level neighborhood hold-rate patterns")
    p.add_argument("--symbols", required=True, help="Comma-separated symbols, or ALL")
    p.add_argument("--min-touches", type=int, default=5)
    p.add_argument("--k", type=int, default=30, help="Neighbors above/below to consider")
    p.add_argument("--export-csv", action="store_true")
    p.add_argument("--write-db", action="store_true", help="Persist to level_neighborhood_stats table")
    args = p.parse_args()

    if args.symbols.upper() == "ALL":
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT symbol FROM price_levels ORDER BY symbol")
        symbols = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]

    all_profiles: list[NeighborhoodProfile] = []
    for sym in symbols:
        print(f"\n{'=' * 100}")
        print(f"  {sym}")
        print("=" * 100)
        profiles = analyze_symbol(sym, args.min_touches, args.k)
        print_summary(profiles)
        all_profiles.extend(profiles)

    if args.export_csv:
        out = Path("/tmp/level_neighborhoods.csv")
        export_csv(all_profiles, out)
        print(f"\nCSV written to {out}")

    if args.write_db:
        write_to_db(all_profiles)


if __name__ == "__main__":
    main()