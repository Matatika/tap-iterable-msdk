"""Iterable tap class."""

from __future__ import annotations

from singer_sdk import Tap
from singer_sdk import typing as th  # JSON schema typing helpers
from typing_extensions import override

from tap_iterable import streams


class TapIterable(Tap):
    """Iterable tap class."""

    name = "tap-iterable"

    config_jsonschema = th.PropertiesList(
        th.Property(
            "api_key",
            th.StringType,
            required=True,
            secret=True,
            title="API key",
            description="Iterable API key",
        ),
        th.Property(
            "region",
            th.StringType,
            allowed_values=("US", "EU"),
            default="US",
            title="Region",
            description="Iterable region",
        ),
        th.Property(
            "start_date",
            th.DateTimeType,
            description="Timestamp in ISO 8601 format to get data from (inclusive)",
        ),
        th.Property(
            "end_date",
            th.DateTimeType,
            description="Timestamp in ISO 8601 format to get data up to (inclusive)",
        ),
    ).to_dict()

    @override
    def discover_streams(self) -> list[streams.IterableStream]:
        return [
            streams.ListsStream(self),
            streams.ListUsersStream(self),
            streams.CampaignsStream(self),
            streams.ChannelsStream(self),
            streams.MessageTypesStream(self),
            streams.TemplatesStream(self),
            streams.MetadataStream(self),
            streams.EmailBounceStream(self),
            streams.EmailClickStream(self),
            streams.EmailComplaintStream(self),
            streams.EmailOpenStream(self),
            streams.EmailSendStream(self),
            streams.EmailSendSkipStream(self),
            streams.EmailSubscribeStream(self),
            streams.EmailUnsubscribeStream(self),
            streams.SMSBounceStream(self),
            streams.SMSClickStream(self),
            streams.SMSReceivedStream(self),
            streams.SMSSendStream(self),
            streams.SMSSendSkipStream(self),
            streams.WebPushClickStream(self),
            streams.WebPushSendStream(self),
            streams.WebPushSendSkipStream(self),
            # streams.UsersStream(self),
            streams.CustomEventStream(self),
        ]


if __name__ == "__main__":
    TapIterable.cli()
