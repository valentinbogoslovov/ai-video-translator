"""Simple background task runner using threads.

Swap to Celery later for production workloads.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)
_active_tasks: dict[str, Any] = {}


def submit_task(task_id: str, fn: Callable, *args: Any, **kwargs: Any) -> None:
    """Submit a function to run in a background thread."""
    future = _executor.submit(fn, *args, **kwargs)
    _active_tasks[task_id] = future

    def _on_done(f: Any) -> None:
        _active_tasks.pop(task_id, None)
        if f.exception():
            logger.error("Task %s failed: %s", task_id, f.exception())

    future.add_done_callback(_on_done)


def is_task_running(task_id: str) -> bool:
    future = _active_tasks.get(task_id)
    return future is not None and not future.done()
