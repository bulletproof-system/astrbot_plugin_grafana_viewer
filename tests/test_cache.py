import asyncio

import pytest

from core.cache import RenderCoordinator
from core.errors import RateLimitedError
from core.models import RenderedImage

PNG = RenderedImage(content=b"png", content_type="image/png")


def test_cache_reuses_successful_render():
    class Clock:
        now = 100.0

        def __call__(self):
            return self.now

    clock = Clock()
    calls = 0

    async def scenario():
        nonlocal calls
        coordinator = RenderCoordinator(
            cache_seconds=20,
            cooldown_seconds=15,
            clock=clock,
        )

        async def loader():
            nonlocal calls
            calls += 1
            return PNG

        first = await coordinator.get_or_render(group_key="g1", cache_key="cpu|1h", loader=loader)
        clock.now += 19
        second = await coordinator.get_or_render(group_key="g1", cache_key="cpu|1h", loader=loader)
        await coordinator.close()
        return first, second

    assert asyncio.run(scenario()) == (PNG, PNG)
    assert calls == 1


def test_concurrent_identical_requests_are_coalesced():
    async def scenario():
        coordinator = RenderCoordinator(cache_seconds=20, cooldown_seconds=0)
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return PNG

        results = await asyncio.gather(
            coordinator.get_or_render(group_key="g1", cache_key="same", loader=loader),
            coordinator.get_or_render(group_key="g1", cache_key="same", loader=loader),
        )
        await coordinator.close()
        return calls, results

    calls, results = asyncio.run(scenario())
    assert calls == 1
    assert results == [PNG, PNG]


def test_cooldown_applies_to_new_render_only():
    async def scenario():
        def clock():
            return 100.0

        coordinator = RenderCoordinator(
            cache_seconds=20,
            cooldown_seconds=15,
            clock=clock,
        )

        async def loader():
            return PNG

        await coordinator.get_or_render(group_key="g1", cache_key="cpu|1h", loader=loader)
        with pytest.raises(RateLimitedError):
            await coordinator.get_or_render(group_key="g1", cache_key="cpu|6h", loader=loader)
        await coordinator.get_or_render(group_key="g1", cache_key="cpu|1h", loader=loader)
        await coordinator.close()

    asyncio.run(scenario())


def test_group_concurrency_limit_rejects_parallel_different_render():
    async def scenario():
        coordinator = RenderCoordinator(cache_seconds=20, cooldown_seconds=0)
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_loader():
            started.set()
            await release.wait()
            return PNG

        first = asyncio.create_task(
            coordinator.get_or_render(group_key="g1", cache_key="slow", loader=slow_loader)
        )
        await started.wait()
        with pytest.raises(RateLimitedError):
            await coordinator.get_or_render(
                group_key="g1", cache_key="different", loader=slow_loader
            )
        release.set()
        await first
        await coordinator.close()

    asyncio.run(scenario())


def test_render_direct_bypasses_cache():
    async def scenario():
        coordinator = RenderCoordinator(cache_seconds=20, cooldown_seconds=15)
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            return PNG

        first = await coordinator.render_direct(loader)
        second = await coordinator.render_direct(loader)
        await coordinator.close()
        return first, second, calls

    first, second, calls = asyncio.run(scenario())
    assert first == PNG and second == PNG
    assert calls == 2
