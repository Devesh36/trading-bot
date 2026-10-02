from dataclasses import dataclass, field, asdict
import hashlib


@dataclass(frozen=True)
class Signal:
    signal: str = "NONE"
    symbol: str = ""
    timeframe: str = "30m"
    signal_candle: int = 0
    confirmation_candle: int = 0
    entry_reference: float = 0
    ema200: float = 0
    supertrend: float = 0
    macd: float = 0
    macd_signal: float = 0
    macd_histogram: float = 0
    reason: tuple = field(default_factory=tuple)

    @property
    def signal_id(self):
        return f"{self.symbol}:30m:{self.signal}:{self.confirmation_candle}"

    def client_id(self, kind="entry"):
        return (
            "ds"
            + hashlib.sha256((self.signal_id + ":" + kind).encode()).hexdigest()[:30]
        )

    def to_dict(self):
        return asdict(self)
