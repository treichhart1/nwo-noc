"""Bounded wire/decompressed JSON for approved HTTP clients; no endpoints or credentials."""

import json, time, zlib


class ResponseBudgetExceeded(ValueError):
    pass


def bounded_json(response, limit, *, deadline=None, clock=time.monotonic):
    """Bound the wire read AND decompressed JSON before parsing untrusted providers."""
    if deadline is not None and clock() >= deadline:
        raise TimeoutError("Provider response time budget exceeded")
    if response.is_stream_consumed:
        # Inline MockTransport responses can already contain decoded content.
        data = response.content
        if len(data) > limit:
            raise ResponseBudgetExceeded("Provider response budget exceeded")
    else:
        encoding = response.headers.get("content-encoding", "identity").lower().strip()
        if encoding not in {"identity", "", "gzip", "deflate"}:
            raise ValueError("Unsupported provider encoding")
        decoder = (
            zlib.decompressobj(31 if encoding == "gzip" else 15)
            if encoding in {"gzip", "deflate"}
            else None
        )
        data = bytearray()
        wire_bytes = 0
        deadline = deadline if deadline is not None else clock() + 6
        for chunk in response.iter_raw(chunk_size=8192):
            wire_bytes += len(chunk)
            if wire_bytes > limit:
                raise ResponseBudgetExceeded("Provider response budget exceeded")
            if clock() >= deadline:
                raise TimeoutError("Provider response time budget exceeded")
            try:
                decoded = (
                    decoder.decompress(chunk, limit - len(data) + 1)
                    if decoder
                    else chunk
                )
            except zlib.error:
                if encoding != "deflate" or wire_bytes != len(chunk):
                    raise
                # HTTP deployments use both zlib-wrapped and raw RFC1951 deflate.
                decoder = zlib.decompressobj(-15)
                decoded = decoder.decompress(chunk, limit - len(data) + 1)
            data.extend(decoded)
            if len(data) > limit or decoder and decoder.unconsumed_tail:
                raise ResponseBudgetExceeded("Provider response budget exceeded")
        if decoder and (not decoder.eof or decoder.unused_data):
            raise ValueError("Malformed provider encoding")
    return json.loads(data)
