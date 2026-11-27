"""Synthetic-only provisioning transport. No native operation is implemented."""

import httpx
from app.core.http_json import bounded_json
from app.customers.powercode_contracts import ProvisionResult, NATIVE_BLOCKER


class NativePowercode:
    def __init__(self, *args, **kwargs):
        raise ValueError(NATIVE_BLOCKER)


class FixtureProvisioner:
    """NOC-owned .invalid contract; cannot use sockets or production credentials.

    No automatic write retry. Timeouts, unexpected status or malformed success
    are ambiguous, including failed reads after a provider may accept a write.
    """

    def __init__(self, transport):
        if not isinstance(transport, httpx.MockTransport):
            raise ValueError("Mock transport required; native provisioning unavailable")
        self.transport = transport

    def submit(self, operation, payload, operation_key):
        if operation not in ("account", "service"):
            raise ValueError(
                "Activation, billing, payment and cancellation operations unavailable"
            )
        try:
            with httpx.Client(
                transport=self.transport,
                verify=True,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(5, connect=3),
            ) as client:
                with client.stream(
                    "POST",
                    "https://powercode-fixture.invalid/noc-provision-v1/" + operation,
                    json=payload,
                    headers={"X-NOC-Operation": operation_key},
                ) as r:
                    if r.status_code != 200:
                        return ProvisionResult(outcome="ambiguous")
                    return ProvisionResult.model_validate(bounded_json(r, 32768))
        except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
            return ProvisionResult(outcome="ambiguous")
