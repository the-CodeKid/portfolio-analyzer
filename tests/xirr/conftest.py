"""close_to replicates Jest's toBeCloseTo(expected, precision) tolerance
exactly: passes when abs(actual-expected) < 10**-precision / 2."""


def close_to(actual: float, expected: float, precision: int = 2) -> bool:
    return abs(actual - expected) < (10 ** -precision) / 2
