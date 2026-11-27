"""Dedicated public ASGI boundary: it never imports/includes private NOC routes."""

from fastapi import FastAPI, Depends, Request, Response, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from app.core.database import get_db
from app.core.config import settings
from .models import Plan
from .contracts import SignupInput, LookupInput, PublicQuoteInput, PublicQuoteResponse
from . import public_service as s

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security(request, call_next):
    if (
        request.url.path.startswith("/public/")
        and request.headers.get("content-length", "").isdigit()
        and int(request.headers["content-length"]) > 32000
    ):
        return JSONResponse(
            status_code=413, content={"detail": "Application request too large"}
        )
    if request.method == "POST" and request.url.path.startswith("/public/"):
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > 32000:
                return JSONResponse(
                    status_code=413, content={"detail": "Application request too large"}
                )
        request._body = bytes(payload)
    try:
        response = await call_next(request)
    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Application service temporarily unavailable; retain your submission key and retry"
            },
        )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(RequestValidationError)
async def invalid(request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Invalid application. Check required fields and consent; do not submit identity/payment data."
        },
    )


def execute(db, fn):
    try:
        r = fn()
        db.commit()
        return r
    except PermissionError:
        db.rollback()
        raise HTTPException(
            403, "Security verification failed; reload and try again"
        ) from None
    except (ValueError, IntegrityError):
        db.rollback()
        raise HTTPException(
            409,
            "Submission could not be accepted; check the form or contact the office",
        ) from None
    except (SQLAlchemyError, OSError):
        db.rollback()
        raise HTTPException(
            503, "Application service unavailable; retain submission key and retry"
        ) from None


@app.get("/health")
def health():
    return {"status": "ok", "service": "signup-only gateway"}


@app.get("/public/signup/config")
def config(request: Request, response: Response, db=Depends(get_db)):
    def read():
        s.enabled()
        s.budget(db, request, "csrf")
        token = s.csrf_issue()
        response.set_cookie(
            "signup_csrf",
            token,
            httponly=True,
            secure=not (settings.signup_public_test_mode and s.providers.isolated()),
            samesite="strict",
            max_age=900,
            path="/",
        )
        return {
            "csrf_token": token,
            "site_key": (
                settings.signup_turnstile_site_key
                if settings.signup_turnstile_mode == "turnstile"
                else None
            ),
            "fixture_mode": settings.signup_public_test_mode and s.providers.isolated(),
            "plans": [
                s.public_plan(p)
                for p in db.scalars(
                    select(Plan)
                    .where(Plan.active.is_(True), Plan.public.is_(True))
                    .order_by(Plan.id)
                    .limit(100)
                )
            ],
        }

    return execute(db, read)


@app.post("/public/signup/applications")
def submit(body: SignupInput, request: Request, db=Depends(get_db)):
    def write():
        s.csrf_check(
            request.headers.get("origin"),
            request.cookies.get("signup_csrf"),
            request.headers.get("x-signup-csrf"),
        )
        s.budget(db, request, "submit")
        return s.submit(db, body)

    return execute(db, write)


@app.post("/public/signup/lookup")
def lookup(body: LookupInput, request: Request, db=Depends(get_db)):
    def read():
        s.csrf_check(
            request.headers.get("origin"),
            request.cookies.get("signup_csrf"),
            request.headers.get("x-signup-csrf"),
        )
        s.budget(db, request, "lookup")
        return s.lookup(db, body)

    return execute(db, read)

# Application/contact searches and receipt tokens must not enter access logs.
from app.signup.privacy import configure as redact_access_queries
redact_access_queries()


@app.post('/public/signup/quote')
def public_quote(body:PublicQuoteInput,request:Request,db=Depends(get_db)):
    return execute(db,lambda:quote_operation(db,request,body,False))


@app.post('/public/signup/quote-response')
def public_quote_response(body:PublicQuoteResponse,request:Request,db=Depends(get_db)):
    return execute(db,lambda:quote_operation(db,request,body,True))


def quote_operation(db,request,body,respond):
    from sqlalchemy import text
    from . import crypto
    s.csrf_check(request.headers.get('origin'),request.cookies.get('signup_csrf'),request.headers.get('x-signup-csrf'))
    s.budget(db,request,'lookup')
    if not s.providers.turnstile(body.turnstile_token,body.request_key):raise PermissionError('Security verification failed')
    token_hash=crypto.digest('quote_access',body.token)
    if respond:
        return db.scalar(text('SELECT public.noc_signup_quote_respond(:hash,:decision)'),{'hash':token_hash,'decision':body.decision})
    return db.scalar(text('SELECT public.noc_signup_quote_read(:hash)'),{'hash':token_hash})


@app.get('/public/signup/health')
def signup_readiness():
    s.enabled()
    return {'status':'ok','service':'signup-only gateway'}
