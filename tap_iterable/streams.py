"""Stream type classes for tap-iterable."""

from __future__ import annotations

import csv
import decimal
import io
import itertools
import json
import math
import re
import tempfile
from datetime import timedelta
from importlib import resources
from pathlib import Path

import humps
from singer_sdk import typing as th
from singer_sdk.streams import Stream
from typing_extensions import override

from tap_iterable import BufferDeque
from tap_iterable.client import IterableStream
from tap_iterable.pagination import DateTimeIntervalPaginator, DateTimeIntervalTokenType

SCHEMAS_DIR = resources.files(__package__) / "schemas"


class ListsStream(IterableStream):
    """Define lists stream."""

    name = "lists"
    path = "/lists"
    records_jsonpath = "$.lists[*]"
    schema_filepath = SCHEMAS_DIR / "lists.json"
    primary_keys = ("id",)

    @override
    def get_child_context(self, record, context):
        return {"listId": record["id"]}


class ListUsersStream(IterableStream):
    """Define lists stream."""

    parent_stream_type = ListsStream
    name = "list_users"
    path = "/lists/getUsers"
    schema_filepath = SCHEMAS_DIR / "list_users.json"
    primary_keys = ("email", "listId")

    @override
    def get_url_params(self, context, next_page_token):
        params = super().get_url_params(context, next_page_token)
        params["listId"] = context["listId"]

        return params

    @override
    def parse_response(self, response):
        yield from ({"email": line} for line in response.iter_lines())

    @override
    def post_process(self, row, context=None):
        row["listId"] = context["listId"]
        return row


class CampaignsStream(IterableStream):
    """Define campaigns stream."""

    name = "campaigns"
    path = "/campaigns"
    records_jsonpath = "$.campaigns[*]"
    schema_filepath = SCHEMAS_DIR / "campaigns.json"
    primary_keys = ("id",)
    replication_key = "updatedAt"

    @override
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._campaign_ids_buffer = BufferDeque(maxlen=400)

    @override
    def parse_response(self, response):
        for record in super().parse_response(response):
            yield record

        # make sure we process the remaining buffer entries
        self._campaign_ids_buffer.finalize()
        yield record  # yield last record again to force child context generation

    @override
    def generate_child_contexts(self, record, context):
        self._campaign_ids_buffer.append(record["id"])

        with self._campaign_ids_buffer as buf:
            if buf.flush:
                yield {"campaign_ids": buf}


class ChannelsStream(IterableStream):
    """Define channels stream."""

    name = "channels"
    path = "/channels"
    records_jsonpath = "$.channels[*]"
    schema_filepath = SCHEMAS_DIR / "channels.json"
    primary_keys = ("id",)


class MessageTypesStream(IterableStream):
    """Define message types stream."""

    name = "message_types"
    path = "/messageTypes"
    records_jsonpath = "$.messageTypes[*]"
    schema_filepath = SCHEMAS_DIR / "message_types.json"
    primary_keys = ("id",)


class _TemplatesContextStream(Stream):
    """Define templates context stream."""

    name = "_templates_context"
    schema = th.ObjectType().to_dict()
    selected = False  # use for context generation only

    @override
    def get_records(self, context):
        template_types = ["Base", "Blast", "Triggered", "Workflow"]
        message_mediums = ["Email", "Push", "InApp", "SMS"]

        for tt, mm in itertools.product(template_types, message_mediums):
            yield {"templateType": tt, "messageMedium": mm}

    @override
    def get_child_context(self, record, context):
        return record


class TemplatesStream(IterableStream):
    """Define templates stream."""

    parent_stream_type = _TemplatesContextStream
    name = "templates"
    path = "/templates"
    records_jsonpath = "$.templates[*]"
    schema_filepath = SCHEMAS_DIR / "templates.json"
    primary_keys = ("templateId",)
    replication_key = "updatedAt"

    @override
    def get_url_params(self, context, next_page_token):
        params = super().get_url_params(context, next_page_token)
        params["templateType"] = context["templateType"]
        params["messageMedium"] = context["messageMedium"]

        if start_date := self.get_starting_timestamp(context):
            params["startDateTime"] = start_date.strftime(r"%Y-%m-%d %H:%M:%S")

        return params

    @override
    def get_child_context(self, record, context):
        if context["messageMedium"] == "Email":
            return {**context, "templateId": record["templateId"]}

        return None

    @override
    def _sync_children(self, child_context):
        if child_context is None:
            return

        for child_stream in self.child_streams:
            if child_stream.selected or child_stream.has_selected_descendents:
                child_stream.sync(context=child_context)


class EmailTemplatesStream(IterableStream):
    """Define email templates stream."""

    parent_stream_type = TemplatesStream
    name = "email_templates"
    path = "/templates/email/get"
    schema_filepath = SCHEMAS_DIR / "email_templates.json"
    primary_keys = ("templateId",)
    state_partitioning_keys = ()

    @override
    def get_url_params(self, context, next_page_token):
        params = super().get_url_params(context, next_page_token)
        params["templateId"] = context["templateId"]

        return params


class _MetadataStream(IterableStream):
    """Define metadata stream."""

    name = "_metadata"
    path = "/metadata"
    schema = th.ObjectType().to_dict()
    selected = False  # use for context generation only
    records_jsonpath = "$.results[*]"

    @override
    def get_child_context(self, record, context):
        return {"table": record["name"]}


class _MetadataTablesStream(IterableStream):
    """Define metadata tables stream."""

    parent_stream_type = _MetadataStream
    name = "_metadata_tables"
    path = "/metadata/{table}"
    schema = th.ObjectType().to_dict()
    selected = False  # use for context generation only
    records_jsonpath = "$.results[*]"

    @override
    def get_child_context(self, record, context):
        return {**context, "key": record["key"]}


class MetadataStream(IterableStream):
    """Define metadata stream."""

    parent_stream_type = _MetadataTablesStream
    name = "metadata"
    path = "/metadata/{table}/{key}"
    schema_filepath = SCHEMAS_DIR / "metadata.json"
    primary_keys = ("table", "key")


# https://api.iterable.com/api/docs#export_exportDataJson
class _ExportStream(IterableStream):
    """Define export stream."""

    path = "/export/data.json"
    replication_key = "createdAt"

    data_type_name: str

    @override
    @property
    def schema_filepath(self):
        return SCHEMAS_DIR / f"{self.name}.json"

    @override
    def get_new_paginator(self):
        return DateTimeIntervalPaginator(
            start=self.get_starting_timestamp(self.context),
            interval=timedelta(days=7),  # TODO: make this configurable
        )

    @override
    def get_url_params(self, context, next_page_token: DateTimeIntervalTokenType):
        start, end = next_page_token

        return {
            "dataTypeName": self.data_type_name,
            "startDateTime": start.strftime(r"%Y-%m-%d %H:%M:%S"),
            "endDateTime": end and end.strftime(r"%Y-%m-%d %H:%M:%S"),
        }

    @override
    def _request(self, prepared_request, context):
        response = self.requests_session.send(
            prepared_request,
            stream=True,  # streaming request
            timeout=self.timeout,
            allow_redirects=self.allow_redirects,
        )
        self._write_request_duration_log(
            endpoint=self.path,
            response=response,
            context=context,
            extra_tags={"url": prepared_request.path_url}
            if self._LOG_REQUEST_METRIC_URLS
            else None,
        )
        self.validate_response(response)

        return response

    @override
    def parse_response(self, response):
        with tempfile.TemporaryDirectory(prefix=f"{self.tap_name}-") as tmpdir:
            filepath = Path(tmpdir) / f"{self.name}.jsonl"

            with (
                response,  # ensure connection is eventually released
                filepath.open("wb") as f,
            ):
                self.logger.info("Writing file: %s", f.name)
                for chunk in response.iter_content(1024**2):  # 1 MB
                    f.write(chunk)

            filesize = filepath.stat().st_size / 1000**2  # convert to MB
            self.logger.info("Processing file: %s (%.1f MB)", filepath, filesize)

            with filepath.open("r") as f:
                yield from (json.loads(line, parse_float=decimal.Decimal) for line in f)

        self._finalize_state(self.stream_state)

    @override
    def post_process(self, row: dict, context=None):
        if self.primary_keys and (
            bad_keys := [k for k in self.primary_keys if row.get(k) is None]
        ):
            self.logger.warning(
                (
                    "Missing or no value for stream primary key properties %s in row, "
                    "skipping"
                ),
                bad_keys,
            )
            return None

        if transactional_data := row.get("transactionalData"):
            row["transactionalData"] = json.loads(transactional_data)

        return row


class EmailBounceStream(_ExportStream):
    """Define email bounce export stream."""

    name = "email_bounce"
    primary_keys = ("messageId", "createdAt", "email", "recipientState")

    data_type_name = "emailBounce"


class EmailClickStream(_ExportStream):
    """Define email click export stream."""

    name = "email_click"
    primary_keys = ("messageId", "createdAt", "email", "url", "ip", "userAgent")

    data_type_name = "emailClick"


class EmailComplaintStream(_ExportStream):
    """Define email complaint export stream."""

    name = "email_complaint"
    primary_keys = ("messageId", "createdAt", "email")

    data_type_name = "emailComplaint"


class EmailOpenStream(_ExportStream):
    """Define email open export stream."""

    name = "email_open"
    primary_keys = ("messageId", "createdAt", "email", "ip", "userAgent")

    data_type_name = "emailOpen"


class EmailSendStream(_ExportStream):
    """Define email send export stream."""

    name = "email_send"
    primary_keys = ("messageId", "createdAt", "email")

    data_type_name = "emailSend"


class EmailSendSkipStream(_ExportStream):
    """Define email send skip export stream."""

    name = "email_send_skip"
    primary_keys = ("messageId", "createdAt", "email")

    data_type_name = "emailSendSkip"


class EmailSubscribeStream(_ExportStream):
    """Define email subscribe export stream."""

    name = "email_subscribe"
    primary_keys = ("createdAt", "email", "signupSource", "emailListIds")

    data_type_name = "emailSubscribe"


class EmailUnsubscribeStream(_ExportStream):
    """Define email unsubscribe export stream."""

    name = "email_unsubscribe"
    primary_keys = ("createdAt", "email", "unsubSource", "emailListIds", "channelIds")

    data_type_name = "emailUnsubscribe"


class SMSBounceStream(_ExportStream):
    """Define SMS bounce export stream."""

    name = "sms_bounce"
    primary_keys = ("messageId", "createdAt", "email")

    data_type_name = "smsBounce"


class SMSClickStream(_ExportStream):
    """Define SMS click export stream."""

    name = "sms_click"
    primary_keys = ("messageId", "createdAt", "email", "clickedUrl")

    data_type_name = "smsClick"


class SMSReceivedStream(_ExportStream):
    """Define SMS received export stream."""

    name = "sms_received"
    primary_keys = ("createdAt", "email", "smsMessage")

    data_type_name = "smsReceived"


class SMSSendStream(_ExportStream):
    """Define SMS send export stream."""

    name = "sms_send"
    primary_keys = ("messageId", "createdAt", "email")

    data_type_name = "smsSend"


class SMSSendSkipStream(_ExportStream):
    """Define SMS send skip export stream."""

    name = "sms_send_skip"
    primary_keys = ("messageId", "createdAt", "email")

    data_type_name = "smsSendSkip"


class WebPushClickStream(_ExportStream):
    """Define web push click export stream."""

    name = "web_push_click"
    primary_keys = ("messageId",)

    data_type_name = "webPushClick"


class WebPushSendStream(_ExportStream):
    """Define web push send export stream."""

    name = "web_push_send"
    primary_keys = ("messageId",)

    data_type_name = "webPushSend"


class WebPushSendSkipStream(_ExportStream):
    """Define web push send skip export stream."""

    name = "web_push_send_skip"
    primary_keys = ("messageId",)

    data_type_name = "webPushSendSkip"


class WhatsAppBounceStream(_ExportStream):
    """Define WhatsApp bounce export stream."""

    name = "whatsapp_bounce"
    primary_keys = ("messageId", "createdAt", "email")

    data_type_name = "whatsAppBounce"


class WhatsAppClickStream(_ExportStream):
    """Define WhatsApp click export stream."""

    name = "whatsapp_click"
    primary_keys = ("messageId", "createdAt", "email")

    data_type_name = "whatsAppClick"


class WhatsAppReceivedStream(_ExportStream):
    """Define WhatsApp received export stream."""

    name = "whatsapp_received"
    primary_keys = ("replyToMessageId", "createdAt", "email")

    data_type_name = "whatsAppReceived"


class WhatsAppSeenStream(_ExportStream):
    """Define WhatsApp seen export stream."""

    name = "whatsapp_seen"
    primary_keys = ("seenMessageId", "createdAt", "email")

    data_type_name = "whatsAppSeen"


class WhatsAppSendStream(_ExportStream):
    """Define WhatsApp send export stream."""

    name = "whatsapp_send"
    primary_keys = ("messageId", "createdAt", "email")

    data_type_name = "whatsAppSend"


class WhatsAppSendSkipStream(_ExportStream):
    """Define WhatsApp send skip export stream."""

    name = "whatsapp_send_skip"
    primary_keys = ("messageId", "createdAt", "email")

    data_type_name = "whatsAppSendSkip"


class UsersStream(_ExportStream):
    """Define users export stream."""

    name = "users"
    primary_keys = ("email",)
    replication_key = "profileUpdatedAt"

    data_type_name = "user"

    @override
    def post_process(self, row, context=None):
        row: dict[str] = super().post_process(row, context)

        # loosely following convention from https://api.iterable.com/api/docs#users_getUserById,
        # use a `dataFields` schema property as to encapsulate all project-specific user
        # fields in order to avoid overhead/complexity of dynamic discovery

        data_fields = {
            f: row.pop(f) for f in row.copy() if f not in self.schema["properties"]
        }

        return {
            **row,
            "dataFields": data_fields,
        }


class CustomEventStream(_ExportStream):
    """Define custom event export stream."""

    name = "custom_event"
    primary_keys = ("createdAt", "email")

    data_type_name = "customEvent"


class ExperimentMetrics(IterableStream):
    """Define experiment metrics stream."""

    parent_stream_type = CampaignsStream
    state_partitioning_keys = ()
    name = "experiment_metrics"
    path = "/experiments/metrics"

    # https://support.iterable.com/hc/en-us/articles/213805923-Metric-Definitions
    schema = th.PropertiesList(
        th.Property("campaignId", th.IntegerType),
        th.Property("experimentId", th.IntegerType),
        th.Property("templateId", th.IntegerType),
        th.Property("name", th.StringType),
        th.Property("type", th.StringType),  # Control, Winner, -
        th.Property("createdBy", th.EmailType),
        th.Property("creationDate", th.DateTimeType),
        th.Property("lastModified", th.DateTimeType),
        th.Property("subject", th.StringType),
        th.Property("improvement", th.NumberType),
        th.Property("confidence", th.NumberType),
        th.Property("totalEmailSends", th.IntegerType),
        th.Property("uniqueEmailSends", th.IntegerType),
        th.Property("emailDeliveryRate", th.NumberType),
        th.Property("totalEmailsDelivered", th.IntegerType),
        th.Property("uniqueEmailsDelivered", th.IntegerType),
        th.Property("totalEmailOpens", th.IntegerType),
        th.Property("totalEmailOpensFiltered", th.IntegerType),
        th.Property("uniqueEmailOpens", th.IntegerType),
        th.Property("uniqueEmailOpensFiltered", th.IntegerType),
        th.Property("uniqueEmailOpensOrClicks", th.IntegerType),
        th.Property("emailOpenRate", th.NumberType),
        th.Property("uniqueEmailOpenRate", th.NumberType),
        th.Property("totalEmailsClicked", th.IntegerType),
        th.Property("totalEmailsClickedFiltered", th.IntegerType),
        th.Property("uniqueEmailClicks", th.IntegerType),
        th.Property("uniqueEmailClicksFiltered", th.IntegerType),
        th.Property("clicksOpens", th.NumberType),
        th.Property("emailClickRate", th.NumberType),
        th.Property("uniqueEmailClickRate", th.NumberType),
        th.Property("totalComplaints", th.IntegerType),
        th.Property("complaintRate", th.NumberType),
        th.Property("totalEmailsBounced", th.IntegerType),
        th.Property("uniqueEmailsBounced", th.IntegerType),
        th.Property("emailBounceRate", th.NumberType),
        th.Property("totalEmailHoldout", th.IntegerType),
        th.Property("totalEmailSendSkips", th.IntegerType),
        th.Property("totalUnsubscribes", th.IntegerType),
        th.Property("uniqueUnsubscribes", th.IntegerType),
        th.Property("emailUnsubscribeRate", th.NumberType),
        th.Property("revenue", th.NumberType),
        th.Property("totalPurchases", th.IntegerType),
        th.Property("uniquePurchases", th.IntegerType),
        th.Property("averageOrderValue", th.NumberType),
        th.Property("purchasesPerMileEmail", th.NumberType),
        th.Property("revenuePerMileEmail", th.NumberType),
        th.Property("totalCustomConversions", th.IntegerType),
        th.Property("uniqueCustomConversions", th.IntegerType),
        th.Property("averageCustomConversionValue", th.NumberType),
        th.Property("conversionsEmailHoldOuts", th.NumberType),
        th.Property("conversionsUniqueEmailsDelivered", th.NumberType),
        th.Property("sumOfCustomConversions", th.IntegerType),
    ).to_dict()

    primary_keys = ("experimentId", "templateId")
    replication_key = "lastModified"

    @override
    def get_url_params(self, context, next_page_token):
        params = super().get_url_params(context, next_page_token)
        params["campaignId"] = context["campaign_ids"]

        return params

    @override
    def parse_response(self, response):
        with io.StringIO(response.text) as f:
            for row in csv.DictReader(f):
                # remove extra values
                if None in row:
                    del row[None]

                yield row

    @override
    def post_process(self, row, context=None):
        row = super().post_process(row, context=context)

        properties: dict = self.schema["properties"]

        for k in list(row.keys()):
            new_key = k.lower()
            new_key = new_key.replace("/ m", "per mile")
            new_key = re.sub(r"\s", "_", new_key)
            new_key = re.sub(r"[\W]", "", new_key)
            new_key = humps.camelize(new_key)

            value = row.pop(k)

            if value == "":
                row[new_key] = value = None

            if value is None or not (property_schema := properties.get(new_key)):
                continue

            numeric_typecasts: dict[th._NumericType] = {
                th.IntegerType: int,
                th.NumberType: float,
            }

            numeric_typecast = next(
                (
                    numeric_typecasts[nt]
                    for nt in numeric_typecasts
                    if nt.__type_name__ in property_schema["type"]
                ),
                None,
            )  # get the first matching typecast if one exists

            if numeric_typecast:
                try:
                    d = decimal.Decimal(value)
                except decimal.DecimalException:
                    d = decimal.Decimal(math.nan)
                    self.logger.debug("Handling invalid decimal '%s' as %s", value, d)

                if d.is_nan() or d.is_infinite():
                    value = None
                    self.logger.debug(
                        (
                            "%s is not supported as a numeric value in JSON, handling "
                            "as %s"
                        ),
                        d,
                        value,
                    )
                else:
                    value = numeric_typecast(d)

            row[new_key] = value

        return row
