class Portfolio:
    def __init__(self, db, initial_equity):
        self.db = db
        if db.get("cash") is None:
            db.set("cash", initial_equity)

    @property
    def cash(self):
        return self.db.get("cash")

    @property
    def positions(self):
        return self.db.positions()

    def equity(self, prices):
        positions = self.positions
        if any(s not in prices for s in positions):
            raise ValueError("Missing position mark")
        return self.cash + sum(p.pnl(prices[s]) for s, p in positions.items())

    def available(self, prices):
        return self.equity(prices) - sum(
            p.entry * p.size / p.leverage for p in self.positions.values()
        )
