#!/usr/bin/env python3
"""
Local reproducible probe: compare request stability/latency between
Gamma API (used today for WIN/LOSS inference) and Data API (positions endpoint).

This does NOT require any private keys. If you provide --wallet (public address),
the script will also validate that Data API returns the fields we need:
conditionId/outcomeIndex/curPrice/redeemable (when there are positions).
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests


@dataclass
class ProbeStats:
    ok: int = 0
    http_non_200: int = 0
    json_parse_errors: int = 0
    exceptions: Dict[str, int] = field(default_factory=dict)
    latencies_s: List[float] = field(default_factory=list)
    sample_non_200: List[Tuple[int, str]] = field(default_factory=list)  # (status, body_prefix)

    def add_exc(self, e: Exception):
        k = type(e).__name__
        self.exceptions[k] = self.exceptions.get(k, 0) + 1


def _pct(v: float) -> str:
    return f"{v * 100:.2f}%"


def _pctl(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    xs2 = sorted(xs)
    # simple nearest-rank percentile
    k = int(round((len(xs2) - 1) * p))
    return xs2[max(0, min(k, len(xs2) - 1))]


def probe_url(session: requests.Session, url: str, *, timeout_s: float) -> Tuple[Optional[int], Optional[Any], float]:
    t0 = time.time()
    resp = session.get(url, timeout=timeout_s)
    dt = time.time() - t0
    data = None
    if resp.status_code == 200:
        data = resp.json()
    return resp.status_code, data, dt


def run_probe(name: str, urls: List[str], *, n: int, timeout_s: float, sleep_s: float) -> ProbeStats:
    s = requests.Session()
    st = ProbeStats()
    for i in range(n):
        url = urls[i % len(urls)]
        try:
            code, data, dt = probe_url(s, url, timeout_s=timeout_s)
            st.latencies_s.append(dt)
            if code != 200:
                st.http_non_200 += 1
                if len(st.sample_non_200) < 5:
                    try:
                        # refetch body prefix safely (already fetched above)
                        r = s.get(url, timeout=timeout_s)
                        st.sample_non_200.append((r.status_code, r.text[:200].replace("\n", "\\n")))
                    except Exception:
                        st.sample_non_200.append((int(code or -1), "<body unavailable>"))
                continue
            st.ok += 1
            if data is None:
                st.json_parse_errors += 1
        except json.JSONDecodeError:
            st.json_parse_errors += 1
        except Exception as e:
            st.add_exc(e)
        if sleep_s > 0:
            time.sleep(sleep_s)
    return st


def summarize(name: str, st: ProbeStats, *, n: int) -> str:
    ok_rate = st.ok / n if n else 0.0
    exc_cnt = sum(st.exceptions.values())
    lat = st.latencies_s
    mean = statistics.mean(lat) if lat else None
    p50 = _pctl(lat, 0.50)
    p95 = _pctl(lat, 0.95)
    p99 = _pctl(lat, 0.99)
    parts = [
        f"[{name}]",
        f"requests={n}",
        f"ok={st.ok} ({_pct(ok_rate)})",
        f"http_non_200={st.http_non_200}",
        f"json_parse_errors={st.json_parse_errors}",
        f"exceptions={exc_cnt} ({st.exceptions})" if exc_cnt else "exceptions=0",
        f"latency_mean={mean:.3f}s" if mean is not None else "latency_mean=N/A",
        f"p50={p50:.3f}s" if p50 is not None else "p50=N/A",
        f"p95={p95:.3f}s" if p95 is not None else "p95=N/A",
        f"p99={p99:.3f}s" if p99 is not None else "p99=N/A",
    ]
    if st.sample_non_200:
        parts.append(f"sample_non_200={st.sample_non_200}")
    return " | ".join(parts)


def validate_positions_schema(positions: Any) -> Dict[str, Any]:
    """Return minimal schema validation summary for data-api positions payload."""
    out: Dict[str, Any] = {"type": type(positions).__name__}
    if not isinstance(positions, list):
        out["ok"] = False
        return out
    out["ok"] = True
    out["count"] = len(positions)
    if not positions:
        return out
    x = positions[0]
    if not isinstance(x, dict):
        out["ok"] = False
        return out
    need = ["conditionId", "outcomeIndex", "curPrice", "redeemable", "assetId"]
    out["has_fields"] = {k: (k in x) for k in need}
    mk = x.get("market") if isinstance(x.get("market"), dict) else {}
    out["market_has"] = {k: (k in mk) for k in ["negRisk", "question"]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200, help="requests per probe")
    ap.add_argument("--timeout", type=float, default=10.0, help="per request timeout (seconds)")
    ap.add_argument("--sleep", type=float, default=0.0, help="sleep between requests (seconds)")
    ap.add_argument("--wallet", type=str, default="", help="public address for data-api positions probe (optional)")
    args = ap.parse_args()

    # 1) Gamma: use common endpoints we depend on.
    # - events?... (used for weather event -> markets list)
    # - markets?slug=... (used in other projects and useful for direct binary resolution checks)
    gamma_urls = [
        "https://gamma-api.polymarket.com/events?limit=1&active=true",
        "https://gamma-api.polymarket.com/events?limit=50&active=true&closed=false&q=Temperature",
    ]

    # 2) Data API: positions endpoint (the one we intend to use for outcomeIndex/curPrice/redeemable).
    wallet = (args.wallet or "").strip()
    if not wallet:
        wallet = "0x0000000000000000000000000000000000000000"
    data_urls = [
        f"https://data-api.polymarket.com/positions?user={wallet}&resolved=true",
    ]

    gamma = run_probe("gamma-api", gamma_urls, n=args.n, timeout_s=args.timeout, sleep_s=args.sleep)
    data = run_probe("data-api-positions", data_urls, n=args.n, timeout_s=args.timeout, sleep_s=args.sleep)

    print(summarize("gamma-api", gamma, n=args.n))
    print(summarize("data-api-positions", data, n=args.n))

    # Schema validation for data-api (if we got at least one OK response).
    try:
        r = requests.get(data_urls[0], timeout=args.timeout)
        if r.status_code == 200:
            v = validate_positions_schema(r.json())
            print("[data-api-positions schema]", json.dumps(v, ensure_ascii=True))
        else:
            print("[data-api-positions schema] skipped (http status != 200)")
    except Exception as e:
        print("[data-api-positions schema] ERROR", type(e).__name__, str(e)[:200])

    # If we have real positions, do a correctness cross-check:
    # For each resolved position, compare whether Gamma marks the associated market as closed/resolved+binary.
    if args.wallet.strip():
        try:
            pos = requests.get(data_urls[0], timeout=args.timeout).json()
        except Exception as e:
            print("[cross-check] ERROR fetching positions:", type(e).__name__, str(e)[:200])
            return

        if not isinstance(pos, list) or not pos:
            print("[cross-check] No resolved positions returned for wallet (nothing to compare).")
            return

        # Extract market slugs (these are per-threshold YES/NO markets, not the parent event slug).
        market_slugs = []
        winners = 0
        for p in pos[:100]:
            mk = p.get("market") if isinstance(p, dict) else None
            if isinstance(mk, dict):
                s = mk.get("slug") or mk.get("marketSlug") or mk.get("ticker")
                if s:
                    market_slugs.append(str(s))
            try:
                cur_price = float(p.get("curPrice", 0) or 0)
                redeemable = bool(p.get("redeemable", False))
                if redeemable or cur_price >= 0.99:
                    winners += 1
            except Exception:
                pass

        market_slugs = list(dict.fromkeys(market_slugs))  # preserve order, unique
        if not market_slugs:
            print("[cross-check] Positions payload had no market.slug/marketSlug/ticker fields (cannot query Gamma markets).")
            return

        # Probe Gamma /markets?slug= for these markets and see if they are binary.
        s = requests.Session()
        checked = 0
        found = 0
        binary = 0
        errors = 0
        for ms in market_slugs[:50]:
            checked += 1
            url = f"https://gamma-api.polymarket.com/markets?slug={ms}"
            try:
                rr = s.get(url, timeout=args.timeout)
                if rr.status_code != 200:
                    continue
                j = rr.json()
                if not isinstance(j, list) or not j:
                    continue
                found += 1
                m = j[0]
                op = m.get("outcomePrices")
                if isinstance(op, str):
                    try:
                        op = json.loads(op)
                    except Exception:
                        op = None
                if isinstance(op, list) and len(op) >= 2:
                    try:
                        yes = float(op[0])
                        no = float(op[1])
                        if (yes >= 0.999 and no <= 0.001) or (no >= 0.999 and yes <= 0.001):
                            binary += 1
                    except Exception:
                        pass
            except Exception:
                errors += 1

        print(
            "[cross-check] wallet_positions=%d | winners_like=%d | market_slugs=%d | gamma_checked=%d | gamma_found=%d | gamma_binary=%d | gamma_errors=%d"
            % (len(pos), winners, len(market_slugs), checked, found, binary, errors)
        )


if __name__ == "__main__":
    main()
