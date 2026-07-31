import logging
from typing import Any

import pytest

import main
from shared.logging.access_log import (
    ACCESS_LOGGER,
    ProbeRouteAccessFilter,
    install_probe_route_access_filter,
    remove_probe_route_access_filter,
)

ACCESS_FORMAT = '%s - "%s %s HTTP/%s" %d %s'
UNSET = object()


def access_record(path: str, status: int, args_override: Any = UNSET) -> logging.LogRecord:
    args: Any = ("127.0.0.1:1", "GET", path, "1.1", status, "OK") if args_override is UNSET else args_override
    return logging.LogRecord(ACCESS_LOGGER, logging.INFO, "", 0, ACCESS_FORMAT, args, None)


@pytest.fixture()
def access_filter():
    return ProbeRouteAccessFilter(main.is_probe_route)


@pytest.mark.parametrize("path", ["/health", "/healthz", "/ready", "/readyz", "/live", "/livez", "/metrics", "/metrics/"])
def test_successful_probe_requests_are_dropped(access_filter, path):
    assert access_filter.filter(access_record(path, 200)) is False


@pytest.mark.parametrize("path", ["/jobs", "/jobs/1", "/admin/dlq", "/healthcheck", "/"])
def test_business_requests_are_kept(access_filter, path):
    assert access_filter.filter(access_record(path, 200)) is True


@pytest.mark.parametrize("status", [400, 401, 404, 500, 503])
def test_failing_probe_requests_are_kept(access_filter, status):
    assert access_filter.filter(access_record("/health", status)) is True


def test_query_string_does_not_hide_probe_route(access_filter):
    assert access_filter.filter(access_record("/health?verbose=1", 200)) is False


@pytest.mark.parametrize(
    "args", [None, (), ("only", "three", "args"), ("c", "GET", 123, "1.1", 200, "OK"), ("c", "GET", "/health", "1.1", "abc", "OK")]
)
def test_unknown_record_shapes_are_never_swallowed(access_filter, args):
    # formato inesperado do uvicorn não pode virar silêncio: na dúvida, loga
    assert access_filter.filter(access_record("/health", 200, args_override=args)) is True


def test_install_is_idempotent_and_removable():
    logger = logging.getLogger(ACCESS_LOGGER)
    remove_probe_route_access_filter()
    install_probe_route_access_filter(main.is_probe_route)
    install_probe_route_access_filter(main.is_probe_route)
    try:
        assert sum(isinstance(f, ProbeRouteAccessFilter) for f in logger.filters) == 1
    finally:
        remove_probe_route_access_filter()
    assert not any(isinstance(f, ProbeRouteAccessFilter) for f in logger.filters)
