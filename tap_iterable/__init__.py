"""Tap for Iterable."""

import singer_sdk.typing as th

RateType = th.NumberType(minimum=0, maximum=1)
