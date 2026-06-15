# Simple SMC AG Trading Bot — one-command flows.
# Run `make help` to see targets. Nothing here can enable live trading:
# live is owner-only via the LIVE_TRADING flag in .env (see CLAUDE.md §1, §7).

PYTHON ?= python3
DAYS   ?= 730

.DEFAULT_GOAL := help
.PHONY: help setup test fetch backtest gate paper clean

help:                ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Typical first run:  make setup && make test && make gate"

setup:               ## Install Python dependencies
	$(PYTHON) -m pip install -r requirements.txt

test:                ## Run the unit test suite
	$(PYTHON) -m pytest -q

fetch:               ## Download 1H + 5M BTCUSDT data from Bybit (DAYS=730)
	$(PYTHON) scripts/fetch_data.py --interval 60 --days $(DAYS)
	$(PYTHON) scripts/fetch_data.py --interval 5  --days $(DAYS)

backtest:            ## Run the Phase-0 gate on cached data (n>=50 AND net PF>1.0)
	$(PYTHON) scripts/backtest.py

gate: fetch backtest ## Full Phase-0: fetch data, then run the gate

paper:               ## Run the bot in PAPER mode (no real orders)
	@echo "Starting bot in PAPER mode. Ctrl-C to stop."
	@echo "NOTE: live trading is owner-only — flip LIVE_TRADING in .env manually (CLAUDE.md §1)."
	LIVE_TRADING=false $(PYTHON) -m bot.runner

clean:               ## Remove cached OHLCV parquet files
	rm -rf data/cache
