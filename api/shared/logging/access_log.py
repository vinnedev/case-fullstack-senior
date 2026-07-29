"""Filtro do access log do uvicorn.

O servidor escreve uma linha de texto por request, em paralelo ao nosso wide
event JSON. Para as rotas de scrape/health isso é ruído duplicado que domina o
stream no Grafana (o Prometheus consulta a cada 10s e o healthcheck do
container também). A mesma flag que governa o wide event governa esta linha,
com a mesma regra: só respostas bem-sucedidas são omitidas.
"""

import logging
from collections.abc import Callable

ACCESS_LOGGER = "uvicorn.access"


class ProbeRouteAccessFilter(logging.Filter):
    def __init__(self, is_probe_route: Callable[[str], bool]) -> None:
        super().__init__()
        self._is_probe_route = is_probe_route

    def filter(self, record: logging.LogRecord) -> bool:
        path, status = _parse_access_record(record)
        if path is None or status is None:
            return True  # formato desconhecido: nunca engolir silenciosamente
        if status >= 400:
            return True
        return not self._is_probe_route(path)


def _parse_access_record(record: logging.LogRecord) -> tuple[str | None, int | None]:
    # uvicorn: ("%s - \"%s %s HTTP/%s\" %d %s", client, method, path, version, status, phrase)
    args = record.args
    if not isinstance(args, tuple) or len(args) < 5:
        return None, None
    raw_path, raw_status = args[2], args[4]
    if not isinstance(raw_path, str):
        return None, None
    try:
        status = int(raw_status)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None, None
    return raw_path.split("?", 1)[0], status


def install_probe_route_access_filter(is_probe_route: Callable[[str], bool]) -> None:
    logger = logging.getLogger(ACCESS_LOGGER)
    if any(isinstance(f, ProbeRouteAccessFilter) for f in logger.filters):
        return
    logger.addFilter(ProbeRouteAccessFilter(is_probe_route))


def remove_probe_route_access_filter() -> None:
    logger = logging.getLogger(ACCESS_LOGGER)
    for existing in [f for f in logger.filters if isinstance(f, ProbeRouteAccessFilter)]:
        logger.removeFilter(existing)
