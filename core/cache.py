from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .errors import RateLimitedError, RenderFailedError
from .models import RenderedImage

Loader = Callable[[], Awaitable[RenderedImage]]


@dataclass(slots=True)
class _CacheEntry:
    image: RenderedImage
    expires_at: float


class ImageCache:
    """A small in-memory TTL + LRU cache for successful renders only."""

    def __init__(
        self,
        *,
        ttl_seconds: int,
        max_entries: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = max(0, ttl_seconds)
        self._max_entries = max(1, max_entries)
        self._clock = clock
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()

    def get(self, key: str) -> RenderedImage | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return entry.image

    def put(self, key: str, image: RenderedImage) -> None:
        if self._ttl_seconds <= 0:
            return
        self._entries.pop(key, None)
        self._entries[key] = _CacheEntry(
            image=image,
            expires_at=self._clock() + self._ttl_seconds,
        )
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


class RenderCoordinator:
    """Cache successful renders and bound duplicate, group, and global work."""

    def __init__(
        self,
        *,
        cache_seconds: int,
        cooldown_seconds: int,
        global_limit: int = 2,
        max_cache_entries: int = 100,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cache = ImageCache(
            ttl_seconds=cache_seconds,
            max_entries=max_cache_entries,
            clock=clock,
        )
        self.cooldown_seconds = max(0, cooldown_seconds)
        self._clock = clock
        self._global_semaphore = asyncio.Semaphore(max(1, global_limit))
        self._state_lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task[RenderedImage]] = {}
        self._group_locks: dict[str, asyncio.Lock] = {}
        self._last_render_started: dict[str, float] = {}
        self._closed = False

    @property
    def cache_size(self) -> int:
        return len(self.cache)

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    async def get_or_render(
        self,
        *,
        group_key: str,
        cache_key: str,
        loader: Loader,
    ) -> RenderedImage:
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        async with self._state_lock:
            if self._closed:
                raise RenderFailedError("renderer is closed")

            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

            task = self._inflight.get(cache_key)
            if task is None:
                now = self._clock()
                last_started = self._last_render_started.get(group_key)
                if (
                    self.cooldown_seconds > 0
                    and last_started is not None
                    and now - last_started < self.cooldown_seconds
                ):
                    raise RateLimitedError("group cooldown is active")

                group_lock = self._group_locks.setdefault(
                    group_key,
                    asyncio.Lock(),
                )
                if group_lock.locked():
                    raise RateLimitedError("group already has an active render")

                self._last_render_started[group_key] = now
                task = asyncio.create_task(
                    self._run_render(
                        cache_key=cache_key,
                        group_lock=group_lock,
                        loader=loader,
                    )
                )
                self._inflight[cache_key] = task

        return await asyncio.shield(task)

    async def _run_render(
        self,
        *,
        cache_key: str,
        group_lock: asyncio.Lock,
        loader: Loader,
    ) -> RenderedImage:
        current_task = asyncio.current_task()
        try:
            async with group_lock:
                async with self._global_semaphore:
                    image = await loader()
            self.cache.put(cache_key, image)
            return image
        finally:
            async with self._state_lock:
                if self._inflight.get(cache_key) is current_task:
                    self._inflight.pop(cache_key, None)

    async def render_direct(self, loader: Loader) -> RenderedImage:
        if self._closed:
            raise RenderFailedError("renderer is closed")
        async with self._global_semaphore:
            return await loader()

    def clear_cache(self) -> None:
        self.cache.clear()

    async def close(self) -> None:
        async with self._state_lock:
            if self._closed:
                return
            self._closed = True
            tasks = list(self._inflight.values())

        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
