"""Explicit user-directed demo entry; never represents a strategy confirmation."""

from dataclasses import dataclass, asdict, replace
import hashlib


@dataclass(frozen=True)
class ManualDemoEntry:
    symbol: str
    signal: str
    contracts: int
    entry_reference: float
    supertrend: float
    stop_candle: int
    request_id: str
    reason: tuple = ("USER_REQUESTED_DEMO_ENTRY", "Not a strategy signal")

    def __post_init__(self):
        if (
            self.signal not in {"LONG", "SHORT"}
            or type(self.contracts) is not int
            or self.contracts < 1
        ):
            raise ValueError(
                "Manual demo order requires a side and positive whole contracts"
            )
        if not self.request_id:
            raise ValueError("A stable manual request ID is required")

    @property
    def signal_id(self):
        return f"manual-demo:{self.symbol}:{self.signal}:{self.request_id}"

    def client_id(self, kind="entry"):
        return (
            "ds"
            + hashlib.sha256((self.signal_id + ":" + kind).encode()).hexdigest()[:30]
        )

    def to_dict(self):
        return dict(asdict(self), source="MANUAL_DEMO_REQUEST")


def fixed_demo_size(maximum, requested, spec):
    if (
        type(requested) is not int
        or requested < spec.min_contracts
        or requested % spec.quantity_step
    ):
        raise ValueError("INVALID_CONTRACT_QUANTITY")
    if requested > maximum.contracts:
        raise ValueError(
            f"REQUESTED_SIZE_EXCEEDS_RISK_LIMIT: maximum {maximum.contracts} contracts"
        )
    fraction = requested / maximum.contracts
    return replace(
        maximum,
        contracts=requested,
        quantity=float(spec.contract_value * requested),
        estimated_loss=maximum.estimated_loss * fraction,
        margin=maximum.margin * fraction,
        notional=maximum.notional * fraction,
    )
