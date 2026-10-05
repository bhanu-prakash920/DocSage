"""Thread-pool helpers that carry context variables (usage meters) into worker threads."""

from __future__ import annotations

import contextvars
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def pmap(
    fn: Callable[[T], R], items: Iterable[T], workers: int = 4, on_done: Callable[[int], None] | None = None
) -> list[R]:
    items = list(items)
    if not items:
        return []
    if workers <= 1 or len(items) == 1:
        out = []
        for i, item in enumerate(items):
            out.append(fn(item))
            if on_done:
                on_done(i + 1)
        return out
    ctx = contextvars.copy_context()
    results: list[R] = [None] * len(items)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(ctx.copy().run, fn, item): i for i, item in enumerate(items)}
        for done, future in enumerate(list(futures), start=1):
            results[futures[future]] = future.result()
            if on_done:
                on_done(done)
    return results
