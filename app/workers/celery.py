#!/usr/bin/env python3
"""
Celery Worker for GPU Render Server
Processes video rendering segments from API Server
"""

import os
import sys
import logging
from celery import Celery
from celery.signals import worker_ready, worker_shutdown
import asyncio

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import settings and render engine
from app.core.config import settings
from render_engine import GPURenderEngine

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Celery configuration from settings
CELERY_BROKER_URL = settings.CELERY_BROKER_URL
CELERY_RESULT_BACKEND = settings.CELERY_RESULT_BACKEND

# Create Celery app
app = Celery(
    'gpu_render',
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND
)

# Celery configuration
app.conf.update(
    # Worker settings
    worker_concurrency=1,  # Single process per worker
    worker_prefetch_multiplier=1,  # Fetch one task at a time
    worker_max_tasks_per_child=10,  # Restart after 10 tasks to prevent memory leaks

    # Task settings
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,

    # Time limits
    task_time_limit=600,  # 10 minutes hard limit
    task_soft_time_limit=540,  # 9 minutes soft limit

    # Result backend
    result_expires=3600,  # Results expire after 1 hour

    # Queue routing
    task_routes={
        'render.segment': {'queue': 'render_queue'},
        'render.merge_segments': {'queue': 'render_queue'},
    },

    # Error handling
    task_acks_late=True,  # Acknowledge after completion
    task_reject_on_worker_lost=True,  # Reject if worker dies
)

# Initialize render engine (singleton)
render_engine = None


@worker_ready.connect
def on_worker_ready(sender=None, **kwargs):
    """Called when worker is ready to accept tasks"""
    global render_engine
    render_engine = GPURenderEngine()
    logger.info("🚀 GPU Render Worker ready to accept tasks")
    logger.info(f"   Broker: {CELERY_BROKER_URL}")
    logger.info(f"   Backend: {CELERY_RESULT_BACKEND}")


@worker_shutdown.connect
def on_worker_shutdown(sender=None, **kwargs):
    """Called when worker is shutting down"""
    global render_engine
    if render_engine:
        # Cleanup render engine resources
        asyncio.run(render_engine.cleanup())
    logger.info("👋 GPU Render Worker shutting down")


@app.task(name='render.segment', bind=True, max_retries=3)
def render_segment(self, job_id: str, segment: dict):
    """
    Render a video segment

    Args:
        job_id: Unique job identifier
        segment: Segment data containing:
            - worker_id: Worker identifier (0-3)
            - start_time: Start time in seconds
            - end_time: End time in seconds
            - start_frame: Start frame number
            - end_frame: End frame number
            - cues: Subtitle cues for this segment
            - scenario_metadata: Video metadata (width, height, fps)

    Returns:
        Rendering result with output path and statistics
    """
    try:
        logger.info(f"📦 Worker {segment.get('worker_id')} starting segment for job {job_id}")
        logger.info(f"   Time range: {segment.get('start_time')}-{segment.get('end_time')}s")
        logger.info(f"   Frame range: {segment.get('start_frame')}-{segment.get('end_frame')}")

        # Run async rendering
        result = asyncio.run(render_engine.render_segment(job_id, segment))

        logger.info(f"✅ Worker {segment.get('worker_id')} completed segment for job {job_id}")
        return result

    except Exception as e:
        logger.error(f"❌ Segment rendering failed: {str(e)}")

        # Retry with exponential backoff
        if self.request.retries < self.max_retries:
            retry_in = 2 ** self.request.retries  # 2, 4, 8 seconds
            logger.info(f"🔄 Retrying in {retry_in} seconds (attempt {self.request.retries + 1}/{self.max_retries})")
            raise self.retry(countdown=retry_in)

        # Final failure
        raise


@app.task(name='render.merge_segments', bind=True, max_retries=2)
def merge_segments(self, job_id: str, segment_results: list):
    """
    Merge rendered segments into final video

    Args:
        job_id: Unique job identifier
        segment_results: List of segment rendering results

    Returns:
        Final video information with output path
    """
    try:
        logger.info(f"🎬 Merging {len(segment_results)} segments for job {job_id}")

        # Run async merging
        result = asyncio.run(render_engine.merge_segments(job_id, segment_results))

        logger.info(f"✅ Successfully merged segments for job {job_id}")
        return result

    except Exception as e:
        logger.error(f"❌ Segment merging failed: {str(e)}")

        # Retry once
        if self.request.retries < self.max_retries:
            logger.info(f"🔄 Retrying merge (attempt {self.request.retries + 1}/{self.max_retries})")
            raise self.retry(countdown=5)

        raise


@app.task(name='render.health_check')
def health_check():
    """Health check task for monitoring"""
    return {
        'status': 'healthy',
        'worker': 'gpu_render',
        'engine': 'ready' if render_engine else 'not_initialized'
    }


if __name__ == '__main__':
    # Run worker when executed directly
    app.worker_main([
        'worker',
        '--loglevel=info',
        '--queues=render_queue',
        '--concurrency=1',
        '--pool=prefork',
        '--optimization=fair',
        '--without-gossip',
        '--without-mingle',
        '--without-heartbeat'
    ])