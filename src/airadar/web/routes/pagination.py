from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from threading import Lock


class VersionedTotalCache:
    def __init__(self, *, maxsize: int) -> None:
        self._maxsize = maxsize
        self._values: OrderedDict[tuple[object, ...], int] = OrderedDict()
        self._lock = Lock()

    def get_or_compute(
        self,
        *,
        signature: tuple[object, ...],
        version: tuple[object, ...],
        compute: Callable[[], int],
        cacheable: bool,
    ) -> int:
        if not cacheable:
            return compute()

        key = (*signature, version)
        with self._lock:
            cached = self._values.get(key)
            if cached is not None:
                self._values.move_to_end(key)
                return cached

        total = compute()
        with self._lock:
            self._values[key] = total
            self._values.move_to_end(key)
            while len(self._values) > self._maxsize:
                self._values.popitem(last=False)
        return total

    def prewarm(
        self,
        *,
        signature: tuple[object, ...],
        version: tuple[object, ...],
        compute: Callable[[], int],
    ) -> None:
        self.get_or_compute(
            signature=signature,
            version=version,
            compute=compute,
            cacheable=True,
        )


def clamp_page(*, page: int, total: int, limit: int) -> int:
    total_pages = max(1, (total + limit - 1) // limit)
    return min(max(page, 1), total_pages)
