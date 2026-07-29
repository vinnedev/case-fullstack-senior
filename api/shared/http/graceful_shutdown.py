import asyncio

from shared.logging.wide_event import WideEvent


class GracefulShutdown:
    def __init__(self, service: str = "api") -> None:
        self.service = service
        self.shutting_down = False
        self._in_flight = 0
        self._lock = asyncio.Lock()
        self._drained = asyncio.Event()
        self._drained.set()

    async def request_started(self) -> bool:
        # checagem dentro do lock: fora dele, uma request podia passar pelo flag,
        # o drain() ver in_flight==0 e o pool ser descartado com a request viva
        async with self._lock:
            if self.shutting_down:
                return False
            self._in_flight += 1
            self._drained.clear()
        return True

    async def request_finished(self) -> None:
        async with self._lock:
            self._in_flight -= 1
            if self._in_flight <= 0:
                self._drained.set()

    async def drain(self, timeout: float) -> None:
        self.shutting_down = True
        if self._in_flight <= 0:
            return
        try:
            await asyncio.wait_for(self._drained.wait(), timeout)
        except TimeoutError:
            WideEvent(self.service, "shutdown_drain_timeout").error(timeout_s=timeout, in_flight=self._in_flight).emit()
