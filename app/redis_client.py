import json
from typing import Any, Optional

import redis.asyncio as aioredis

from app.config import settings

_pool = aioredis.ConnectionPool.from_url(settings.redis_url, max_connections=50, decode_responses=True)
redis_client = aioredis.Redis(connection_pool=_pool)


def _key(session_id: str) -> str:
    return f"interview:session:{session_id}"


async def cache_session_state(session_id: str, state: dict[str, Any]) -> None:
    """Store the hot conversation state for a session so subsequent turns don't
    have to reload full history from Postgres. This is what lets the service
    handle many concurrent multi-turn sessions without hammering the DB."""
    await redis_client.set(_key(session_id), json.dumps(state), ex=settings.session_ttl_seconds)


async def get_session_state(session_id: str) -> Optional[dict[str, Any]]:
    raw = await redis_client.get(_key(session_id))
    return json.loads(raw) if raw else None


async def drop_session_state(session_id: str) -> None:
    await redis_client.delete(_key(session_id))
