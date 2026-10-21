# In: utils/openai_pool.py

import asyncio
import httpx
from openai import AsyncOpenAI
import os
from dotenv import load_dotenv
from pathlib import Path

# from config.settings import get_openai_client

# OPENAI_API_KEY = get_openai_client

dotenv_path = Path(r"D:\TOC-chatbot\TOC-chatbot\backend\config\.env")

load_dotenv(dotenv_path=dotenv_path)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


class OpenAIPool:
    def __init__(self, max_concurrent_requests=10, api_key_env_var="OPENAI_API_KEY"):
        api_key = os.getenv(api_key_env_var)
        if not api_key:
            raise ValueError(f"{api_key_env_var} not found in environment variables")
        
        # This client manages the persistent connection pool
        self.http_client = httpx.AsyncClient()
        self.client = AsyncOpenAI(api_key=api_key, http_client=self.http_client)
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)

    async def get_client(self):
        return self.client

    async def close(self):
        """Properly closes the underlying HTTP client's connections."""
        print("\n[INFO] Closing OpenAI HTTP client connections...")
        await self.http_client.aclose()


# Pool instances for different API keys
_pool_instances = {}

def get_pool(api_key_env_var="OPENAI_API_KEY", max_concurrent_requests=10):
    """Get or create a pool instance for a specific API key."""
    if api_key_env_var not in _pool_instances:
        _pool_instances[api_key_env_var] = OpenAIPool(max_concurrent_requests, api_key_env_var)
    return _pool_instances[api_key_env_var]

async def close_all_pools():
    """Close all pool instances."""
    for pool in _pool_instances.values():
        await pool.close()
    _pool_instances.clear()