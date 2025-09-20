"""
Parallel Worker for GPU-accelerated rendering
Integrates with Phase 2 render engine for actual video processing
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from pathlib import Path

from modules.queue import RenderJob, RenderQueue
from modules.callbacks import CallbackService
from modules.errors import ErrorCodes, ErrorFactory, RenderException, handle_async_render_exception
from render_engine import GPURenderEngine
from src.s3 import S3Service

logger = logging.getLogger(__name__)


class ParallelRenderWorker:
    """Production worker that processes render jobs with actual GPU rendering"""

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
            pool_size: Number of parallel browser instances (compatibility parameter)
        """
        self.queue = queue
        self.config = config
        self.pool_size = pool_size
        self.callback_service = CallbackService()

        # Initialize render engine and S3 service
        self.render_engine = GPURenderEngine()
        self.s3_service = S3Service(bucket=config.get('s3_bucket', 'ecg-rendered-videos'))

        # State
        self.is_running = False
        self.current_job: Optional[RenderJob] = None

        logger.info(f"ParallelRenderWorker initialized with GPU rendering engine")

    async def start(self):
        """Start the worker"""
        self.is_running = True
        logger.info(f"Parallel render worker started (GPU engine active)")

        try:
            while self.is_running:
                try:
                    # Get next job from queue
                    job = await self.queue.get_next_job()

                    if job:
                        self.current_job = job
                        await self.process_job(job)
                        self.current_job = None
                    else:
                        # No jobs available, wait
                        await asyncio.sleep(5)

                except RenderException as e:
                    logger.error(f"Render error in worker: {e}")
                    if self.current_job:
                        await self._handle_job_error(self.current_job, e)
                    await asyncio.sleep(5)

                except Exception as e:
                    logger.error(f"Unexpected worker error: {e}")
                    if self.current_job:
                        error = ErrorFactory.unexpected_error(
                            job_id=self.current_job.job_id,
                            original_error=str(e)
                        )
                        await self._handle_job_error(self.current_job, RenderException(error))
                    await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"Worker failed critically: {e}")

    async def stop(self):
        """Stop the worker"""
        self.is_running = False
        logger.info("Parallel render worker stopping...")

        # Cancel current job if any
        if self.current_job:
            await self.cancel_current_job()

        # Cleanup render engine
        await self.render_engine.cleanup()

    async def cancel_current_job(self):
        """Cancel the currently processing job"""
        if self.current_job:
            job_id = self.current_job.job_id
            self.queue.fail_job(job_id, "Job cancelled by worker", ErrorCodes.JOB_CANCELLED)

            await self.callback_service.send_error(
                self.current_job.callback_url,
                job_id,
                "Job cancelled",
                ErrorCodes.JOB_CANCELLED
            )
            self.current_job = None

    @handle_async_render_exception
    async def process_job(self, job: RenderJob):
        """
        Process a single render job with actual GPU rendering

        Args:
            job: Render job to process
        """
        job_id = job.job_id

        try:
            logger.info(f"🎬 Starting GPU rendering for job {job_id}")

            # Send initial callback
            await self.callback_service.send_progress(
                job.callback_url,
                job_id,
                0,
                "Initializing GPU rendering..."
            )

            # Update job status
            job.status = "processing"
            job.progress = 10
            self.queue.update_job(job)

            # Validate job data
            await self._validate_job_data(job)

            # Download video if needed (S3 URL handling)
            video_path = await self._prepare_video(job.video_url, job_id)

            # Send progress: video prepared
            await self.callback_service.send_progress(
                job.callback_url,
                job_id,
                20,
                "Video prepared, starting rendering..."
            )

            # Create segments for rendering
            segments = await self._create_render_segments(job, video_path)

            # Process segments
            segment_results = []
            total_segments = len(segments)

            for i, segment in enumerate(segments):
                try:
                    # Send progress update
                    progress = 20 + (60 * (i / total_segments))  # 20-80% for segment processing
                    await self.callback_service.send_progress(
                        job.callback_url,
                        job_id,
                        int(progress),
                        f"Rendering segment {i+1}/{total_segments}..."
                    )

                    # Process segment with render engine
                    result = await self.render_engine.render_segment(job_id, segment)
                    segment_results.append(result)

                    logger.info(f"Segment {i+1}/{total_segments} completed for job {job_id}")

                except Exception as e:
                    logger.error(f"Segment {i+1} failed for job {job_id}: {e}")
                    # Continue with other segments or fail the entire job
                    raise RenderException(ErrorFactory.streaming_error(
                        job_id=job_id,
                        pipeline_stage=f"segment_{i+1}"
                    ))

            # Send progress: merging segments
            await self.callback_service.send_progress(
                job.callback_url,
                job_id,
                80,
                "Merging segments..."
            )

            # Merge segments if multiple
            if len(segment_results) > 1:
                final_video_path = await self.render_engine.merge_segments(job_id, segment_results)
            else:
                final_video_path = segment_results[0]['output_path']

            # Send progress: uploading
            await self.callback_service.send_progress(
                job.callback_url,
                job_id,
                90,
                "Uploading to S3..."
            )

            # Upload to S3
            s3_url = await self.s3_service.upload_video(
                file_path=final_video_path,
                key=f"rendered/{job_id}.mp4"
            )

            # Get file information
            file_path = Path(final_video_path)
            file_size = file_path.stat().st_size if file_path.exists() else 0
            duration = self._extract_duration_from_scenario(job.scenario)

            # Complete job
            self.queue.complete_job(job_id)

            # Send completion callback
            await self.callback_service.send_completion(
                job.callback_url,
                job_id,
                s3_url,
                file_size,
                duration
            )

            logger.info(f"✅ Job {job_id} completed successfully")

            # Cleanup temporary files
            await self._cleanup_temp_files(job_id)

        except RenderException:
            # Re-raise render exceptions
            raise
        except Exception as e:
            logger.error(f"Job {job_id} failed with unexpected error: {e}")
            error = ErrorFactory.unexpected_error(
                job_id=job_id,
                original_error=str(e)
            )
            raise RenderException(error)

    async def _handle_job_error(self, job: RenderJob, exception: RenderException):
        """Handle job processing error"""
        job_id = job.job_id
        error = exception.error

        # Mark job as failed
        self.queue.fail_job(job_id, error.message, error.code)

        # Send error callback
        await self.callback_service.send_error(
            job.callback_url,
            job_id,
            error.message,
            error.code,
            error.details
        )

        # Cleanup temporary files
        await self._cleanup_temp_files(job_id)

    async def _validate_job_data(self, job: RenderJob):
        """Validate job data before processing"""
        if not job.video_url:
            raise RenderException(ErrorFactory.invalid_video_format(
                job_id=job.job_id,
                format_info="Missing video URL"
            ))

        if not job.scenario or not isinstance(job.scenario, dict):
            raise RenderException(ErrorFactory.scenario_parse_error(
                job_id=job.job_id,
                parse_details="Invalid or missing scenario data"
            ))

    async def _prepare_video(self, video_url: str, job_id: str) -> str:
        """Prepare video for rendering (download if S3 URL)"""
        try:
            if video_url.startswith('https://') and 's3' in video_url:
                # Download from S3
                local_path = f"/tmp/render/{job_id}/input.mp4"
                await self.s3_service.download_video(video_url, local_path)
                return local_path
            else:
                # Use URL directly
                return video_url
        except Exception as e:
            raise RenderException(ErrorFactory.storage_access_error(
                job_id=job_id,
                operation="video_download"
            ))

    async def _create_render_segments(self, job: RenderJob, video_path: str) -> list:
        """Create render segments from job data"""
        # For now, create a single segment for the entire video
        # This can be extended for parallel processing
        scenario = job.scenario
        options = job.options

        # Extract video metadata
        width = options.get('width', 1920)
        height = options.get('height', 1080)
        fps = options.get('fps', 30)

        # Calculate duration from scenario cues
        duration = self._extract_duration_from_scenario(scenario)
        total_frames = int(duration * fps)

        segment = {
            'worker_id': 0,
            'start_time': 0,
            'end_time': duration,
            'start_frame': 0,
            'end_frame': total_frames,
            'cues': scenario.get('cues', []),
            'scenario_metadata': {
                'width': width,
                'height': height,
                'fps': fps
            },
            'video_path': video_path,
            'scenario': scenario,
            'options': options
        }

        return [segment]

    def _extract_duration_from_scenario(self, scenario: dict) -> float:
        """Extract video duration from scenario cues"""
        cues = scenario.get('cues', [])
        if not cues:
            return 30.0  # Default 30 seconds

        # Find the maximum end time
        max_end = 0
        for cue in cues:
            end_time = cue.get('end', 0)
            max_end = max(max_end, end_time)

        return max(max_end, 1.0)  # At least 1 second

    async def _cleanup_temp_files(self, job_id: str):
        """Clean up temporary files for job"""
        try:
            temp_dir = Path(f"/tmp/render/{job_id}")
            if temp_dir.exists():
                import shutil
                shutil.rmtree(temp_dir)
                logger.debug(f"Cleaned up temp files for job {job_id}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temp files for job {job_id}: {e}")