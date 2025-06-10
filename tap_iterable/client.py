"""REST client handling, including IterableStream base class."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import cached_property
from importlib import resources

from singer_sdk.authenticators import APIKeyAuthenticator
from singer_sdk.pagination import BaseAPIPaginator  # noqa: TC002
from singer_sdk.streams import RESTStream
from typing_extensions import override

SCHEMAS_DIR = resources.files(__package__) / "schemas"


class IterableStream(RESTStream):
    """Iterable stream class."""

    # disable default pagination logic as some endpoints responses are not JSON (and
    # none support pagination anyway)
    next_page_token_jsonpath = None

    @override
    @cached_property
    def url_base(self):
        if self.config["region"] == "EU":
            return "https://api.eu.iterable.com/api"

        return "https://api.iterable.com/api"

    @override
    @property
    def authenticator(self):
        return APIKeyAuthenticator.create_for_stream(
            self,
            key="Api-Key",
            value=self.config["api_key"],
            location="header",
        )

    @property
    def http_headers(self) -> dict:
        """Return the http headers needed.

        Returns:
            A dictionary of HTTP headers.
        """
        # If not using an authenticator, you may also provide inline auth headers:
        # headers["Private-Token"] = self.config.get("auth_token")  # noqa: ERA001
        return {}

    def get_new_paginator(self) -> BaseAPIPaginator:
        """Create a new pagination helper instance.

        If the source API can make use of the `next_page_token_jsonpath`
        attribute, or it contains a `X-Next-Page` header in the response
        then you can remove this method.

        If you need custom pagination that uses page numbers, "next" links, or
        other approaches, please read the guide: https://sdk.meltano.com/en/v0.25.0/guides/pagination-classes.html.

        Returns:
            A pagination helper instance.
        """
        return super().get_new_paginator()

    @override
    def get_url_params(self, context, next_page_token):
        params = super().get_url_params(context, next_page_token)

        if start_date := self.get_starting_timestamp(context):
            params["startDateTime"] = start_date.strftime(r"%Y-%m-%d %H:%M:%S")

        return params

    @cached_property
    def _date_time_properties(self):
        properties: dict[str, dict] = self.schema["properties"]

        return {
            name
            for name, schema in properties.items()
            if schema.get("format") == "date-time"
        }

    @override
    def post_process(self, row, context=None):
        for name in self._date_time_properties:
            value = row.get(name)

            if not value:
                continue

            if isinstance(value, int):
                value = datetime.fromtimestamp(
                    value / 1000,  # assume timestamp in milliseconds
                    tz=timezone.utc,
                ).isoformat()

            row[name] = value

        return row
