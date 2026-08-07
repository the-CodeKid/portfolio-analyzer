"""Ported from sipRollingXirr/testFixtures.ts."""

from datetime import date

from xirr.types import NavEntry

# Fast growing fund: +20% monthly
fast_growing_fund = [
    NavEntry(date(2023, 1, 1), 100), NavEntry(date(2023, 2, 1), 120), NavEntry(date(2023, 3, 1), 144),
    NavEntry(date(2023, 4, 1), 172.8), NavEntry(date(2023, 5, 1), 207.36), NavEntry(date(2023, 6, 1), 248.83),
    NavEntry(date(2023, 7, 1), 298.60), NavEntry(date(2023, 8, 1), 358.32), NavEntry(date(2023, 9, 1), 429.98),
    NavEntry(date(2023, 10, 1), 515.98), NavEntry(date(2023, 11, 1), 619.18), NavEntry(date(2023, 12, 1), 743.01),
    NavEntry(date(2024, 1, 1), 891.61),
]

# Slow growing fund: +2% monthly
slow_growing_fund = [
    NavEntry(date(2023, 1, 1), 100), NavEntry(date(2023, 2, 1), 102), NavEntry(date(2023, 3, 1), 104.04),
    NavEntry(date(2023, 4, 1), 106.12), NavEntry(date(2023, 5, 1), 108.24), NavEntry(date(2023, 6, 1), 110.41),
    NavEntry(date(2023, 7, 1), 112.61), NavEntry(date(2023, 8, 1), 114.87), NavEntry(date(2023, 9, 1), 117.16),
    NavEntry(date(2023, 10, 1), 119.51), NavEntry(date(2023, 11, 1), 121.90), NavEntry(date(2023, 12, 1), 124.34),
    NavEntry(date(2024, 1, 1), 126.82),
]

# Stable fund 1: +1% monthly
stable_fund_1 = [
    NavEntry(date(2023, 1, 1), 100), NavEntry(date(2023, 2, 1), 101), NavEntry(date(2023, 3, 1), 102),
    NavEntry(date(2023, 4, 1), 103), NavEntry(date(2023, 5, 1), 104), NavEntry(date(2023, 6, 1), 105),
    NavEntry(date(2023, 7, 1), 106), NavEntry(date(2023, 8, 1), 107), NavEntry(date(2023, 9, 1), 108),
    NavEntry(date(2023, 10, 1), 109), NavEntry(date(2023, 11, 1), 110), NavEntry(date(2023, 12, 1), 111),
    NavEntry(date(2024, 1, 1), 112),
]

# Stable fund 2: +1.5% monthly
stable_fund_2 = [
    NavEntry(date(2023, 1, 1), 100), NavEntry(date(2023, 2, 1), 101.5), NavEntry(date(2023, 3, 1), 103),
    NavEntry(date(2023, 4, 1), 104.5), NavEntry(date(2023, 5, 1), 106), NavEntry(date(2023, 6, 1), 107.5),
    NavEntry(date(2023, 7, 1), 109), NavEntry(date(2023, 8, 1), 110.5), NavEntry(date(2023, 9, 1), 112),
    NavEntry(date(2023, 10, 1), 113.5), NavEntry(date(2023, 11, 1), 115), NavEntry(date(2023, 12, 1), 116.5),
    NavEntry(date(2024, 1, 1), 118),
]

# Moderate growth fund: +5% monthly
moderate_growth_fund = [
    NavEntry(date(2023, 1, 1), 100), NavEntry(date(2023, 2, 1), 105), NavEntry(date(2023, 3, 1), 110),
    NavEntry(date(2023, 4, 1), 115), NavEntry(date(2023, 5, 1), 120), NavEntry(date(2023, 6, 1), 125),
    NavEntry(date(2023, 7, 1), 130), NavEntry(date(2023, 8, 1), 135), NavEntry(date(2023, 9, 1), 140),
    NavEntry(date(2023, 10, 1), 145), NavEntry(date(2023, 11, 1), 150), NavEntry(date(2023, 12, 1), 155),
    NavEntry(date(2024, 1, 1), 160),
]

# Declining fund: -5% monthly
declining_fund = [
    NavEntry(date(2023, 1, 1), 100), NavEntry(date(2023, 2, 1), 95), NavEntry(date(2023, 3, 1), 90),
    NavEntry(date(2023, 4, 1), 85), NavEntry(date(2023, 5, 1), 80), NavEntry(date(2023, 6, 1), 75),
    NavEntry(date(2023, 7, 1), 70), NavEntry(date(2023, 8, 1), 65), NavEntry(date(2023, 9, 1), 60),
    NavEntry(date(2023, 10, 1), 55), NavEntry(date(2023, 11, 1), 50), NavEntry(date(2023, 12, 1), 45),
    NavEntry(date(2024, 1, 1), 40),
]
