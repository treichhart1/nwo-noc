"""Uvicorn access log query redaction; edge/APM logs need matching operator policy."""

import logging


class RedactQuery(logging.Filter):
    def filter(self, record):
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def configure():
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactQuery) for f in logger.filters):
        logger.addFilter(RedactQuery())
