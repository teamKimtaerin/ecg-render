"""
Parallel Worker for GPU-accelerated rendering
Processes video segments in parallel using multiple browser instances
"""

import os
import asyncio
import logging
import tempfile
import shutil
from typing import Dict, Any, Optional, List
from pathlib import Path
from dataclasses import dataclass
import time

from modules.queue import RenderJob, RenderQueue
from modules.callbacks import CallbackService
from modules.worker_pool import WorkerPoolManager
from modules.segment_optimizer import SegmentOptimizer, VideoSegment
from modules.ffmpeg import FFmpegService
from src.s3 import S3Service

logger = logging.getLogger(__name__)


@dataclass
class SegmentResult:
    """Result from rendering a segment"""
    segment_id: int
    worker_id: int
    frames: List[bytes]
    start_time: float
    end_time: float
    render_time: float


class ParallelRenderWorker:
    """Worker that processes render jobs using parallel segment rendering"""

    def __init__(
        self,
        queue: RenderQueue,
        config: Dict[str, Any],
        pool_size: int = 4
    ):
        """
        Initialize parallel render worker

        Args:
            queue: Render job queue
            config: Worker configuration
            pool_size: Number of parallel browser instances
        """
        self.queue = queue
        self.config = config
        self.pool_size = pool_size

        # Services
        self.s3_service = S3Service(config.get("s3_bucket", "ecg-rendered-videos"))
        self.ffmpeg_service = FFmpegService()
        self.callback_service = CallbackService()
        self.worker_pool = WorkerPoolManager(pool_size=pool_size)
        self.segment_optimizer = SegmentOptimizer()

        # Paths
        self.temp_dir = Path(config.get("temp_dir", "/tmp/render"))
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # State
        self.is_running = False
        self.current_job: Optional[RenderJob] = None
        self.segment_results: Dict[int, SegmentResult] = {}

    async def start(self):
        """Start the parallel worker"""
        self.is_running = True
        logger.info(f"Parallel render worker started with {self.pool_size} browser instances")

        # Initialize worker pool
        await self.worker_pool.initialize()

        try:
            while self.is_running:
                try:
                    # Get next job from queue
                    job = await self.queue.get_next_job()

                    if job:
                        self.current_job = job
                        await self.process_job_parallel(job)
                        self.current_job = None
                    else:
                        # No jobs available, wait
                        await asyncio.sleep(5)

                except Exception as e:
                    logger.error(f"Worker error: {e}")
                    await asyncio.sleep(5)

        finally:
            await self.worker_pool.cleanup()

    async def stop(self):
        """Stop the parallel worker"""
        self.is_running = False
        logger.info("Parallel render worker stopping...")

        if self.current_job:
            await self.cancel_current_job()

        await self.worker_pool.cleanup()

    async def cancel_current_job(self):
        """Cancel the currently processing job"""
        if self.current_job:
            job_id = self.current_job.job_id
            self.queue.fail_job(job_id, "Job cancelled by worker", "CANCELLED")

            await self.callback_service.send_error(
                self.current_job.callback_url,
                job_id,
                "Job cancelled",
                "CANCELLED"
            )

            self.current_job = None
            self.segment_results.clear()

    async def process_job_parallel(self, job: RenderJob):
        """
        Process a render job using parallel segment rendering

        Args:
            job: Render job to process
        """
        job_id = job.job_id
        job_dir = self.temp_dir / job_id
        start_time = time.time()

        try:
            logger.info(f"🚀 Processing job {job_id} with parallel rendering")
            job_dir.mkdir(parents=True, exist_ok=True)

            # Send initial callback
            await self.callback_service.send_progress(
                job.callback_url,
                job_id,
                0,
                "Starting parallel render process"
            )

            # Step 1: Download video (5% progress)
            logger.info(f"📥 Downloading video for job {job_id}")
            video_path = await self.download_video(job.video_url, job_dir)
            await self.update_progress(job, 5, "Video downloaded")

            # Step 2: Get video info
            video_info = await self.ffmpeg_service.get_video_info(str(video_path))
            duration = video_info["duration"]
            fps = video_info["fps"]
            width = job.options.get("width", video_info["width"])
            height = job.options.get("height", video_info["height"])

            logger.info(f"📊 Video info: {duration:.1f}s, {fps}fps, {width}x{height}")

            # Step 3: Segment optimization (10% progress)
            logger.info(f"🔍 Analyzing and segmenting video")
            segments = self.segment_optimizer.smart_segment_split(
                job.scenario,
                duration,
                self.pool_size
            )

            segment_info = self.segment_optimizer.get_segment_info(segments)
            logger.info(f"📊 Created {len(segments)} segments: {segment_info}")

            await self.update_progress(job, 10, f"Video segmented into {len(segments)} parts")

            # Step 4: Parallel segment rendering (10-70% progress)
            logger.info(f"🎬 Starting parallel rendering of {len(segments)} segments")
            render_tasks = []

            for segment in segments:
                task = asyncio.create_task(
                    self.render_segment_async(
                        job,
                        video_path,
                        segment,
                        width,
                        height,
                        fps
                    )
                )
                render_tasks.append(task)

            # Wait for all segments to complete
            segment_results = await asyncio.gather(*render_tasks)

            # Store results in order
            for result in segment_results:
                self.segment_results[result.segment_id] = result

            await self.update_progress(job, 70, "All segments rendered")

            # Step 5: Merge segments and encode (70-90% progress)
            logger.info(f"🎞️ Merging {len(segment_results)} segments")
            output_path = await self.merge_segments_and_encode(
                job_dir,
                segment_results,
                width,
                height,
                fps,
                job.options.get("quality", 90)
            )

            await self.update_progress(job, 90, "Video encoded")

            # Step 6: Upload to S3 (90-100% progress)
            logger.info(f"☁️ Uploading to S3 for job {job_id}")
            s3_key = f"rendered/{job_id}/output.mp4"
            s3_url = await self.s3_service.upload_file(output_path, s3_key)

            # Get file info
            file_size = output_path.stat().st_size
            output_duration = (await self.ffmpeg_service.get_video_info(str(output_path)))["duration"]

            # Complete job
            self.queue.complete_job(job_id)

            # Calculate performance metrics
            total_time = time.time() - start_time
            speedup = duration / total_time if total_time > 0 else 0

            # Send completion callback
            await self.callback_service.send_callback(
                job.callback_url,
                {
                    "job_id": job_id,
                    "status": "completed",
                    "progress": 100,
                    "download_url": s3_url,
                    "file_size": file_size,
                    "duration": output_duration,
                    "render_time": total_time,
                    "speedup": f"{speedup:.1f}x",
                    "segments_processed": len(segment_results),
                    "message": f"Rendering completed in {total_time:.1f}s ({speedup:.1f}x realtime)"
                }
            )

            logger.info(f"✅ Job {job_id} completed in {total_time:.1f}s ({speedup:.1f}x realtime)")

        except asyncio.CancelledError:
            logger.info(f"Job {job_id} was cancelled")
            self.queue.fail_job(job_id, "Job cancelled", "CANCELLED")
            raise

        except Exception as e:
            error_message = str(e)
            logger.error(f"❌ Job {job_id} failed: {error_message}")

            self.queue.fail_job(job_id, error_message)

            await self.callback_service.send_error(
                job.callback_url,
                job_id,
                error_message,
                "RENDER_ERROR"
            )

        finally:
            # Cleanup
            self.segment_results.clear()
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)

    async def render_segment_async(
        self,
        job: RenderJob,
        video_path: Path,
        segment: VideoSegment,
        width: int,
        height: int,
        fps: float
    ) -> SegmentResult:
        """
        Render a single segment asynchronously

        Args:
            job: Render job
            video_path: Path to video file
            segment: Segment to render
            width: Video width
            height: Video height
            fps: Frame rate

        Returns:
            SegmentResult with rendered frames
        """
        segment_start = time.time()

        # Get available worker
        worker = await self.worker_pool.get_available_worker(timeout=60)
        if not worker:
            raise Exception(f"No available worker for segment {segment.segment_id}")

        try:
            logger.info(f"🎬 Worker {worker.worker_id} rendering segment {segment.segment_id} "
                       f"({segment.start_time:.1f}-{segment.end_time:.1f}s)")

            # Render segment
            frames = await self.worker_pool.render_segment(
                worker,
                str(video_path),
                {
                    "segment_id": segment.segment_id,
                    "start_time": segment.start_time,
                    "end_time": segment.end_time,
                    "cues": segment.cues
                },
                job.scenario,
                width,
                height
            )

            render_time = time.time() - segment_start

            # Update progress
            segments_complete = len(self.segment_results) + 1
            progress = 10 + int((segments_complete / self.pool_size) * 60)
            await self.update_progress(
                job,
                min(progress, 70),
                f"Segment {segment.segment_id} complete ({segments_complete}/{self.pool_size})"
            )

            logger.info(f"✅ Segment {segment.segment_id} rendered in {render_time:.1f}s "
                       f"({len(frames)} frames)")

            return SegmentResult(
                segment_id=segment.segment_id,
                worker_id=worker.worker_id,
                frames=frames,
                start_time=segment.start_time,
                end_time=segment.end_time,
                render_time=render_time
            )

        finally:
            # Release worker back to pool
            await self.worker_pool.release_worker(worker)

    async def merge_segments_and_encode(
        self,
        job_dir: Path,
        segment_results: List[SegmentResult],
        width: int,
        height: int,
        fps: float,
        quality: int
    ) -> Path:
        """
        Merge rendered segments and encode final video

        Args:
            job_dir: Job directory
            segment_results: List of segment results
            width: Video width
            height: Video height
            fps: Frame rate
            quality: Encoding quality

        Returns:
            Path to output video
        """
        # Sort segments by ID to ensure correct order
        sorted_results = sorted(segment_results, key=lambda r: r.segment_id)

        # Create frames directory
        frames_dir = job_dir / "frames"
        frames_dir.mkdir(exist_ok=True)

        # Write all frames in order
        frame_counter = 0
        for result in sorted_results:
            for frame_data in result.frames:
                frame_path = frames_dir / f"frame_{frame_counter:06d}.png"
                with open(frame_path, 'wb') as f:
                    f.write(frame_data)
                frame_counter += 1

        logger.info(f"📝 Wrote {frame_counter} frames to disk")

        # Encode video with FFmpeg
        output_path = job_dir / "output.mp4"
        await self.ffmpeg_service.encode_video_gpu(
            frames_dir,
            output_path,
            fps,
            quality,
            width,
            height
        )

        return output_path

    async def download_video(self, video_url: str, job_dir: Path) -> Path:
        """Download video from URL or S3"""
        video_path = job_dir / "input_video.mp4"

        if video_url.startswith("s3://"):
            # Download from S3
            s3_key = video_url.replace("s3://", "").split("/", 1)[1]
            await self.s3_service.download_file(s3_key, video_path)
        else:
            # Download from HTTP URL
            await self.download_http_file(video_url, video_path)

        return video_path

    async def download_http_file(self, url: str, output_path: Path):
        """Download file from HTTP URL"""
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                with open(output_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)

    async def update_progress(self, job: RenderJob, progress: int, message: str = ""):
        """Update job progress and send callback"""
        job.progress = progress
        self.queue.update_job(job)

        await self.callback_service.send_progress(
            job.callback_url,
            job.job_id,
            progress,
            message
        )

    def get_worker_status(self) -> Dict[str, Any]:
        """Get current worker status"""
        return {
            "type": "parallel",
            "pool_size": self.pool_size,
            "current_job": self.current_job.job_id if self.current_job else None,
            "segments_processed": len(self.segment_results),
            "pool_status": self.worker_pool.get_pool_status()
        }