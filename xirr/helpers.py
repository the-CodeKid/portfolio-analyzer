"""Ported from sipRollingXirr/core/helpers.ts, utils/date/dateUtils.ts, and
utils/data/fillMissingNavDates.ts.

fill_missing_nav_dates is mislabeled upstream: the comment says "forward
fill" but it actually fills gap days with the *next* available NAV entry,
not the previous one (traced against the upstream nil-transaction test
fixture, where a 30-day Jan-to-Feb gap gets Feb's NAV throughout). Ported
here exactly as it behaves, not as it's described.
"""

from __future__ import annotations

import calendar
import math
from datetime import date, timedelta

from xirr.types import NavEntry


def is_valid_input(nav_data_list: list[list[NavEntry]]) -> bool:
    return len(nav_data_list) > 0 and not any(len(f) < 2 for f in nav_data_list)


def are_dates_continuous(nav_data: list[NavEntry]) -> bool:
    if len(nav_data) < 2:
        return True
    sorted_data = sorted(nav_data, key=lambda e: e.date)
    for prev, curr in zip(sorted_data, sorted_data[1:]):
        if (curr.date - prev.date).days != 1:
            return False
    return True


def fill_missing_nav_dates(nav_data: list[NavEntry]) -> list[NavEntry]:
    if not nav_data:
        return []
    sorted_data = sorted(nav_data, key=lambda e: e.date)
    filled: list[NavEntry] = []
    i = 0
    current = sorted_data[0].date
    last = sorted_data[-1].date

    while current <= last:
        if i < len(sorted_data) and current == sorted_data[i].date:
            filled.append(NavEntry(date=current, nav=sorted_data[i].nav))
            i += 1
        else:
            filled.append(NavEntry(date=current, nav=sorted_data[i].nav))
        current = current + timedelta(days=1)

    return filled


def ensure_continuous_dates(fund: list[NavEntry]) -> list[NavEntry]:
    return fund if are_dates_continuous(fund) else fill_missing_nav_dates(fund)


def build_date_map(fund: list[NavEntry]) -> dict[str, NavEntry]:
    return {to_date_key(entry.date): entry for entry in fund}


def get_sorted_dates(fund: list[NavEntry]) -> list[date]:
    return [entry.date for entry in sorted(fund, key=lambda e: e.date)]


def to_date_key(d: date) -> str:
    return d.isoformat()


def get_nth_previous_month_date(current_date: date, months: float) -> date:
    """N months before current_date, clamped to the target month's last day
    when current_date's day-of-month doesn't exist there (e.g. Mar 31 minus
    1 month -> Feb 28/29).

    `months` may be fractional (callers derive it from `years * 12`). JS's
    setMonth() truncates (current_month_index - months) toward zero *after*
    the subtraction, not `months` on its own -- those give different answers
    once months has a fractional part, so the truncation order is preserved
    here rather than just doing int(months).
    """
    current_month0 = current_date.month - 1
    truncated_delta = math.trunc(current_month0 - months)
    total_months = current_date.year * 12 + truncated_delta
    target_year, target_month0 = divmod(total_months, 12)
    target_month = target_month0 + 1
    days_in_target = calendar.monthrange(target_year, target_month)[1]
    day = min(current_date.day, days_in_target)
    return date(target_year, target_month, day)


def generate_sip_dates(
    current_date: date, months: float, first_date: date
) -> tuple[set[str], date | None]:
    sip_dates: set[str] = set()
    earliest_sip_date: date | None = None

    m = months
    while m >= 1:
        sip_date = get_nth_previous_month_date(current_date, m)
        if sip_date < first_date:
            return set(), None

        sip_dates.add(to_date_key(sip_date))
        if earliest_sip_date is None or sip_date < earliest_sip_date:
            earliest_sip_date = sip_date
        m -= 1

    return sip_dates, earliest_sip_date


def get_investment_year(current_date: date, first_sip_date: date) -> int:
    """1-based investment year for step-up SIP, counting from first_sip_date."""
    years_diff = current_date.year - first_sip_date.year
    months_diff = current_date.month - first_sip_date.month
    total_years = years_diff + (0 if months_diff >= 0 else -1)
    return total_years + 1
