from .config import OrderbookStrategyConfig
from .data import fetch_normalized_imbalance, find_nearest_wall
from .strategy import Signal


def compute_orderbook_signal(exchange, symbol: str, cfg: OrderbookStrategyConfig, obi_state: dict):
    """Independent of the mean-reversion strategy: looks for a strong, persistent
    order-book imbalance, backed by a real resting wall, on a symbol that hasn't
    already made a big move (that's the other strategy's territory). A leading
    signal from order-flow pressure, instead of reacting to a move that already
    happened.

    obi_state is mutated across calls (per symbol: direction + consecutive-cycle
    count) to require the imbalance to persist for cfg.obi_persist_cycles polls
    before it's trusted - a single strong reading is easy to fake (spoofing: a big
    order placed and pulled before it would ever fill).
    """
    obi = fetch_normalized_imbalance(exchange, symbol, cfg.obi_depth_levels, cfg.obi_price_range_pct)

    direction = None
    if obi >= cfg.obi_threshold:
        direction = "long"
    elif obi <= -cfg.obi_threshold:
        direction = "short"

    if direction is None:
        obi_state.pop(symbol, None)
        return None

    state = obi_state.get(symbol)
    if state is None or state["direction"] != direction:
        obi_state[symbol] = {"direction": direction, "cycles": 1}
        return None  # first sighting this direction - wait for it to persist

    state["cycles"] += 1
    if state["cycles"] < cfg.obi_persist_cycles:
        return None

    # persisted long enough - now require a genuine wall backing this direction,
    # the order-book equivalent of "volume exhaustion" (aggressive flow absorbed
    # without price moving through it)
    ticker = exchange.fetch_ticker(symbol)
    price = float(ticker["last"])
    wall_side = "bids" if direction == "long" else "asks"
    far_stop = price * (1 - cfg.max_stop_pct / 100) if direction == "long" else price * (1 + cfg.max_stop_pct / 100)
    wall_price = find_nearest_wall(
        exchange, symbol, wall_side, price, far_stop,
        cfg.wall_multiplier, cfg.min_wall_usd, cfg.wall_scan_depth,
    )
    if wall_price is None:
        return None  # imbalance without a concrete resting wall - too easy to fake

    obi_state.pop(symbol, None)  # signal fired (or invalid) - reset persistence tracking

    buffer = wall_price * 0.001
    opp_side = "asks" if direction == "long" else "bids"
    far_target = price * (1 + cfg.max_stop_pct / 100) if direction == "long" else price * (1 - cfg.max_stop_pct / 100)
    opposite_wall = find_nearest_wall(
        exchange, symbol, opp_side, price, far_target,
        cfg.wall_multiplier, cfg.min_wall_usd, cfg.wall_scan_depth,
    )

    if direction == "long":
        stop = wall_price - buffer
        risk = price - stop
        # only use the opposite wall as the target if it's a meaningful distance away
        # (at least 1R) - a wall sitting right at/near the current price (e.g. simply
        # the best ask) makes a degenerate, near-zero target, not a real one
        min_tp_distance = max(risk, price * 0.002)
        take_profit = (
            opposite_wall if (opposite_wall and opposite_wall - price >= min_tp_distance)
            else price + cfg.take_profit_r_multiple * risk
        )
    else:
        stop = wall_price + buffer
        risk = stop - price
        min_tp_distance = max(risk, price * 0.002)
        take_profit = (
            opposite_wall if (opposite_wall and price - opposite_wall >= min_tp_distance)
            else price - cfg.take_profit_r_multiple * risk
        )

    if direction == "long" and not (stop < price < take_profit):
        return None
    if direction == "short" and not (take_profit < price < stop):
        return None

    return Signal(
        direction, price, stop, take_profit,
        f"order-book imbalance {obi:+.2f} sustained {state['cycles']} cycles, wall-confirmed",
        strategy="orderbook_imbalance",
    )
