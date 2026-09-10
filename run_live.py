import argparse
from bot.config import Config
from bot.live import LiveTrader


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument(
        "--i-understand-the-risk",
        action="store_true",
        help="required to run against testnet=false (real money) in config.yaml",
    )
    args = p.parse_args()

    cfg = Config.load(args.config)

    if not cfg.testnet and not args.i_understand_the_risk:
        raise SystemExit(
            "config.yaml has testnet: false (REAL MONEY) but --i-understand-the-risk was not passed. "
            "Refusing to start. Test on testnet first."
        )

    trader = LiveTrader(cfg)
    trader.run_forever()


if __name__ == "__main__":
    main()
