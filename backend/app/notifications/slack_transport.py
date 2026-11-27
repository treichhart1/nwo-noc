"""Slack native HTTP contracts; no implicit retries or exactly-once guarantee."""

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from urllib.parse import urlsplit
import httpx
from app.core.config import settings

CHANNEL = r"[CG][A-Z0-9]{8,31}"
STAMP = r"[0-9]{10,16}\.[0-9]{6}"
SAFE_ERRORS = {
    "invalid_auth",
    "not_authed",
    "token_revoked",
    "account_inactive",
    "missing_scope",
    "channel_not_found",
    "not_in_channel",
    "is_archived",
    "invalid_blocks",
    "invalid_arguments",
    "no_permission",
    "restricted_action",
    "posting_to_general_channel_denied",
    "team_disabled",
    "action_prohibited",
    "invalid_payload",
    "invalid_token",
    "channel_is_archived",
    "too_many_attachments",
    "no_text",
    "no_service",
    "no_service_id",
    "no_team",
}


class SlackFailure(RuntimeError):
    def __init__(self, code="unknown_acceptance", *, rejected=False, retry_after=60):
        self.code = (
            code
            if code
            in SAFE_ERRORS
            | {
                "rate_limited",
                "timeout",
                "invalid_response",
                "unknown_acceptance",
                "not_found",
                "identity_mismatch",
                "lookup_unavailable",
            }
            else "unknown_acceptance"
        )
        self.rejected = bool(rejected)
        self.retry_after = min(3600, max(1, int(retry_after)))
        super().__init__(self.code)


@dataclass(frozen=True)
class SlackReceipt:
    channel: str
    timestamp: str | None
    raw_status: str


def webhook_url(value, team):
    u = urlsplit(value)
    if (
        u.scheme != "https"
        or u.hostname != "hooks.slack.com"
        or u.port is not None
        or u.username
        or u.password
        or u.query
        or u.fragment
        or not re.fullmatch(
            r"/services/" + re.escape(team) + r"/B[A-Z0-9]{8,31}/[A-Za-z0-9]{16,128}",
            u.path,
        )
    ):
        raise ValueError("Approved Slack webhook origin and workspace required")
    return value


def signature(body, timestamp, provided, secret, now):
    if (
        not re.fullmatch(r"[0-9]{10,16}", timestamp or "")
        or abs(now - int(timestamp)) > 300
        or len(body) > 16384
    ):
        raise ValueError("Invalid Slack signature")
    expected = (
        "v0="
        + hmac.new(
            secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
        ).hexdigest()
    )
    if not isinstance(provided, str) or not hmac.compare_digest(expected, provided):
        raise ValueError("Invalid Slack signature")


class NativeSlack:
    def __init__(self, config, credentials, *, transport=None, authorized=False):
        if transport is not None and not isinstance(transport, httpx.MockTransport):
            raise ValueError("Only an explicit MockTransport may be injected")
        if transport is None and (
            not authorized
            or not settings.slack_native_transport_enabled
            or not settings.slack_egress_approved
            or settings.offline_mode
        ):
            raise ValueError("Native Slack host authorization required")
        self.config, self._credentials, self._transport = config, credentials, transport

    def request(self, method, path, *, body=None, params=None, webhook=None):
        allowed = {
            "chat.postMessage",
            "auth.test",
            "conversations.history",
            "conversations.replies",
        }
        if webhook is None and path not in allowed:
            raise ValueError("Slack API method not approved")
        url = (
            webhook_url(webhook, self.config["workspace_id"])
            if webhook
            else "https://slack.com/api/" + path
        )
        headers = (
            {}
            if webhook
            else {"Authorization": "Bearer " + self._credentials["bot_token"]}
        )
        try:
            with httpx.Client(
                transport=self._transport,
                verify=True,
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(5, connect=3),
            ) as c:
                with c.stream(
                    method, url, headers=headers, json=body, params=params
                ) as r:
                    raw = bytearray()
                    for part in r.iter_bytes():
                        raw.extend(part)
                        if len(raw) > 65536:
                            raise SlackFailure("invalid_response")
                    if r.status_code == 429:
                        retry = r.headers.get("Retry-After", "60")
                        raise SlackFailure(
                            "rate_limited",
                            rejected=True,
                            retry_after=int(retry) if retry.isdigit() else 60,
                        )
                    if webhook:
                        if r.status_code == 200 and bytes(raw).strip() == b"ok":
                            return {"ok": True}
                        code = bytes(raw).decode("ascii", errors="ignore").strip()
                        raise SlackFailure(
                            code,
                            rejected=r.status_code in {400, 403, 404, 410}
                            and code in SAFE_ERRORS,
                        )
                    if r.status_code in {401, 403}:
                        raise SlackFailure("invalid_auth", rejected=True)
                    if r.status_code != 200:
                        raise SlackFailure()
                    try:
                        value = json.loads(raw)
                    except (ValueError, UnicodeError):
                        raise SlackFailure("invalid_response") from None
                    if not isinstance(value, dict):
                        raise SlackFailure("invalid_response")
                    if value.get("ok") is not True:
                        code = value.get("error")
                        if code == "ratelimited":
                            code = "rate_limited"
                        raise SlackFailure(
                            code,
                            rejected=code in SAFE_ERRORS or code == "rate_limited",
                            retry_after=60,
                        )
                    return value
        except SlackFailure:
            raise
        except httpx.TimeoutException:
            raise SlackFailure("timeout") from None
        except (httpx.HTTPError, ValueError, TypeError):
            raise SlackFailure() from None

    def identity(self):
        value = self.request("POST", "auth.test", body={})
        if (
            value.get("team_id") != self.config["workspace_id"]
            or value.get("user_id") != self.config["bot_user_id"]
        ):
            raise SlackFailure("identity_mismatch", rejected=True)
        return value

    def submit(self, *, key, destination, payload):
        if (
            destination not in self.config["approved_channels"]
            or destination not in self.config["test_channels"]
        ):
            raise ValueError("Channel is not independently approved")
        # No Idempotency-Key/client_msg_id guarantee is assumed for either method.
        if self.config["mode"] == "webhook":
            url = self._credentials["webhooks"].get(destination)
            if not url:
                raise ValueError("No channel-bound webhook configured")
            self.request("POST", "webhook", body=payload, webhook=url)
            return SlackReceipt(destination, None, "webhook_ok_no_timestamp")
        self.identity()  # workspace ownership, not a message submission
        value = self.request(
            "POST",
            "chat.postMessage",
            body=payload
            | {
                "channel": destination,
                "parse": "none",
                "mrkdwn": False,
                "reply_broadcast": False,
            },
        )
        ts = value.get("ts")
        if (
            value.get("channel") != destination
            or not isinstance(ts, str)
            or not re.fullmatch(STAMP, ts)
        ):
            raise SlackFailure("invalid_response")
        msg = value.get("message", {})
        if (
            msg.get("user") != self.config["bot_user_id"]
            or msg.get("app_id") != self.config["app_id"]
        ):
            raise SlackFailure("invalid_response")
        return SlackReceipt(destination, ts, "chat_postMessage_ok")

    def send(self, *, key, destination, payload):
        return self.submit(key=key, destination=destination, payload=payload)

    def reconcile(self, *, channel, timestamp, thread_ts=None, text):
        if (
            not self.config.get("history_enabled")
            or channel not in self.config["approved_channels"]
            or channel not in self.config["test_channels"]
            or not re.fullmatch(STAMP, timestamp)
        ):
            raise SlackFailure("lookup_unavailable")
        self.identity()
        path = "conversations.replies" if thread_ts else "conversations.history"
        params = {
            "channel": channel,
            "oldest": timestamp,
            "latest": timestamp,
            "inclusive": "true",
            "limit": 1,
        }
        if thread_ts:
            params["ts"] = thread_ts
        value = self.request("GET", path, params=params)
        messages = value.get("messages", [])
        if not isinstance(messages, list) or len(messages) != 1:
            raise SlackFailure("not_found")
        m = messages[0]
        if (
            m.get("ts") != timestamp
            or m.get("user") != self.config["bot_user_id"]
            or m.get("app_id") != self.config["app_id"]
            or m.get("text") != text
            or (thread_ts and m.get("thread_ts") != thread_ts)
        ):
            raise SlackFailure("identity_mismatch")
        return SlackReceipt(channel, timestamp, "history_verified")
