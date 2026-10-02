from risk.position_sizing import round_stop


def favorable_stop(position, level, spec):
    proposed = round_stop(float(level), spec, position.direction)
    return (
        max(position.stop, proposed)
        if position.direction == "LONG"
        else min(position.stop, proposed)
    )


def stop_hit(position, high, low):
    return (
        low <= position.stop if position.direction == "LONG" else high >= position.stop
    )


def stop_fill_reference(position, open_price):
    return (
        min(open_price, position.stop)
        if position.direction == "LONG"
        else max(open_price, position.stop)
    )
