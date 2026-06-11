"""Async crawl task manager for traceable live fanout workflows."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from gflights.live_trace import TaskTrace, new_task_trace

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class CrawlTaskResult(Generic[R]):
    index: int
    name: str
    task_trace: TaskTrace
    value: R


class AsyncCrawlTaskManager:
    """Structured-concurrency manager for one crawl workflow."""

    def __init__(self, *, root_trace: TaskTrace, concurrency: int = 1) -> None:
        self.root_trace = root_trace
        self.concurrency = max(1, concurrency)
        self._semaphore = asyncio.Semaphore(self.concurrency)

    @classmethod
    def root(
        cls, *, command: str, name: str, run_id: str, concurrency: int = 1
    ) -> "AsyncCrawlTaskManager":
        return cls(
            root_trace=new_task_trace(command=command, name=name, run_id=run_id),
            concurrency=concurrency,
        )

    def child(self, name: str, *, run_id: str | None = None) -> TaskTrace:
        return self.root_trace.child(name, run_id=run_id)

    async def map_ordered(
        self,
        items: Sequence[T],
        *,
        task_name: str,
        worker: Callable[[int, T, TaskTrace], Awaitable[R]],
        run_id_for_item: Callable[[int, T], str | None] | None = None,
    ) -> list[CrawlTaskResult[R]]:
        """Run bounded fanout with stable input order and child task traces."""
        results: list[CrawlTaskResult[R] | None] = [None] * len(items)

        async def run_one(position: int, item: T, trace: TaskTrace) -> None:
            async with self._semaphore:
                results[position - 1] = CrawlTaskResult(
                    index=position,
                    name=trace.name,
                    task_trace=trace,
                    value=await worker(position, item, trace),
                )

        async with asyncio.TaskGroup() as task_group:
            for position, item in enumerate(items, start=1):
                run_id = run_id_for_item(position, item) if run_id_for_item else None
                trace = self.root_trace.child(
                    f"{task_name}-{position:02d}",
                    run_id=run_id,
                )
                task_group.create_task(
                    run_one(position, item, trace),
                    name=f"{self.root_trace.name}:{trace.name}",
                )

        return [result for result in results if result is not None]
