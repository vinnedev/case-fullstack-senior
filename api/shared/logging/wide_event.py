import json
import random
import sys
import time
import uuid
from contextvars import ContextVar

from shared.config.settings import get_settings

_current_event: ContextVar["WideEvent | None"] = ContextVar("wide_event", default=None)

SLOW_THRESHOLD_MS = get_settings().log_slow_threshold_ms
SUCCESS_SAMPLE_RATE = get_settings().log_success_sample_rate


class WideEvent:
    def __init__(self, service: str, name: str, **fields):
        self._start = time.monotonic()
        self.fields: dict = {
            "event_id": str(uuid.uuid4()),
            "service": service,
            "event": name,
            **fields,
        }
        self._error = False

    def add(self, **fields):
        self.fields.update(fields)
        return self

    def error(self, exc: BaseException | None = None, **fields):
        self._error = True
        if exc is not None:
            self.fields.update({"error_type": type(exc).__name__, "error_message": str(exc)})
        self.fields.update(fields)
        return self

    def _should_emit(self, duration_ms: float) -> bool:
        if self._error or duration_ms >= SLOW_THRESHOLD_MS:
            return True
        return random.random() < SUCCESS_SAMPLE_RATE

    def emit(self):
        duration_ms = round((time.monotonic() - self._start) * 1000, 2)
        if not self._should_emit(duration_ms):
            return
        self.fields.update({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "duration_ms": duration_ms,
            "level": "error" if self._error else "info",
        })
        print(json.dumps(self.fields, default=str), file=sys.stdout, flush=True)


def start_event(service: str, name: str, **fields) -> WideEvent:
    event = WideEvent(service, name, **fields)
    _current_event.set(event)
    return event


def current_event() -> WideEvent:
    event = _current_event.get()
    if event is None:
        event = WideEvent("unknown", "orphan")
        _current_event.set(event)
    return event
