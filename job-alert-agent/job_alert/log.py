"""Structured logging.

`log.info("fetched", company="stripe", jobs=12)` renders as
`2026-09-26T21:00:00 INFO sources.greenhouse fetched company=stripe jobs=12`
or, with LOG_FORMAT=json, as one JSON object per line.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


class _Formatter(logging.Formatter):
    def __init__(self, as_json: bool):
        super().__init__()
        self.as_json = as_json

    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {}) or {}
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
        name = record.name.removeprefix("job_alert.")
        if self.as_json:
            payload = {"ts": ts, "level": record.levelname, "logger": name, "msg": record.getMessage(), **fields}
            if record.exc_info:
                payload["exc"] = self.formatException(record.exc_info)
            return json.dumps(payload, default=str)
        kv = " ".join(f"{k}={_fmt(v)}" for k, v in fields.items())
        line = f"{ts} {record.levelname:<7} {name} {record.getMessage()}" + (f" {kv}" if kv else "")
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _fmt(v: object) -> str:
    s = str(v)
    return f'"{s}"' if " " in s else s


class StructuredLogger:
    def __init__(self, name: str):
        self._log = logging.getLogger(name)

    def _emit(self, level: int, msg: str, exc_info: bool = False, **fields: object) -> None:
        if self._log.isEnabledFor(level):
            self._log.log(level, msg, exc_info=exc_info, extra={"fields": fields})

    def debug(self, msg: str, **f: object) -> None:
        self._emit(logging.DEBUG, msg, **f)

    def info(self, msg: str, **f: object) -> None:
        self._emit(logging.INFO, msg, **f)

    def warning(self, msg: str, **f: object) -> None:
        self._emit(logging.WARNING, msg, **f)

    def error(self, msg: str, exc_info: bool = False, **f: object) -> None:
        self._emit(logging.ERROR, msg, exc_info=exc_info, **f)


def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(name)


def setup_logging(verbose: bool = False) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter(as_json=os.environ.get("LOG_FORMAT") == "json"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
