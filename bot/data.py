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
