"""Optional isolated Slack interaction receiver. No private application routes."""

import asyncio
import hashlib
import json
import re
from datetime import timedelta
from functools import lru_cache
from urllib.parse import parse_qs
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import create_engine, select, text, delete, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from app.core.config import settings
from app.notifications.slack_models import (
    SlackIntegration,
    SlackInteraction,
    SlackInteractionBudget,
)
from app.notifications.models import NotificationDelivery
from app.notifications.secrets import unseal
from app.notifications.service import clock, digest
from app.notifications.slack_transport import signature, STAMP

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)


@lru_cache(maxsize=1)
def session_factory():
    # Gateway must have its own restricted DB role and no notification vault/JWT key.
    engine = create_engine(
        settings.database_url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=0.5,
        connect_args={
            "connect_timeout": 1,
            "options": "-c statement_timeout=1000 -c lock_timeout=500",
        },
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def record(db, body, headers):
    r = db.get(SlackIntegration, 1)
    if (
        not r
        or not r.live_authorized
        or not r.config.get("enabled")
        or not r.config.get("interactions_enabled")
        or r.config.get("mode") != "bot"
    ):
        raise ValueError()
    secret = unseal(settings.slack_callback_key_file, r.signing_ciphertext)
    signature(
        body,
        headers.get("x-slack-request-timestamp"),
        headers.get("x-slack-signature"),
        secret,
        clock().timestamp(),
    )
    form = parse_qs(body.decode("utf-8"), strict_parsing=True, max_num_fields=1)
    if set(form) != {"payload"} or len(form["payload"]) != 1:
        raise ValueError()
    event = json.loads(form["payload"][0])
    if (
        not isinstance(event, dict)
        or event.get("type") != "block_actions"
        or event.get("api_app_id") != r.config["app_id"]
        or event.get("team", {}).get("id") != r.config["workspace_id"]
    ):
        raise ValueError()
    actions = event.get("actions", [])
    if not isinstance(actions, list) or len(actions) != 1:
        raise ValueError()
    action = actions[0]
    if (
        action.get("action_id") not in {"noc_ack", "noc_review", "noc_open"}
        or not re.fullmatch(r"[0-9]{1,10}", action.get("value", ""))
        or not re.fullmatch(STAMP, action.get("action_ts", ""))
    ):
        raise ValueError()
    user = event.get("user", {}).get("id")
    channel = event.get("channel", {}).get("id")
    ts = event.get("container", {}).get("message_ts")
    if (
        channel not in r.config["approved_channels"]
        or channel not in r.config["test_channels"]
        or not isinstance(ts, str)
        or not re.fullmatch(STAMP, ts)
    ):
        raise ValueError()
    d = db.execute(
        select(
            NotificationDelivery.id,
            NotificationDelivery.channel,
            NotificationDelivery.recipient_key,
            NotificationDelivery.provider_receipt,
            NotificationDelivery.slack_authorization,
        ).where(NotificationDelivery.id == int(action["value"]))
    ).first()
    if (
        not d
        or d.channel != "slack"
        or d.recipient_key != channel
        or d.provider_receipt != ts
        or not d.slack_authorization
        or d.slack_authorization.get("revision") != r.revision
    ):
        raise ValueError()
    if not db.scalar(
        text("SELECT public.noc_slack_interaction_allowed(:w,:u,:c,:d)"),
        {"w": r.config["workspace_id"], "u": user, "c": channel, "d": d.id},
    ):
        raise ValueError()
    key = digest(
        [
            r.config["workspace_id"],
            user,
            channel,
            ts,
            action["action_id"],
            action["action_ts"],
        ]
    )
    # Durable unique identity makes retries harmless even if the HTTP ACK is lost.
    prior = db.get(SlackInteraction, key)
    if prior:
        return {"duplicate": True}
    minute = clock().replace(second=0, microsecond=0)
    count = db.scalar(
        insert(SlackInteractionBudget)
        .values(minute=minute, count=1)
        .on_conflict_do_update(
            index_elements=[SlackInteractionBudget.minute],
            set_={"count": func.least(SlackInteractionBudget.count + 1, 121)},
        )
        .returning(SlackInteractionBudget.count)
    )
    if count > 120:
        raise ValueError()
    db.execute(
        delete(SlackInteractionBudget).where(
            SlackInteractionBudget.minute < minute - timedelta(minutes=10)
        )
    )
    # Concurrent receivers converge on the unique key without replayed actions.
    db.execute(
        insert(SlackInteraction)
        .values(
            key=key,
            delivery_id=d.id,
            slack_user_id=user,
            workspace=r.config["workspace_id"],
            channel=channel,
            message_ts=ts,
            action=action["action_id"],
            body_hash=hashlib.sha256(body).hexdigest(),
            state="pending",
            received_at=clock(),
        )
        .on_conflict_do_nothing(index_elements=[SlackInteraction.key])
    )
    r.last_callback_at = clock()
    return {"duplicate": False}


@app.middleware("http")
async def secure(request, call_next):
    try:
        result = await call_next(request)
    except Exception:
        result = JSONResponse({"detail": "Slack callback unavailable"}, status_code=503)
    result.headers["Cache-Control"] = "no-store"
    result.headers["X-Content-Type-Options"] = "nosniff"
    return result


@app.post("/callbacks/slack/interactions")
async def receive(request: Request):
    if not settings.slack_interaction_gateway_enabled:
        return JSONResponse({"detail": "Slack interactions disabled"}, status_code=503)
    if (
        request.query_params
        or request.headers.get("content-type", "").split(";")[0].strip()
        != "application/x-www-form-urlencoded"
    ):
        return Response(status_code=400)

    async def read():
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > 16384:
                raise ValueError()
        return bytes(body)

    try:
        body = await asyncio.wait_for(read(), 0.5)

        def process():
            with session_factory()() as db:
                result = record(db, body, request.headers)
                db.commit()
                return result

        await asyncio.wait_for(run_in_threadpool(process), 2)
        return Response(status_code=200)
    except (ValueError, KeyError, TypeError, UnicodeError, SQLAlchemyError):
        return Response(status_code=403)
    except (TimeoutError, OSError):
        return Response(status_code=503)


@app.get("/health")
def health():
    return {"status": "ok", "service": "slack-interactions-only"}
