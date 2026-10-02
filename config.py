"""Strict configuration. Credentials are read only from the selected dotenv file."""

from dataclasses import dataclass, fields, field
from pathlib import Path
import os
import math
from dotenv import dotenv_values


@dataclass(frozen=True)
class Config:
    trading_mode: str = "paper"
    enable_live_trading: bool = False
    delta_api_key: str = field(default="", repr=False)
    delta_api_secret: str = field(default="", repr=False)
    delta_testnet_api_key: str = field(default="", repr=False)
    delta_testnet_api_secret: str = field(default="", repr=False)
    symbols: str = "BTCUSD,ETHUSD"
    timeframe: str = "30m"
    supertrend_period: int = 13
    supertrend_multiplier: float = 4.0
    ema_period: int = 200
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    risk_per_trade_percent: float = 1.0
    default_leverage: float = 5.0
    max_leverage: float = 10.0
    max_daily_loss_percent: float = 3.0
    max_open_positions: int = 2
    max_position_equity_percent: float = 20.0
    max_trades_per_day: int = 6
    cooldown_after_loss_minutes: int = 60
    enable_pyramiding: bool = False
    pyramid_capital_percent: float = 10.0
    max_pyramid_adds: int = 3
    use_volume_filter: bool = False
    paper_equity: float = 10000.0
    fee_rate: float = 0.0005
    slippage_bps: float = 5.0
    poll_seconds: int = 15
    stale_seconds: int = 120
    database_path: str = ""
    log_path: str = "logs/bot.log"
    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = field(default="", repr=False)
    risk_timezone: str = "Asia/Kolkata"

    def __post_init__(self):
        if self.trading_mode not in {"paper", "testnet", "live"}:
            raise ValueError("TRADING_MODE must be paper, testnet or live")
        if self.trading_mode == "live" and not self.enable_live_trading:
            raise ValueError("LIVE TRADING DISABLED: both live flags are required")
        if not 1 <= self.default_leverage <= self.max_leverage <= 10:
            raise ValueError("Require 1 <= DEFAULT_LEVERAGE <= MAX_LEVERAGE <= 10")
        if (
            self.timeframe,
            self.ema_period,
            self.supertrend_period,
            self.supertrend_multiplier,
            self.macd_fast,
            self.macd_slow,
            self.macd_signal,
        ) != ("30m", 200, 13, 4, 12, 26, 9):
            raise ValueError("V1 strategy parameters are fixed to the requested rules")
        if self.enable_pyramiding or self.use_volume_filter:
            raise ValueError(
                "V1: pyramiding and subjective volume filters are disabled pending approved deterministic rules"
            )
        for name in (
            "risk_per_trade_percent",
            "max_daily_loss_percent",
            "max_position_equity_percent",
            "pyramid_capital_percent",
        ):
            if not 0 < getattr(self, name) <= 100:
                raise ValueError(f"{name} must be in (0, 100]")
        for f in fields(self):
            v = getattr(self, f.name)
            if (
                isinstance(v, (float, int))
                and not isinstance(v, bool)
                and (not math.isfinite(v) or v < 0)
            ):
                raise ValueError(f"{f.name} must be finite and nonnegative")
        if (
            min(
                self.paper_equity,
                self.poll_seconds,
                self.stale_seconds,
                self.max_open_positions,
                self.max_trades_per_day,
            )
            <= 0
        ):
            raise ValueError("Equity, polling and limits must be positive")
        import re

        if not self.symbol_list or any(
            not re.fullmatch(r"[A-Z0-9]{3,40}", s) for s in self.symbol_list
        ):
            raise ValueError(
                "SYMBOLS must be comma-separated alphanumeric product symbols"
            )
        from zoneinfo import ZoneInfo

        ZoneInfo(self.risk_timezone)

    @property
    def db_path(self):
        return self.database_path or f"state/{self.trading_mode}.db"

    @property
    def symbol_list(self):
        return list(
            dict.fromkeys(
                s.strip().upper() for s in self.symbols.split(",") if s.strip()
            )
        )

    @property
    def base_url(self):
        return (
            "https://cdn-ind.testnet.deltaex.org"
            if self.trading_mode == "testnet"
            else "https://api.india.delta.exchange"
        )

    @property
    def credentials(self):
        if self.trading_mode == "paper":
            return "", ""
        if self.trading_mode == "testnet":
            return self.delta_testnet_api_key, self.delta_testnet_api_secret
        return self.delta_api_key, self.delta_api_secret

    @classmethod
    def load(cls, path=".env"):
        env = dotenv_values(Path(path))
        values = {}
        defaults = cls()
        for f in fields(cls):
            key = f.name.upper()
            # Secrets must be in .env; environment can override other settings.
            secret = any(
                x in f.name for x in ("api_key", "api_secret", "bot_token", "chat_id")
            )
            raw = env.get(key) if secret else os.environ.get(key, env.get(key))
            if raw is None:
                continue
            default = getattr(defaults, f.name)
            if isinstance(default, bool):
                if str(raw).lower() not in {"true", "false"}:
                    raise ValueError(f"{key} must be true or false")
                values[f.name] = str(raw).lower() == "true"
            else:
                values[f.name] = type(default)(raw)
        return cls(**values)
