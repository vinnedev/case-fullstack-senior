import json

import pytest

from shared.logging import wide_event as we
from shared.logging.wide_event import WideEvent, start_event, current_event


def emitted(capsys) -> dict | None:
    out = capsys.readouterr().out.strip()
    if not out:
        return None
    return json.loads(out.splitlines()[-1])


def test_emit_outputs_single_json_line_with_base_fields(capsys):
    WideEvent("svc", "thing_happened").add(foo="bar").emit()
    event = emitted(capsys)
    assert event["service"] == "svc"
    assert event["event"] == "thing_happened"
    assert event["foo"] == "bar"
    assert event["level"] == "info"
    assert "duration_ms" in event and "timestamp" in event and "event_id" in event


def test_add_accumulates_context_progressively(capsys):
    event = WideEvent("svc", "request")
    event.add(step_one=True)
    event.add(step_two=2)
    event.emit()
    out = emitted(capsys)
    assert out["step_one"] is True
    assert out["step_two"] == 2


def test_error_captures_exception_and_sets_level(capsys):
    event = WideEvent("svc", "request")
    event.error(ValueError("boom"), outcome="failed")
    event.emit()
    out = emitted(capsys)
    assert out["level"] == "error"
    assert out["error_type"] == "ValueError"
    assert out["error_message"] == "boom"
    assert out["outcome"] == "failed"


def test_successful_events_respect_sample_rate_zero(capsys, monkeypatch):
    monkeypatch.setattr(we, "SUCCESS_SAMPLE_RATE", 0.0)
    monkeypatch.setattr(we, "SLOW_THRESHOLD_MS", 10_000)
    WideEvent("svc", "request").emit()
    assert emitted(capsys) is None


def test_errors_always_emit_even_with_sample_rate_zero(capsys, monkeypatch):
    monkeypatch.setattr(we, "SUCCESS_SAMPLE_RATE", 0.0)
    event = WideEvent("svc", "request")
    event.error(RuntimeError("x"))
    event.emit()
    assert emitted(capsys)["level"] == "error"


def test_slow_events_always_emit_even_with_sample_rate_zero(capsys, monkeypatch):
    monkeypatch.setattr(we, "SUCCESS_SAMPLE_RATE", 0.0)
    monkeypatch.setattr(we, "SLOW_THRESHOLD_MS", 0.0)
    WideEvent("svc", "request").emit()
    assert emitted(capsys)["level"] == "info"


def test_start_event_binds_current_event():
    event = start_event("svc", "request", request_id="abc")
    assert current_event() is event
    assert event.fields["request_id"] == "abc"


def test_current_event_without_start_returns_orphan():
    start_event("svc", "request")
    we._current_event.set(None)
    orphan = current_event()
    assert orphan.fields["event"] == "orphan"
