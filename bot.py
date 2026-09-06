#!/usr/bin/env python3
"""Vietnam shark + Wyckoff scanner ported from LuDanDaoGam_Wyckoff_fixed_v3.

Signals are evaluated on the latest D1 bar only.
No Telegram token is stored in this repository; use GitHub Actions Secrets.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

# ===== Parameters copied from the source tool =====
LOOKBACK_SUPPORT_BARS = 20
MIN_LOWER_SHADOW_RATIO = 0.50
VOLUME_MA_PERIOD = 20
VOLUME_MULTIPLIER_MIN = 1.5
VOLUME_MULTIPLIER_MAX = 3.0
MAX_STOP_LOSS_PCT = 0.07
CLOSE_RANGE_RATIO = 0.70
CMF_PERIOD = 20
MFI_PERIOD = 14
MFI_THRESHOLD = 55
VOLUME_BREAK_MULTIPLIER = 1.3

MAX_WORKERS = max(1, min(12, int(os.getenv("MAX_WORKERS", "5"))))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "15"))
MAX_BARS = int(os.getenv("MAX_BARS", "900"))
VIETNAM_TZ = timezone(timedelta(hours=7))
UA = "Mozilla/5.0 (GitHub-Actions SharkWyckoffBot/1.0)"


@dataclass
class ScanResult:
    symbol: str
    source: str | None = None
    bar_date: str | None = None
    close: float | None = None
    volume: float | None = None
    vol_ratio: float | None = None
    shark_color: str | None = None
    shark_score: int = 0
    cond_count: int = 0
    conditions: dict[str, bool] | None = None
    blocked: bool = False
    block_reason: str = ""
    spring: bool = False
    upthrust: bool = False
    wyckoff_score: int = 0
    error: str | None = None


def to_float(x: Any) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def parse_date(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Most TCBS/VNDIRECT values are ISO-like. Keep only date.
    if len(s) >= 10 and s[4] == '-' and s[7] == '-':
        return s[:10]
    for fmt in ("%d/%m/%Y", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).date().isoformat()
        except ValueError:
            pass
    return None


def get_json(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    hdr = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdr.update(headers)
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=hdr, timeout=REQUEST_TIMEOUT)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(str(last) if last else "request failed")


def clean_bars(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        d = row.get("date")
        o, h, l, c, v = (to_float(row.get(k)) for k in ("open", "high", "low", "close", "volume"))
        if not d or None in (o, h, l, c, v):
            continue
        if l <= 0 or v < 0 or h < max(o, c, l) or l > min(o, c):
            continue
        out[d] = {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}
    return [out[d] for d in sorted(out)][-MAX_BARS:]


def fetch_tcbs(symbol: str) -> list[dict[str, Any]]:
    data = get_json(
        "https://apipub.tcbs.com.vn/stock-insight/v1/stock/bars-long-term",
        params={"ticker": symbol, "type": "stock", "resolution": "D"},
    )
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    rows = []
    for x in items:
        if not isinstance(x, dict):
            continue
        rows.append({
            "date": parse_date(x.get("tradingDate") or x.get("date")),
            "open": x.get("open"), "high": x.get("high"), "low": x.get("low"),
            "close": x.get("close"), "volume": x.get("volume"),
        })
    return clean_bars(rows)


def fetch_vndirect(symbol: str) -> list[dict[str, Any]]:
    start = (datetime.now(VIETNAM_TZ).date() - timedelta(days=1200)).isoformat()
    data = get_json(
        "https://api-finfo.vndirect.com.vn/v4/stock_prices",
        params={"sort": "date", "q": f"code:{symbol}~date:gte:{start}", "size": 1000, "page": 1},
        headers={"Origin": "https://dstock.vndirect.com.vn", "Referer": "https://dstock.vndirect.com.vn/"},
    )
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    rows = []
    for x in items:
        if not isinstance(x, dict):
            continue
        # VNDIRECT stock_prices historically reports listed share prices in thousand VND.
        # Scale provider-specific fields only; this is NOT the removed Yahoo heuristic.
        def p(k: str):
            val = to_float(x.get(k))
            return val * 1000 if val is not None else None
        rows.append({
            "date": parse_date(x.get("date") or x.get("tradingDate")),
            "open": p("open"), "high": p("high"), "low": p("low"), "close": p("close"),
            "volume": x.get("nmVolume") if x.get("nmVolume") is not None else x.get("volume"),
        })
    return clean_bars(rows)


def fetch_yahoo(symbol: str) -> list[dict[str, Any]]:
    data = get_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.VN",
        params={"range": "5y", "interval": "1d", "includeAdjustedClose": "true"},
    )
    result = (((data or {}).get("chart") or {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        return []
    meta = result.get("meta") or {}
    if meta.get("currency") not in (None, "VND"):
        return []
    ts = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [None])[0]) or {}
    rows = []
    for i, t in enumerate(ts):
        try:
            dt = datetime.fromtimestamp(int(t), tz=timezone.utc).astimezone(VIETNAM_TZ).date().isoformat()
            rows.append({
                "date": dt,
                "open": quote.get("open", [None] * len(ts))[i],
                "high": quote.get("high", [None] * len(ts))[i],
                "low": quote.get("low", [None] * len(ts))[i],
                "close": quote.get("close", [None] * len(ts))[i],
                "volume": quote.get("volume", [None] * len(ts))[i],
            })
        except Exception:
            continue
    return clean_bars(rows)


def fetch_stock(symbol: str) -> tuple[list[dict[str, Any]], str]:
    errors = []
    for name, fn in (("TCBS", fetch_tcbs), ("VNDIRECT", fetch_vndirect), ("YAHOO", fetch_yahoo)):
        try:
            bars = fn(symbol)
            if len(bars) >= 61:
                return bars, name
            errors.append(f"{name}: {len(bars)} bars")
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise RuntimeError(" | ".join(errors))


def sma(values: list[float], period: int, idx: int) -> float | None:
    if idx < period - 1:
        return None
    return sum(values[idx - period + 1:idx + 1]) / period


def rsi_from_avg(gain: float, loss: float) -> float:
    if loss == 0 and gain == 0:
        return 50.0
    if loss == 0:
        return 100.0
    if gain == 0:
        return 0.0
    rs = gain / loss
    return 100.0 - 100.0 / (1.0 + rs)


def calculate_indicators(data: list[dict[str, Any]]) -> None:
    n = len(data)
    closes = [b["close"] for b in data]
    vols = [b["volume"] for b in data]

    rsi = [None] * n
    avg_gain = avg_loss = 0.0
    for i in range(1, n):
        ch = closes[i] - closes[i - 1]
        g, l = (ch if ch > 0 else 0.0), (-ch if ch < 0 else 0.0)
        if i <= 14:
            avg_gain += g; avg_loss += l
            if i == 14:
                avg_gain /= 14; avg_loss /= 14
                rsi[i] = rsi_from_avg(avg_gain, avg_loss)
        else:
            avg_gain = (avg_gain * 13 + g) / 14
            avg_loss = (avg_loss * 13 + l) / 14
            rsi[i] = rsi_from_avg(avg_gain, avg_loss)

    cmf = [None] * n
    for i in range(CMF_PERIOD - 1, n):
        mf = vol = 0.0
        for b in data[i - CMF_PERIOD + 1:i + 1]:
            rng = b["high"] - b["low"]
            mfm = 0.0 if rng == 0 else (((b["close"] - b["low"]) - (b["high"] - b["close"])) / rng)
            mf += mfm * b["volume"]
            vol += b["volume"]
        cmf[i] = mf / vol if vol > 0 else 0.0

    mfi = [None] * n
    for i in range(MFI_PERIOD, n):
        pos = neg = 0.0
        for k in range(i - MFI_PERIOD + 1, i + 1):
            typ = (data[k]["high"] + data[k]["low"] + data[k]["close"]) / 3
            ptyp = (data[k-1]["high"] + data[k-1]["low"] + data[k-1]["close"]) / 3
            flow = typ * data[k]["volume"]
            if typ > ptyp: pos += flow
            elif typ < ptyp: neg += flow
        if neg == 0 and pos == 0: mfi[i] = 50.0
        elif neg == 0: mfi[i] = 100.0
        elif pos == 0: mfi[i] = 0.0
        else: mfi[i] = 100.0 - 100.0 / (1.0 + pos / neg)

    for i, b in enumerate(data):
        b["ma20"] = sma(closes, 20, i)
        b["ma20_volume"] = sma(vols, VOLUME_MA_PERIOD, i)
        b["rsi"] = rsi[i]
        b["cmf20"] = cmf[i]
        b["mfi14"] = mfi[i]


def is_pivot_low(data: list[dict[str, Any]], idx: int, left: int = 2, right: int = 2) -> bool:
    if idx < left or idx + right >= len(data):
        return False
    v = data[idx]["low"]
    for k in range(1, left + 1):
        if data[idx-k]["low"] <= v: return False
    for k in range(1, right + 1):
        if data[idx+k]["low"] <= v: return False
    return True


def calculate_trend_filter(data: list[dict[str, Any]]) -> None:
    n = len(data)
    for i in range(n):
        double_bottom = rsi_div = break_ma20_vol = False
        pivots = [j for j in range(max(2, i - 35), i - 1) if is_pivot_low(data, j)]
        if len(pivots) >= 2:
            p1, p2 = pivots[-2], pivots[-1]
            gap = p2 - p1
            if 5 <= gap <= 30:
                low1, low2 = data[p1]["low"], data[p2]["low"]
                tolerance = abs(low2 - low1) / max(low1, 1)
                between = data[p1 + 1:p2]
                neckline = max((b["high"] for b in between), default=-math.inf)
                double_bottom = tolerance <= 0.025 and data[i]["close"] > neckline
                r1, r2 = data[p1].get("rsi"), data[p2].get("rsi")
                rsi_div = r1 is not None and r2 is not None and low2 < low1 and r2 > r1
        b = data[i]
        if b.get("ma20") is not None and b.get("ma20_volume") not in (None, 0):
            prev = data[i-1] if i > 0 else None
            crossed = (prev is not None and prev.get("ma20") is not None and prev["close"] <= prev["ma20"] and b["close"] > b["ma20"]) or (prev is None and b["close"] > b["ma20"])
            if crossed and b["volume"] > b["ma20_volume"] * VOLUME_BREAK_MULTIPLIER:
                break_ma20_vol = True
        b["doubleBottom"] = double_bottom
        b["rsiDivergence"] = rsi_div
        b["breakMA20WithVolume"] = break_ma20_vol
        b["trendConfirmed"] = double_bottom or rsi_div or break_ma20_vol


def evaluate_main(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calculate_indicators(data)
    calculate_trend_filter(data)
    processed = []
    for i, c0 in enumerate(data):
        c = dict(c0)
        support = min((b["low"] for b in data[i-LOOKBACK_SUPPORT_BARS:i]), default=None) if i >= LOOKBACK_SUPPORT_BARS else None
        a1 = support is not None and c["low"] < support
        a2 = support is not None and c["close"] > support
        rng = c["high"] - c["low"]
        body = abs(c["close"] - c["open"])
        lower_wick = max(0.0, min(c["open"], c["close"]) - c["low"])
        b1 = ((lower_wick / body) >= MIN_LOWER_SHADOW_RATIO) if (rng > 0 and body > 0) else (rng > 0 and lower_wick > 0)
        b2 = rng > 0 and ((c["close"] - c["low"]) / rng) >= CLOSE_RANGE_RATIO
        vma = c.get("ma20_volume") or 0.0
        ratio = c["volume"] / vma if vma > 0 else 0.0
        b3 = vma > 0 and VOLUME_MULTIPLIER_MIN <= ratio <= VOLUME_MULTIPLIER_MAX
        b4 = c.get("cmf20") is not None and c["cmf20"] > 0
        b5 = c.get("mfi14") is not None and c["mfi14"] > MFI_THRESHOLD
        stop = c["low"] * (1 - MAX_STOP_LOSS_PCT * 0.5)
        risk = (c["close"] - stop) / c["close"] if c["close"] > 0 else math.inf
        b6 = 0 <= risk <= MAX_STOP_LOSS_PCT
        cmf_ok = c.get("cmf20") is not None and c["cmf20"] > 0
        trend_ok = c.get("trendConfirmed") is True
        blocked = not cmf_ok or not trend_ok
        reason = "Bẫy hồi (CMF≤0)" if not cmf_ok else ("Chưa có trend (Filter)" if not trend_ok else "")
        conds = {"A1": a1, "A2": a2, "B1": b1, "B2": b2, "B3": b3, "B4": b4, "B5": b5, "B6": b6}
        raw = (25 if a1 else 0) + (25 if a2 else 0) + (10 if b1 else 0) + (10 if b2 else 0) + (10 if b3 else 0) + (10 if b4 else 0) + (5 if b5 else 0) + (5 if b6 else 0)
        c.update({
            "support_level": support, "A1": a1, "A2": a2, "B1": b1, "B2": b2, "B3": b3, "B4": b4, "B5": b5, "B6": b6,
            "rawScore": raw if not blocked else 0, "totalScore": raw if not blocked else 0,
            "condCount": sum(conds.values()), "blocked": blocked, "blockReason": reason, "conditions": conds,
        })
        processed.append(c)
    return processed


def classify_shark(bar: dict[str, Any]) -> str | None:
    if not (bar.get("A1") and bar.get("A2")) or bar.get("blocked"):
        return None
    count = int(bar.get("condCount") or 0)
    if count >= 7: return "PURPLE"
    if count >= 5: return "YELLOW"
    return "GREEN"


def calculate_poc(bars: list[dict[str, Any]]) -> float | None:
    if not bars or not any(b["volume"] > 0 for b in bars):
        return None
    min_l = min(b["low"] for b in bars); max_h = max(b["high"] for b in bars)
    size = (max_h - min_l) / 20
    if size == 0: return min_l
    bins = [0.0] * 20
    for b in bars:
        s = min(19, max(0, math.floor((b["low"] - min_l) / size)))
        e = min(19, math.floor((b["high"] - min_l) / size))
        num = e - s + 1
        if num <= 0: continue
        per = b["volume"] / num
        for j in range(s, e + 1): bins[j] += per
    idx = max(range(20), key=lambda i: bins[i])
    return min_l + (idx + 0.5) * size


def calculate_weis_wave(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(bars) < 2: return []
    true_ranges: list[float] = []
    waves: list[dict[str, Any]] = []
    direction = 0
    pivot = bars[0]["close"]
    wave_start = bars[0]["close"]
    wave_vol = bars[0]["volume"]
    extreme = bars[0]["close"]
    for i in range(1, len(bars)):
        b, prev = bars[i], bars[i-1]
        true_ranges.append(max(b["high"] - b["low"], abs(b["high"] - prev["close"]), abs(b["low"] - prev["close"])))
        if len(true_ranges) > 14: true_ranges.pop(0)
        atr = sum(true_ranges) / len(true_ranges)
        reversal = max(atr * 0.75, b["close"] * 0.01, 1.0)
        wave_vol += b["volume"]
        if direction == 0:
            if b["close"] >= pivot + reversal:
                direction = 1; extreme = b["close"]
            elif b["close"] <= pivot - reversal:
                direction = -1; extreme = b["close"]
            else:
                continue
        elif direction == 1:
            if b["close"] > extreme: extreme = b["close"]
            if extreme - b["close"] >= reversal:
                waves.append({"dir": 1, "vol": wave_vol - b["volume"], "startP": wave_start, "endP": extreme, "deltaP": abs(extreme-wave_start), "complete": True})
                direction = -1; wave_start = extreme; extreme = b["close"]; wave_vol = b["volume"]
        else:
            if b["close"] < extreme: extreme = b["close"]
            if b["close"] - extreme >= reversal:
                waves.append({"dir": -1, "vol": wave_vol - b["volume"], "startP": wave_start, "endP": extreme, "deltaP": abs(extreme-wave_start), "complete": True})
                direction = 1; wave_start = extreme; extreme = b["close"]; wave_vol = b["volume"]
    if direction != 0:
        waves.append({"dir": direction, "vol": wave_vol, "startP": wave_start, "endP": extreme, "deltaP": abs(extreme-wave_start), "complete": False})
    return waves


def bullish_pivot_divergence_at(processed: list[dict[str, Any]], idx: int) -> bool:
    pivots = []
    for j in range(max(2, idx - 60), idx - 1):
        v = processed[j]["low"]
        if processed[j-1]["low"] > v and processed[j-2]["low"] > v and processed[j+1]["low"] > v and processed[j+2]["low"] > v:
            pivots.append(j)
    if len(pivots) < 2: return False
    p1, p2 = pivots[-2], pivots[-1]
    r1, r2 = processed[p1].get("rsi"), processed[p2].get("rsi")
    return r1 is not None and r2 is not None and processed[p2]["low"] < processed[p1]["low"] and r2 > r1


def evaluate_wyckoff_latest(processed: list[dict[str, Any]]) -> dict[str, Any]:
    i = len(processed) - 1
    out = {"isSpring": False, "isUT": False, "totalScore": 0, "W1": False, "W2": False, "W3": False, "W4": False, "W5": False, "W6": False}
    if i < 60: return out
    d = processed[i]
    prior60 = processed[i-60:i]
    profile60 = processed[i-59:i+1]
    s_min = min(b["low"] for b in prior60)
    resistance = max(b["high"] for b in prior60)
    poc = calculate_poc(profile60)
    waves = calculate_weis_wave(processed[:i+1])
    w1 = poc is not None and poc > 0 and abs(d["close"] - poc) / poc <= 0.05
    w2 = d["low"] < s_min and d["close"] > s_min
    w3 = w2 and (d.get("ma20_volume") or 0) > 0 and d["volume"] <= d["ma20_volume"]
    w4 = w2 and bullish_pivot_divergence_at(processed, i)
    spring = w2 and w3
    completed = [w for w in waves if w.get("complete")]
    ups = [w for w in completed if w["dir"] == 1]
    downs = [w for w in completed if w["dir"] == -1]
    last_up = ups[-1] if ups else None; prev_up = ups[-2] if len(ups) >= 2 else None; last_down = downs[-1] if downs else None
    w5 = bool(last_up and last_down and len(completed) >= 2 and completed[-1] is last_down and completed[-2] is last_up and last_up["vol"] > last_down["vol"] and last_up["deltaP"] > last_down["deltaP"])
    rejected = d["high"] > resistance and d["close"] < resistance
    effort_failure = bool(last_up and prev_up and last_up["vol"] > 1.5 * prev_up["vol"] and last_up["deltaP"] < prev_up["deltaP"])
    high_volume_reject = (d.get("ma20_volume") or 0) > 0 and d["volume"] >= 1.5 * d["ma20_volume"]
    ut = rejected and (effort_failure or high_volume_reject)
    w6 = not ut
    score = (20 if w1 else 0) + (25 if w2 else 0) + (20 if w3 else 0) + (10 if w4 else 0) + (25 if w5 else 0) - (30 if ut else 0)
    out.update({"isSpring": spring, "isUT": ut, "totalScore": max(0, min(100, score)), "W1": w1, "W2": w2, "W3": w3, "W4": w4, "W5": w5, "W6": w6, "poc": poc, "s_min": s_min, "resistance": resistance})
    return out


def scan_symbol(symbol: str) -> ScanResult:
    try:
        bars, source = fetch_stock(symbol)
        processed = evaluate_main([dict(b) for b in bars])
        last = processed[-1]
        wy = evaluate_wyckoff_latest(processed)
        vma = last.get("ma20_volume") or 0
        return ScanResult(
            symbol=symbol, source=source, bar_date=last["date"], close=last["close"], volume=last["volume"],
            vol_ratio=(last["volume"] / vma if vma > 0 else None), shark_color=classify_shark(last),
            shark_score=int(last.get("totalScore") or 0), cond_count=int(last.get("condCount") or 0),
            conditions=last.get("conditions"), blocked=bool(last.get("blocked")), block_reason=last.get("blockReason", ""),
            spring=bool(wy["isSpring"]), upthrust=bool(wy["isUT"]), wyckoff_score=int(wy["totalScore"]),
        )
    except Exception as e:
        return ScanResult(symbol=symbol, error=str(e)[:300])


def read_symbols(path: str = "symbols.txt") -> list[str]:
    txt = Path(path).read_text(encoding="utf-8")
    out = []
    for token in txt.replace(",", " ").split():
        s = token.strip().upper()
        if s and s not in out: out.append(s)
    return out


def fmt_price(x: float | None) -> str:
    if x is None: return "--"
    return f"{x:,.0f}".replace(",", ".")


def fmt_vol(x: float | None) -> str:
    if x is None: return "--"
    if x >= 1e9: return f"{x/1e9:.1f}B"
    if x >= 1e6: return f"{x/1e6:.1f}M"
    if x >= 1e3: return f"{x/1e3:.0f}K"
    return f"{x:.0f}"


def signal_line(r: ScanResult, label: str) -> str:
    vr = f"{r.vol_ratio:.2f}x" if r.vol_ratio is not None else "--"
    return f"{label} {r.symbol} | {r.bar_date} | {fmt_price(r.close)} | KL {fmt_vol(r.volume)} | Vol/MA20 {vr} | Main {r.shark_score}/100 ({r.cond_count}/8) | W {r.wyckoff_score}/100"


def build_report(results: list[ScanResult]) -> str:
    ok = [r for r in results if not r.error]
    fail = [r for r in results if r.error]
    purple = sorted((r for r in ok if r.shark_color == "PURPLE"), key=lambda r: (-r.shark_score, r.symbol))
    yellow = sorted((r for r in ok if r.shark_color == "YELLOW"), key=lambda r: (-r.shark_score, r.symbol))
    green = sorted((r for r in ok if r.shark_color == "GREEN"), key=lambda r: (-r.shark_score, r.symbol))
    spring = sorted((r for r in ok if r.spring), key=lambda r: (-r.wyckoff_score, r.symbol))
    ut = sorted((r for r in ok if r.upthrust), key=lambda r: (r.wyckoff_score, r.symbol))
    now = datetime.now(VIETNAM_TZ).strftime("%d/%m/%Y %H:%M")
    lines = [
        "🦈 BOT QUÉT CÁ MẬP + WYCKOFF", f"⏰ {now} (VN)",
        f"✅ Quét được: {len(ok)}/{len(results)} | ❌ Lỗi/không đủ data: {len(fail)}",
        f"🟣 {len(purple)} | 🟡 {len(yellow)} | 🟢 {len(green)} | 🌱 Spring {len(spring)} | ⚠️ Upthrust {len(ut)}",
    ]
    groups = [("\n🟣 CÁ MẬP TÍM", purple, "🟣"), ("\n🟡 CÁ MẬP VÀNG", yellow, "🟡"), ("\n🟢 CÁ MẬP XANH", green, "🟢"), ("\n🌱 WYCKOFF SPRING", spring, "🌱"), ("\n⚠️ WYCKOFF UPTHRUST", ut, "⚠️")]
    any_signal = False
    for title, arr, emoji in groups:
        if not arr: continue
        any_signal = True
        lines.append(title)
        lines.extend(signal_line(r, emoji) for r in arr)
    if not any_signal:
        lines.append("\n⚪ Phiên mới nhất chưa có tín hiệu tím/vàng/xanh, Spring hoặc Upthrust.")
    if fail:
        sample = ", ".join(r.symbol for r in fail[:30])
        lines.append(f"\nℹ️ Mã lỗi/không đủ dữ liệu (tối đa 30): {sample}" + ("…" if len(fail) > 30 else ""))
    lines.append("\nLưu ý: đây là bộ lọc tín hiệu theo đúng rule của file nguồn, không phải khuyến nghị mua/bán.")
    return "\n".join(lines)


def chunks(text: str, max_len: int = 3900) -> list[str]:
    if len(text) <= max_len: return [text]
    out, cur = [], ""
    for line in text.splitlines(True):
        if len(cur) + len(line) > max_len and cur:
            out.append(cur.rstrip()); cur = ""
        if len(line) > max_len:
            for i in range(0, len(line), max_len):
                part = line[i:i+max_len]
                if cur: out.append(cur.rstrip()); cur = ""
                out.append(part.rstrip())
        else:
            cur += line
    if cur: out.append(cur.rstrip())
    return out


def telegram_send(text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Thiếu GitHub Secrets TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for part in chunks(text):
        r = requests.post(url, json={"chat_id": chat_id, "text": part, "disable_web_page_preview": True}, timeout=20)
        r.raise_for_status()
        time.sleep(0.15)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="symbols.txt")
    ap.add_argument("--telegram-test", action="store_true")
    args = ap.parse_args()
    if args.telegram_test:
        telegram_send("✅ Test BOT QUÉT CÁ MẬP: Telegram đã kết nối thành công.")
        print("Telegram test OK")
        return 0
    symbols = read_symbols(args.symbols)
    print(f"Scanning {len(symbols)} symbols with {MAX_WORKERS} workers...")
    results: list[ScanResult] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(scan_symbol, s): s for s in symbols}
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result(); results.append(r)
            status = "ERR" if r.error else (r.shark_color or ("SPRING" if r.spring else ("UT" if r.upthrust else "OK")))
            print(f"[{i}/{len(symbols)}] {r.symbol}: {status}")
    results.sort(key=lambda r: r.symbol)
    report = build_report(results)
    Path("scan_report.txt").write_text(report + "\n", encoding="utf-8")
    print(report)
    telegram_send(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
