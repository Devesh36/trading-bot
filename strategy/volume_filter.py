"""Observational only: rolling extrema are not discretionary chart pivots."""


def allowed(context, enabled=False):
    if enabled:
        raise NotImplementedError(
            "A deterministic volume filter must be specified and reviewed first"
        )
    return True
