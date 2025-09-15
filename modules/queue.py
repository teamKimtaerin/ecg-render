"""
Render Queue Management System
Handles job queuing and distribution for GPU rendering tasks
"""

import json
import asyncio
import uuid
from typing import Dict, List, Optional, Any
from datetime import datetime
import redis
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)


@dataclass
class RenderJob:
    """Render job data structure"""
    job_id: str
    video_url: str
    scenario: Dict[str, Any]
    options: Dict[str, Any]
    callback_url: str
    status: str = "queued"
    progress: int = 0
    created_at: str = None
    started_at: str = None
    completed_at: str = None
    error_message: str = None
    error_code: str = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> 'RenderJob':
        return cls(**data)


class RenderQueue:
    """Redis-based render job queue"""

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        """Initialize queue with Redis connection"""
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.queue_key = "render:queue"
        self.jobs_key = "render:jobs"
        self.active_key = "render:active"
        self.max_concurrent = 3

    async def add_job(self, job: RenderJob) -> str:
        """Add job to queue"""
        try:
            # Store job data
            job_data = json.dumps(job.to_dict())
            self.redis_client.hset(self.jobs_key, job.job_id, job_data)

            # Add to queue
            self.redis_client.rpush(self.queue_key, job.job_id)

            logger.info(f"Job {job.job_id} added to queue")
            return job.job_id

        except Exception as e:
            logger.error(f"Failed to add job {job.job_id}: {e}")
            raise

    async def get_next_job(self) -> Optional[RenderJob]:
        """Get next job from queue"""
        try:
            # Check if we can process more jobs
            active_count = self.redis_client.scard(self.active_key)
            if active_count >= self.max_concurrent:
                return None

            # Get next job ID from queue
            job_id = self.redis_client.lpop(self.queue_key)
            if not job_id:
                return None

            # Get job data
            job_data = self.redis_client.hget(self.jobs_key, job_id)
            if not job_data:
                logger.error(f"Job {job_id} not found in storage")
                return None

            # Parse job
            job_dict = json.loads(job_data)
            job = RenderJob.from_dict(job_dict)

            # Mark as active
            self.redis_client.sadd(self.active_key, job_id)

            # Update status
            job.status = "processing"
            job.started_at = datetime.utcnow().isoformat()
            self.update_job(job)

            logger.info(f"Job {job_id} retrieved from queue")
            return job

        except Exception as e:
            logger.error(f"Failed to get next job: {e}")
            return None

    def update_job(self, job: RenderJob) -> None:
        """Update job in storage"""
        try:
            job_data = json.dumps(job.to_dict())
            self.redis_client.hset(self.jobs_key, job.job_id, job_data)
            logger.debug(f"Job {job.job_id} updated")

        except Exception as e:
            logger.error(f"Failed to update job {job.job_id}: {e}")

    def complete_job(self, job_id: str) -> None:
        """Mark job as completed"""
        try:
            # Remove from active set
            self.redis_client.srem(self.active_key, job_id)

            # Update job status
            job_data = self.redis_client.hget(self.jobs_key, job_id)
            if job_data:
                job_dict = json.loads(job_data)
                job = RenderJob.from_dict(job_dict)
                job.status = "completed"
                job.completed_at = datetime.utcnow().isoformat()
                job.progress = 100
                self.update_job(job)

            logger.info(f"Job {job_id} completed")

        except Exception as e:
            logger.error(f"Failed to complete job {job_id}: {e}")

    def fail_job(self, job_id: str, error_message: str, error_code: str = "RENDER_ERROR") -> None:
        """Mark job as failed"""
        try:
            # Remove from active set
            self.redis_client.srem(self.active_key, job_id)

            # Update job status
            job_data = self.redis_client.hget(self.jobs_key, job_id)
            if job_data:
                job_dict = json.loads(job_data)
                job = RenderJob.from_dict(job_dict)
                job.status = "failed"
                job.error_message = error_message
                job.error_code = error_code
                job.completed_at = datetime.utcnow().isoformat()
                self.update_job(job)

            logger.error(f"Job {job_id} failed: {error_message}")

        except Exception as e:
            logger.error(f"Failed to mark job {job_id} as failed: {e}")

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a job"""
        try:
            # Remove from queue if present
            self.redis_client.lrem(self.queue_key, 0, job_id)

            # Remove from active set if present
            was_active = self.redis_client.srem(self.active_key, job_id)

            # Update job status
            job_data = self.redis_client.hget(self.jobs_key, job_id)
            if job_data:
                job_dict = json.loads(job_data)
                job = RenderJob.from_dict(job_dict)
                job.status = "cancelled"
                job.completed_at = datetime.utcnow().isoformat()
                self.update_job(job)

                logger.info(f"Job {job_id} cancelled")
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to cancel job {job_id}: {e}")
            return False

    def get_job(self, job_id: str) -> Optional[RenderJob]:
        """Get job by ID"""
        try:
            job_data = self.redis_client.hget(self.jobs_key, job_id)
            if job_data:
                job_dict = json.loads(job_data)
                return RenderJob.from_dict(job_dict)
            return None

        except Exception as e:
            logger.error(f"Failed to get job {job_id}: {e}")
            return None

    def get_queue_status(self) -> Dict[str, Any]:
        """Get queue status"""
        try:
            return {
                "queue_size": self.redis_client.llen(self.queue_key),
                "active_jobs": self.redis_client.scard(self.active_key),
                "total_jobs": self.redis_client.hlen(self.jobs_key),
                "max_concurrent": self.max_concurrent
            }

        except Exception as e:
            logger.error(f"Failed to get queue status: {e}")
            return {
                "queue_size": 0,
                "active_jobs": 0,
                "total_jobs": 0,
                "max_concurrent": self.max_concurrent
            }

    def clear_queue(self) -> None:
        """Clear all jobs from queue (for testing)"""
        try:
            self.redis_client.delete(self.queue_key)
            self.redis_client.delete(self.jobs_key)
            self.redis_client.delete(self.active_key)
            logger.info("Queue cleared")

        except Exception as e:
            logger.error(f"Failed to clear queue: {e}")