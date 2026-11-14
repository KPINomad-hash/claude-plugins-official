"""One dedicated thread per server on which every COM call is made.

COM automation servers of desktop programs are single-threaded apartments:
objects must be used from the thread that created them, and calls must not
overlap. Routing every call through one worker thread gives both guarantees
and serialises operations inside one program instance.

A call that exceeds its timeout is NOT cancelled (a COM call cannot be) — the
caller gets a `timeout` error and later calls get `busy` until the program
returns. That keeps "we stopped waiting" distinct from "the solver stopped".
"""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

from . import errors
from .errors import AdapterError


class ComThread:
    def __init__(self, name: str = "com-worker"):
        self._queue: "queue.Queue[tuple[Future, Callable[[], Any], str]]" = queue.Queue()
        self._current: tuple[str, float] | None = None
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        pythoncom = None
        try:
            import pythoncom  # type: ignore

            pythoncom.CoInitialize()
        except ImportError:
            pythoncom = None
        try:
            while True:
                future, fn, label = self._queue.get()
                if not future.set_running_or_notify_cancel():
                    continue
                self._current = (label, time.monotonic())
                try:
                    future.set_result(fn())
                except BaseException as exc:  # noqa: BLE001 - handed to the caller
                    future.set_exception(exc)
                finally:
                    self._current = None
        finally:
            if pythoncom is not None:
                pythoncom.CoUninitialize()

    def busy_with(self) -> dict | None:
        current = self._current
        if current is None:
            return None
        return {"operation": current[0], "running_seconds": round(time.monotonic() - current[1], 1)}

    def submit(self, fn: Callable[[], Any], label: str) -> Future:
        busy = self.busy_with()
        if busy is not None:
            raise AdapterError(
                errors.BUSY,
                f"The program is still executing '{busy['operation']}' ({busy['running_seconds']} s).",
                "Wait and retry; check get_job_status if an analysis is running, "
                "or look for a modal dialog open in the program window.",
            )
        future: Future = Future()
        self._queue.put((future, fn, label))
        return future

    def call(self, fn: Callable[[], Any], label: str, timeout: float) -> Any:
        future = self.submit(fn, label)
        try:
            return future.result(timeout=timeout)
        except FutureTimeout:
            raise AdapterError(
                errors.TIMEOUT,
                f"No answer from the program for '{label}' within {timeout:.0f} s. "
                "The call was not cancelled and the program may still be working.",
                "Check the program window for a modal dialog or a long operation, then retry.",
            ) from None
