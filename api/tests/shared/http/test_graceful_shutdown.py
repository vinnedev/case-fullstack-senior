import asyncio

import pytest

import main
from shared.http.graceful_shutdown import GracefulShutdown


@pytest.fixture()
def run():
    def _run(coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    return _run


def test_accepts_requests_before_shutdown(run):
    gs = GracefulShutdown()
    assert run(gs.request_started()) is True


def test_rejects_requests_during_shutdown(run):
    gs = GracefulShutdown()
    gs.shutting_down = True
    assert run(gs.request_started()) is False


def test_drain_returns_immediately_when_idle(run):
    gs = GracefulShutdown()
    run(gs.drain(timeout=0.1))
    assert gs.shutting_down is True


def test_drain_waits_for_in_flight_requests(run):
    gs = GracefulShutdown()

    async def scenario():
        await gs.request_started()

        async def finish_later():
            await asyncio.sleep(0.05)
            await gs.request_finished()

        task = asyncio.ensure_future(finish_later())
        await gs.drain(timeout=1.0)
        await task

    run(scenario())
    assert gs._in_flight == 0


def test_drain_gives_up_after_timeout(run):
    gs = GracefulShutdown()

    async def scenario():
        await gs.request_started()
        await gs.drain(timeout=0.05)

    run(scenario())
    assert gs._in_flight == 1


class TestProbeRouteLogSuppression:
    """Flag LOG_SUPPRESS_PROBE_ROUTES: tira ruído do Grafana sem esconder falha."""

    @pytest.mark.parametrize("path", ["/health", "/healthz", "/ready", "/readyz", "/live", "/livez", "/metrics", "/metrics/"])
    def test_probe_routes_are_suppressed_when_flag_on(self, path):
        assert main.should_emit_request_log(path, suppress_probe_routes=True) is False

    @pytest.mark.parametrize("path", ["/health", "/metrics", "/jobs"])
    def test_nothing_is_suppressed_when_flag_off(self, path):
        assert main.should_emit_request_log(path, suppress_probe_routes=False) is True

    @pytest.mark.parametrize("path", ["/jobs", "/jobs/1", "/admin/dlq", "/docs", "/healthcheck"])
    def test_business_routes_are_never_suppressed(self, path):
        assert main.should_emit_request_log(path, suppress_probe_routes=True) is True

    @pytest.mark.parametrize("status", [400, 401, 404, 500, 503])
    def test_failing_probe_routes_still_log(self, status):
        assert main.should_emit_request_log("/health", suppress_probe_routes=True, status=status) is True
