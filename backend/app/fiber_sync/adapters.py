"""Mock export adapter only. This contract is NOT an assertion about Fiber Designer APIs."""

from .contracts import Envelope


class MockAdapter:
    def __init__(self, response):
        self.response = response

    def fetch_approved(self, *, instance_id, project_id, after_cursor):
        envelope = Envelope.model_validate(self.response)
        if envelope.instance_id != instance_id or envelope.project_id != project_id:
            raise ValueError("Export namespace mismatch")
        # A repeated last envelope is returned for durable deduplication, not silently transformed.
        return envelope


class UnavailableAdapter:
    def fetch_approved(self, **kwargs):
        raise ValueError(
            "Live Fiber Designer adapter unavailable; verify export compatibility first"
        )
