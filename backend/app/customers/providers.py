"""Bounded fixture adapters. No native Powercode path/table is presumed supported.

Live construction is deliberately unavailable pending approved schema discovery.
Secrets are not part of payloads, exception text, dataclass repr, or NOC storage.
"""

import json, time
import httpx
from pydantic import ValidationError, BaseModel, Field, ConfigDict
from app.customers.contracts import ExportBatch
from app.services.integration_interlock import disabled


class ProviderFailure(RuntimeError):
    CODES = {
        "disabled",
        "schema_mismatch",
        "authentication_failed",
        "timeout",
        "unavailable",
        "rate_limited",
        "incomplete_snapshot",
        "conflicting_duplicate",
        "cursor_conflict",
        "stale_snapshot",
        "invalid_reference",
        "overlap_skipped",
    }

    def __init__(self, code):
        self.code = code if code in self.CODES else "unavailable"
        super().__init__(self.code)


def parse(value):
    try:
        return ExportBatch.model_validate(value)
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise ProviderFailure("schema_mismatch") from None


class SourceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retries: int = Field(2, ge=0, le=2, strict=True)
    timeout_seconds: float = Field(5, ge=1, le=10, allow_inf_nan=False)
    connect_seconds: float = Field(3, ge=1, le=3, allow_inf_nan=False)
    backoff_seconds: float = Field(0.25, ge=0.2, le=1, allow_inf_nan=False)
    max_bytes: int = Field(4_000_000, ge=4096, le=4_000_000, strict=True)


class FixtureAPI:
    """Tests a NOC export envelope over HTTPS GET; endpoint is synthetic only."""

    def __init__(self, transport, *, sleep=time.sleep, limits=None):
        if not isinstance(transport, httpx.MockTransport):
            raise ProviderFailure("disabled")
        self.transport, self.sleep = transport, sleep
        self.limits = SourceLimits.model_validate(limits or {})

    def fetch(self, *, cursor=None):
        # Always loopback-free mock transport: this class cannot open a socket.
        with httpx.Client(
            transport=self.transport,
            verify=True,
            follow_redirects=False,
            timeout=httpx.Timeout(
                self.limits.timeout_seconds, connect=self.limits.connect_seconds
            ),
            trust_env=False,
        ) as client:
            for attempt in range(self.limits.retries + 1):
                if disabled():
                    raise ProviderFailure("disabled")
                try:
                    with client.stream(
                        "GET",
                        "https://powercode-fixture.invalid/noc-export-v1",
                        headers={"X-NOC-Export-Cursor": cursor} if cursor else {},
                    ) as r:
                        if r.status_code in (401, 403):
                            raise ProviderFailure("authentication_failed")
                        if r.status_code == 429 or r.status_code >= 500:
                            if attempt == self.limits.retries:
                                raise ProviderFailure(
                                    "rate_limited"
                                    if r.status_code == 429
                                    else "unavailable"
                                )
                            self.sleep(self.limits.backoff_seconds * 2**attempt)
                            continue
                        if r.status_code != 200:
                            raise ProviderFailure("schema_mismatch")
                        data = bytearray()
                        for chunk in r.iter_bytes():
                            data.extend(chunk)
                            if len(data) > self.limits.max_bytes:
                                raise ProviderFailure("schema_mismatch")
                        return parse(json.loads(data))
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt == self.limits.retries:
                        raise ProviderFailure(
                            "timeout"
                            if isinstance(exc, httpx.TimeoutException)
                            else "unavailable"
                        ) from None
                    self.sleep(self.limits.backoff_seconds * 2**attempt)
                except (ValueError, UnicodeError, RecursionError):
                    raise ProviderFailure("schema_mismatch") from None
        raise ProviderFailure("unavailable")


class FixturePostgres:
    """An already-open LOCAL disposable PG connection to an explicit fixture view.

    Expected view: noc95_export.customer_batch(payload jsonb), exactly one row.
    This view is a NOC test contract, not an asserted Powercode table. No DSN,
    connection factory or arbitrary SQL is accepted. Production schema mapping
    and source account permissions remain unverified and cannot activate here.
    """

    def __init__(self, connection):
        if (
            connection.info.host not in ("127.0.0.1", "localhost")
            or connection.info.dbname != "nwo_noc"
            or not connection.autocommit
        ):
            raise ProviderFailure("disabled")
        self.connection = connection

    def fetch(self, *, cursor=None):
        if disabled():
            raise ProviderFailure("disabled")
        try:
            with self.connection.transaction():
                self.connection.execute("SET TRANSACTION READ ONLY")
                self.connection.execute("SET LOCAL statement_timeout='3000ms'")
                self.connection.execute("SET LOCAL lock_timeout='1000ms'")
                columns = self.connection.execute(
                    "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='noc95_export' AND table_name='customer_batch'"
                ).fetchall()
                if columns != [("payload", "jsonb")]:
                    raise ProviderFailure("schema_mismatch")
                rows = self.connection.execute(
                    "SELECT payload FROM noc95_export.customer_batch LIMIT 2"
                ).fetchall()
                if len(rows) != 1 or len(json.dumps(rows[0][0]).encode()) > 4_000_000:
                    raise ProviderFailure("schema_mismatch")
                return parse(rows[0][0])
        except ProviderFailure:
            raise
        except Exception:
            # Never stringify database errors: may contain SQL/credentials/PII.
            raise ProviderFailure("unavailable") from None
