"""Run-level options that shape *how* values are drawn, not *which* values.

Everything here is a knob on distributions. `generate_data_from_dbml` has
always answered "what goes in this column" from the schema; these options
answer "how often", "how spread" and "when", which the schema cannot say and
uniform randomness gets wrong in ways a dashboard makes visible: every hour of
the night as busy as noon, every customer with the same number of orders.

Both option groups default to today's behaviour, so a caller that passes
nothing generates exactly what 1.4.0 generated with the same seed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TimeProfile:
    """How generated timestamps and dates are spread across their window.

    `business_hours` weights timestamps toward weekdays and working hours
    instead of spreading them evenly over every second of the window. A
    night-time order is still possible, just rare -- the way it is in real
    data.

    `growth` is the relative change in volume across the window: `0.5` means
    the end of the window is half again as busy as the start, `-0.3` means it
    tailed off. `0.0` is flat. Bounded below at `-1.0`, which would mean the
    window ends with no activity at all -- past that there is nothing to draw.

    `seasonality` is the amplitude of an annual cycle over the window, `0.0`
    (none) to `1.0` (the peak month is twice the average and the trough is
    empty). The peak sits in the fourth quarter, the shape that retail, SaaS
    renewals and most other business calendars share.

    Ordering within a row (`created_at` before `updated_at`) is *not* a profile
    setting: it is always on, because a row updated before it was created is
    wrong under any profile.
    """

    business_hours: bool = False
    growth: float = 0.0
    seasonality: float = 0.0

    def __post_init__(self) -> None:
        if self.growth < -1.0:
            raise ValueError(
                f"growth must be -1.0 or more (got {self.growth}): -1.0 already means "
                "the window ends with no activity at all."
            )
        if not 0.0 <= self.seasonality <= 1.0:
            raise ValueError(f"seasonality must be between 0.0 and 1.0 (got {self.seasonality}).")

    @property
    def is_uniform(self) -> bool:
        """True when this profile changes nothing about a uniform draw."""
        return not self.business_hours and self.growth == 0.0 and self.seasonality == 0.0


# The profile a run gets when it passes nothing: uniform in every respect, so
# the same seed reproduces what earlier releases produced.
UNIFORM = TimeProfile()


def validate_skew(skew: float) -> float:
    """Check a run-level `skew` and hand it back.

    `skew` is how unevenly a child table's rows are spread over its parents.
    `0.0` is uniform: every parent is equally likely to be picked for every
    child row, which is what every release before this one did. `1.0` is the
    steepest supported shape, a few parents holding most of the children. The
    familiar "a fifth of the customers place most of the orders" sits around
    `0.8`. Values outside `[0.0, 1.0]` are refused rather than clamped, because
    a clamped `5` silently generating the same data as `1` is exactly the
    kind of no-op nobody notices.
    """
    if not 0.0 <= skew <= 1.0:
        raise ValueError(f"skew must be between 0.0 and 1.0 (got {skew}).")
    return skew
