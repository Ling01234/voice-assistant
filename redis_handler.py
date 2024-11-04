# redis_handler.py
import json
import redis.asyncio as redis
import os
from dotenv import load_dotenv
load_dotenv()

# Configure Redis connection
redis_client = redis.from_url(f"redis://localhost:6379", password=os.getenv("REDIS_PASSWORD"))

LIVE_CALLS_KEY = "live_calls"  # Key to store all live call counts as JSON

async def get_live_calls(restaurant_id: int) -> int:
    """Retrieve the current number of live calls for a restaurant."""
    # Get the live calls JSON from Redis
    data = await redis_client.get(LIVE_CALLS_KEY)
    if data:
        live_calls = json.loads(data)
        return live_calls.get(str(restaurant_id), 0)
    return 0

async def increment_live_calls(restaurant_id: int) -> None:
    """Increment the live call count for a restaurant."""
    # Get the live calls JSON from Redis
    data = await redis_client.get(LIVE_CALLS_KEY)
    if data:
        live_calls = json.loads(data)
    else:
        live_calls = {}

    # Increment the count for the specific restaurant
    live_calls[str(restaurant_id)] = live_calls.get(str(restaurant_id), 0) + 1

    # Save the updated JSON back to Redis
    await redis_client.set(LIVE_CALLS_KEY, json.dumps(live_calls))

async def decrement_live_calls(restaurant_id: int) -> None:
    """Decrement the live call count for a restaurant."""
    # Get the live calls JSON from Redis
    data = await redis_client.get(LIVE_CALLS_KEY)
    if data:
        live_calls = json.loads(data)
    else:
        live_calls = {}

    # Decrement the count, ensuring it doesn’t go below zero
    if live_calls.get(str(restaurant_id), 0) > 0:
        live_calls[str(restaurant_id)] -= 1

    # Save the updated JSON back to Redis
    await redis_client.set(LIVE_CALLS_KEY, json.dumps(live_calls))

async def reset_live_calls(restaurant_id: int) -> None:
    """Reset the live call count for a specific restaurant to zero."""
    # Get the live calls JSON from Redis
    data = await redis_client.get(LIVE_CALLS_KEY)
    if data:
        live_calls = json.loads(data)
    else:
        live_calls = {}

    # Reset the count for the specific restaurant
    live_calls[str(restaurant_id)] = 0

    # Save the updated JSON back to Redis
    await redis_client.set(LIVE_CALLS_KEY, json.dumps(live_calls))

async def reset_all_live_calls() -> None:
    """Reset the live call count for all restaurants."""
    # Set the live calls JSON to an empty dictionary
    await redis_client.set(LIVE_CALLS_KEY, json.dumps({}))