from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    product_id: int
    contract_value: Decimal
    tick_size: Decimal
    min_contracts: int = 1
    quantity_step: int = 1
    max_contracts: int = 0
    settlement: str = "USD"
    taker_fee: float = 0.0005
    initial_margin_percent: float = 0.0

    def __post_init__(self):
        if (
            not self.contract_value.is_finite()
            or not self.tick_size.is_finite()
            or min(self.contract_value, self.tick_size) <= 0
        ):
            raise ValueError("Invalid contract specification")
        if min(self.min_contracts, self.quantity_step) < 1:
            raise ValueError("Invalid contract lot")

    @classmethod
    def from_api(cls, p):
        if (
            p["contract_type"] != "perpetual_futures"
            or p["notional_type"] != "vanilla"
            or p.get("is_quanto", False)
            or p["contract_unit_currency"] != p["underlying_asset"]["symbol"]
            or p["settling_asset"]["symbol"] != "USD"
            or p["quoting_asset"]["symbol"] != "USD"
        ):
            raise ValueError(
                "Only linear USD settled/quoted perpetuals are supported; currency conversion is not guessed"
            )
        if (
            p["state"] != "live"
            or p["trading_status"] != "operational"
            or p["product_specs"].get("only_reduce_only_orders_allowed")
        ):
            raise ValueError("Instrument is not available for entries")
        # REST order size is integer contracts. API currently supplies no lot/min fields.
        return cls(
            p["symbol"],
            int(p["id"]),
            Decimal(p["contract_value"]),
            Decimal(p["tick_size"]),
            max_contracts=int(p.get("position_size_limit", 0)),
            taker_fee=float(p["taker_commission_rate"]),
            initial_margin_percent=float(p["initial_margin"]),
        )
