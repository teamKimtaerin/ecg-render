"""
Redis Manager for Worker Status Updates
Handles communication with Redis for job progress tracking
"""

import redis
import json
import logging
from typing import Dict, Any, Optional
import asyncio
from app.core.config import settings

logger = logging.getLogger(__name__)


class RedisManager:
    """Manages Redis connections and worker status updates"""

    def __init__(self, redis_url: Optional[str] = None):
        """Initialize Redis manager"""
        # Redis configuration from settings
        self.redis_url = redis_url or settings.REDIS_URL
        self.redis_pool_size = 20

        # Create connection pool (macOS compatible)
        self.pool = redis.ConnectionPool.from_url(
            self.redis_url,
            max_connections=self.redis_pool_size,
            decode_responses=True,
            socket_timeout=10,
            socket_connect_timeout=10
        )

        # Create Redis client
        self.client = redis.Redis(connection_pool=self.pool)

        # Test connection
        try:
            self.client.ping()
            logger.info(f"✅ Redis connected: {self.redis_url}")
        except Exception as e:
            logger.error(f"❌ Redis connection failed: {str(e)}")
            raise

    async def update_worker_status(
        self,
        job_id: str,
        worker_id: int,
        status: str,
        progress: float
    ) -> bool:
        """
        Update worker status in Redis

        Args:
            job_id: Job identifier
            worker_id: Worker identifier (0-3)
            status: Worker status (pending, processing, completed, failed)
            progress: Progress percentage (0-100)

        Returns:
            Success status
        """
        try:
            # Create status data
            status_data = {
                'status': status,
                'progress': min(100, max(0, progress)),  # Clamp to 0-100
                'updated_at': datetime.utcnow().isoformat(),
                'worker_id': worker_id
            }

            # Store in Redis with TTL
            key = f"worker:{job_id}:{worker_id}"
            value = json.dumps(status_data)

            # Use async Redis operation
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self.client.setex,
                key,
                600,  # 10 minutes TTL
                value
            )

            if result:
                logger.debug(f"Updated worker status: {key} -> {status} ({progress:.1f}%)")

            return bool(result)

        except Exception as e:
            logger.error(f"Failed to update worker status: {str(e)}")
            return False

    async def get_worker_status(self, job_id: str, worker_id: int) -> Optional[Dict[str, Any]]:
        """
        Get worker status from Redis

        Args:
            job_id: Job identifier
            worker_id: Worker identifier

        Returns:
            Worker status data or None
        """
        try:
            key = f"worker:{job_id}:{worker_id}"

            # Async Redis operation
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, self.client.get, key)

            if data:
                return json.loads(data)

            return None

        except Exception as e:
            logger.error(f"Failed to get worker status: {str(e)}")
            return None

    async def get_all_worker_statuses(
        self,
        job_id: str,
        worker_count: int = 4
    ) -> Dict[int, Dict[str, Any]]:
        """
        Get all worker statuses for a job

        Args:
            job_id: Job identifier
            worker_count: Number of workers

        Returns:
            Dictionary of worker statuses
        """
        statuses = {}

        for worker_id in range(worker_count):
            status = await self.get_worker_status(job_id, worker_id)
            if status:
                statuses[worker_id] = status
            else:
                # Default status if not found
                statuses[worker_id] = {
                    'status': 'pending',
                    'progress': 0,
                    'worker_id': worker_id
                }

        return statuses





    async def ping(self) -> bool:
        """
        Check Redis connection

        Returns:
            Connection status
        """
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self.client.ping)
            return bool(result)
        except:
            return False

    def close(self):
        """Close Redis connection pool"""
        try:
            self.pool.disconnect()
            logger.debug("Redis connection pool closed")
        except Exception as e:
            logger.error(f"Error closing Redis pool: {str(e)}")




    def __del__(self):
        """Cleanup on deletion"""
        self.close()


# Global Redis manager instance
_redis_manager = None


def get_redis_manager() -> RedisManager:
    """Get singleton Redis manager instance"""
    global _redis_manager
    if _redis_manager is None:
        _redis_manager = RedisManager()
    return _redis_manager