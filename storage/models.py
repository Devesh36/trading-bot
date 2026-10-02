from dataclasses import dataclass, asdict


@dataclass
class Position:
    symbol: str
    direction: str
    entry: float
    contracts: int
    contract_value: float
    stop: float
    leverage: float
    entry_timestamp: int
    signal_id: str
    entry_fees: float = 0
    entry_slippage: float = 0
    stop_order_id: str = ""
    order_id: str = ""
    status: str = "open"
    reason: str = ""
    last_managed_candle: int = 0

    @property
    def size(self):
        return self.contracts * self.contract_value

    @property
    def sign(self):
        return 1 if self.direction == "LONG" else -1

    def pnl(self, price):
        return self.sign * (price - self.entry) * self.size

    def to_dict(self):
        return asdict(self)
