import pandas as pd
import ccxt


def fetch_ohlcv_df(exchange: ccxt.Exchange, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df


def scan_movers(
    exchange: ccxt.Exchange, quote_currency: str, top_n: int, min_abs_move_pct: float,
    min_quote_volume_usd: float = 0.0,
):
    """Rank USDT-M perpetual symbols by absolute 24h % change.

    Uses the exchange's 24h ticker stats as a cheap proxy for "moved a lot recently" -
    good enough for a screener, not a precise window match.
    """
    tickers = exchange.fetch_tickers()
    candidates = []
    for symbol, t in tickers.items():
        market = exchange.markets.get(symbol)
        if not market or not market.get("swap") or market.get("quote") != quote_currency:
            continue
        if min_quote_volume_usd > 0 and float(t.get("quoteVolume") or 0) < min_quote_volume_usd:
            continue
        pct = t.get("percentage")
        if pct is None:
            continue
        if abs(pct) >= min_abs_move_pct:
            candidates.append((symbol, pct))
    candidates.sort(key=lambda x: abs(x[1]), reverse=True)
    return candidates[:top_n]


def fetch_orderbook_imbalance(exchange: ccxt.Exchange, symbol: str, depth: int, price_range_pct: float) -> float:
    """bid_volume / ask_volume within price_range_pct of mid, using top `depth` levels."""
    ob = exchange.fetch_order_book(symbol, limit=depth)
    bids, asks = ob["bids"], ob["asks"]
    if not bids or not asks:
        return 1.0
    mid = (bids[0][0] + asks[0][0]) / 2
    lo = mid * (1 - price_range_pct / 100)
    hi = mid * (1 + price_range_pct / 100)
    bid_vol = sum(qty for price, qty in bids if price >= lo)
    ask_vol = sum(qty for price, qty in asks if price <= hi)
    if ask_vol == 0:
        return float("inf")
    return bid_vol / ask_vol


def find_nearest_wall(
    exchange: ccxt.Exchange, symbol: str, side: str, near_price: float, far_price: float,
    wall_multiplier: float, min_wall_usd: float, depth: int,
):
    """Scan the order book between near_price and far_price for a price level whose
    resting size stands out from its neighbors (wall_multiplier x the local median)
    and is worth at least min_wall_usd - a real support/resistance level, more
    meaningful for a stop-loss than a pure ATR distance.

    side: "bids" to look for support below price (for a long's stop), "asks" for
    resistance above price (for a short's stop). Returns the wall price closest to
    near_price (the tightest valid stop), or None if nothing qualifies.
    """
    ob = exchange.fetch_order_book(symbol, limit=depth)
    levels = ob.get(side) or []
    lo, hi = min(near_price, far_price), max(near_price, far_price)
    in_range = [(p, q) for p, q in levels if lo <= p <= hi]
    if len(in_range) < 5:
        return None

    qtys = sorted(q for _, q in in_range)
    median_qty = qtys[len(qtys) // 2]
    if median_qty <= 0:
        return None

    walls = [
        (p, q) for p, q in in_range
        if q >= median_qty * wall_multiplier and q * p >= min_wall_usd
    ]
    if not walls:
        return None

    walls.sort(key=lambda w: abs(w[0] - near_price))
    return walls[0][0]
