from concurrent.futures import ThreadPoolExecutor

import backend.data.angel_one_client as angel_module
import backend.db.redis_client as redis_module
import backend.engine.market_data_service as market_module


def test_angel_one_singleton_is_thread_safe(monkeypatch):
    class FakeAngelClient:
        pass

    angel_module._client = None
    monkeypatch.setattr(angel_module, "AngelOneClient", FakeAngelClient)

    with ThreadPoolExecutor(max_workers=8) as executor:
        instances = list(executor.map(lambda _: angel_module.get_angel_one_client(), range(20)))

    assert len({id(instance) for instance in instances}) == 1


def test_market_data_service_singleton_is_thread_safe(monkeypatch):
    class FakeMarketDataService:
        pass

    market_module._service = None
    monkeypatch.setattr(market_module, "MarketDataService", FakeMarketDataService)

    with ThreadPoolExecutor(max_workers=8) as executor:
        instances = list(executor.map(lambda _: market_module.get_market_data_service(), range(20)))

    assert len({id(instance) for instance in instances}) == 1


def test_redis_cache_singleton_is_thread_safe(monkeypatch):
    class FakeRedisCache:
        pass

    redis_module._cache_instance = None
    monkeypatch.setattr(redis_module, "RedisCache", FakeRedisCache)

    with ThreadPoolExecutor(max_workers=8) as executor:
        instances = list(executor.map(lambda _: redis_module.get_cache(), range(20)))

    assert len({id(instance) for instance in instances}) == 1
