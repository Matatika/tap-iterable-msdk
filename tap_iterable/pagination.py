"""Pagination classes for tap-iterable."""

from __future__ import annotations

import typing as t
from datetime import datetime, timedelta, timezone

from singer_sdk.pagination import BaseAPIPaginator
from typing_extensions import override

DateTimeIntervalTokenType = tuple[datetime, t.Optional[datetime]]


class DateTimeIntervalPaginator(BaseAPIPaginator[DateTimeIntervalTokenType]):
    """Date-time interval paginator."""

    @override
    def __init__(self, *, start: datetime, interval: timedelta) -> None:
        self.interval = interval
        super().__init__(self._get_date_range(start))

    @override
    def get_next(self, response):
        end_date = self.current_value[-1]

        if not end_date:
            return None  # end pagination

        return self._get_date_range(end_date)

    @override
    def continue_if_empty(self, response):
        return True

    def _get_date_range(self, start: datetime) -> DateTimeIntervalTokenType:
        end = start + self.interval

        # `startDateTime` is inclusive, `endDateTime` is exclusive
        return start, end if end < datetime.now(tz=timezone.utc) else None
