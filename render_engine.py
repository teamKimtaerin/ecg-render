"""
GPU Render Engine for Celery Worker
Handles actual video segment rendering with Playwright and FFmpeg
"""

import os
import asyncio
import gc
import json
import logging
from typing import Dict, Any
from pathlib import Path

from modules.streaming_pipeline import StreamingPipeline, BackpressureManager
from modules.memory_optimizer import get_memory_optimizer, get_gpu_manager
from utils.browser_manager import BrowserManager
from utils.redis_manager import RedisManager

logger = logging.getLogger(__name__)


class GPURenderEngine:
    """Main rendering engine for processing video segments with streaming pipeline"""

    def __init__(self):
        """Initialize render engine with Phase 2 optimizations"""
        self.browser_manager = BrowserManager()
        self.redis_manager = RedisManager()
        self.memory_optimizer = get_memory_optimizer()
        self.gpu_manager = get_gpu_manager()
        self.backpressure_manager = BackpressureManager()
        self.temp_dir = Path(os.getenv('TEMP_DIR', '/tmp/render'))
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        # Configuration
        self.render_page_url = os.getenv('RENDER_PAGE_URL', 'http://localhost:3001/editor')
        self.s3_bucket = os.getenv('S3_BUCKET', 'ecg-rendered-videos')

        logger.info("GPU Render Engine initialized with streaming pipeline")

    async def render_segment(self, job_id: str, segment: Dict[str, Any]) -> Dict[str, Any]:
        """
        Render a video segment with streaming pipeline optimization

        Args:
            job_id: Job identifier
            segment: Segment data

        Returns:
            Rendering result
        """
        worker_id = segment.get('worker_id', 0)
        start_time = segment.get('start_time', 0)
        end_time = segment.get('end_time', 30)
        start_frame = segment.get('start_frame', 0)
        end_frame = segment.get('end_frame', 900)
        total_frames = end_frame - start_frame
        streaming_pipeline = None

        # Start memory optimizer
        await self.memory_optimizer.start()

        # Update status
        await self.redis_manager.update_worker_status(job_id, worker_id, 'processing', 0)

        # Create temporary output file
        output_path = self.temp_dir / f"segment_{job_id}_{worker_id}.mp4"

        try:
            logger.info(f"🎬 Rendering segment {worker_id}: frames {start_frame}-{end_frame}")

            # Get video metadata
            metadata = segment.get('scenario_metadata', {})
            width = metadata.get('width', 1920)
            height = metadata.get('height', 1080)
            fps = metadata.get('fps', 30)

            # Optimize memory for this segment
            optimization = await self.memory_optimizer.optimize_for_render(total_frames)
            if not optimization['can_proceed']:
                raise RuntimeError(f"Insufficient memory for {total_frames} frames")

            # Apply optimizations
            frame_buffer_size = optimization.get('frame_buffer_size', 60)
            logger.info(f"Memory optimizations: {optimization['optimizations']}")

            # Initialize streaming pipeline with optimized buffer size
            streaming_pipeline = StreamingPipeline(
                output_path=str(output_path),
                width=width,
                height=height,
                fps=fps
            )
            streaming_pipeline.frame_queue.max_size = frame_buffer_size

            # Check GPU availability
            import torch
            use_gpu = torch.cuda.is_available() and self.gpu_manager.check_availability(1024)
            await streaming_pipeline.start(use_gpu=use_gpu)

            # Initialize browser
            browser = await self.browser_manager.create_browser()
            page = await browser.new_page(
                viewport={'width': width, 'height': height},
                device_scale_factor=1
            )

            try:
                # Build render URL with segment data
                render_url = self._build_render_url(segment)
                logger.info(f"Loading render page: {render_url}")

                # Load render page
                await page.goto(render_url, wait_until='networkidle')

                # Wait for video to be ready
                await page.wait_for_selector('video', state='attached', timeout=30000)

                # Inject segment scenario
                await self._inject_scenario(page, segment)

                # Process frames with streaming pipeline
                frames_processed = 0
                frames_dropped = 0

                for frame_num in range(start_frame, end_frame):
                    # Apply backpressure if needed
                    await self.backpressure_manager.apply_backpressure()

                    # Calculate time for this frame
                    frame_time = start_time + ((frame_num - start_frame) / fps)

                    # Seek to frame time
                    await page.evaluate(f'''
                        (function() {{
                            const video = document.querySelector('video');
                            if (video) {{
                                video.currentTime = {frame_time};
                            }}
                        }})()
                    ''')

                    # Wait for frame to render
                    await asyncio.sleep(0.033)  # ~30fps timing

                    # Capture screenshot
                    screenshot_data = await page.screenshot(
                        type='png',
                        full_page=False,
                        clip={'x': 0, 'y': 0, 'width': width, 'height': height}
                    )

                    # Add frame to streaming pipeline
                    success = await streaming_pipeline.add_frame(screenshot_data, frame_num)
                    if not success:
                        frames_dropped += 1
                        logger.warning(f"Frame {frame_num} dropped due to backpressure")

                    frames_processed += 1

                    # Update progress
                    if frames_processed % 30 == 0:  # Every second of video
                        progress = (frames_processed / total_frames) * 100
                        await self.redis_manager.update_worker_status(
                            job_id, worker_id, 'processing', progress
                        )

                        # Log pipeline stats
                        stats = streaming_pipeline.get_stats()
                        memory_stats = self.memory_optimizer.get_optimization_stats()
                        logger.info(
                            f"Worker {worker_id}: {frames_processed}/{total_frames} frames ({progress:.1f}%), "
                            f"Queue: {stats['queue_stats']['queue_size']}/{stats['queue_stats']['max_size']}, "
                            f"Dropped: {frames_dropped}, Memory: {memory_stats['current_memory_mb']:.1f}MB"
                        )

                    # Adaptive garbage collection based on memory pressure
                    if frames_processed % 100 == 0:
                        await self.memory_optimizer.garbage_collect(level=1)
                    elif frames_processed % 300 == 0:
                        await self.memory_optimizer.garbage_collect(level=2)

            finally:
                # Clean up browser
                await page.close()
                await browser.close()

            # Finalize streaming pipeline
            await streaming_pipeline.finalize()

            # Get final statistics
            pipeline_stats = streaming_pipeline.get_stats()
            memory_stats = self.memory_optimizer.get_optimization_stats()

            # Get file size
            file_size = output_path.stat().st_size if output_path.exists() else 0

            # Update completion status
            await self.redis_manager.update_worker_status(job_id, worker_id, 'completed', 100)

            result = {
                'worker_id': worker_id,
                'success': True,
                'frames_processed': frames_processed,
                'frames_dropped': frames_dropped,
                'output_path': str(output_path),
                'file_size': file_size,
                'start_frame': start_frame,
                'end_frame': end_frame,
                'duration': end_time - start_time,
                'drop_rate': pipeline_stats['queue_stats']['drop_rate'],
                'memory_peak_mb': memory_stats['current_memory_mb'],
                'memory_trend': memory_stats['memory_trend']
            }

            logger.info(
                f"✅ Worker {worker_id} completed: {frames_processed} frames, "
                f"{file_size/1024/1024:.2f}MB, {frames_dropped} dropped ({result['drop_rate']*100:.1f}%)"
            )
            return result

        except Exception as e:
            logger.error(f"❌ Worker {worker_id} failed: {str(e)}")

            # Update failure status
            await self.redis_manager.update_worker_status(job_id, worker_id, 'failed', 0)

            # Clean up temp file if exists
            if output_path.exists():
                output_path.unlink()

            raise

        finally:
            # Cleanup streaming pipeline
            if streaming_pipeline:
                try:
                    await streaming_pipeline.finalize()
                except:
                    pass

            # Stop memory optimizer
            await self.memory_optimizer.stop()

            # Final aggressive garbage collection
            await self.memory_optimizer.garbage_collect(level=2)
            gc.collect()

    async def merge_segments(self, job_id: str, segment_results: list) -> Dict[str, Any]:
        """
        Merge rendered segments into final video using SegmentMerger

        Args:
            job_id: Job identifier
            segment_results: List of segment results

        Returns:
            Final video information
        """
        try:
            logger.info(f"🎞️ Merging {len(segment_results)} segments for job {job_id}")

            # Sort segments by worker_id to ensure correct order
            sorted_results = sorted(segment_results, key=lambda x: x.get('worker_id', 0))

            # Get segment file paths
            segment_paths = []
            for result in sorted_results:
                if result.get('success') and result.get('output_path'):
                    path = Path(result['output_path'])
                    if path.exists():
                        segment_paths.append(str(path))

            if not segment_paths:
                raise ValueError("No successful segments to merge")

            logger.info(f"Found {len(segment_paths)} segments to merge")

            # Create output path
            output_path = self.temp_dir / f"final_{job_id}.mp4"

            # Use SegmentMerger for intelligent merging
            from modules.segment_merger import SegmentMerger, SegmentInfo
            merger = SegmentMerger(job_id, output_dir=self.temp_dir)
            merger.expected_segments = len(sorted_results)

            # Register segments
            for result in sorted_results:
                if result.get('success'):
                    segment_info = SegmentInfo(
                        worker_id=result.get('worker_id', 0),
                        segment_id=result.get('worker_id', 0),
                        start_time=result.get('start_frame', 0) / 30.0,  # Assuming 30fps
                        end_time=result.get('end_frame', 0) / 30.0,
                        file_path=result.get('output_path', ''),
                        file_size=result.get('file_size', 0),
                        frames_processed=result.get('frames_processed', 0),
                        status='completed'
                    )
                    merger.register_segment(segment_info)
                    merger.update_segment_status(
                        result.get('worker_id', 0),
                        'completed',
                        file_path=result.get('output_path', '')
                    )

            # Check if all segments are ready
            if not merger.are_all_segments_ready():
                failed_segments = merger.get_failed_segments()
                if failed_segments:
                    logger.warning(f"Found {len(failed_segments)} failed segments")
                    # Attempt error recovery if possible
                    from modules.segment_merger import ErrorRecovery
                    recovery = ErrorRecovery(max_retries=2)
                    if recovery.can_recover():
                        logger.info("Attempting partial merge with available segments")
                    else:
                        raise RuntimeError(f"Too many failed segments: {len(failed_segments)}")

            # Perform merge
            merge_result = await merger.merge_segments(str(output_path))

            if not merge_result.get('success'):
                raise RuntimeError("Segment merge failed")

            # Clean up segments after successful merge
            await merger.cleanup_segments()

            # Upload to S3 if configured
            s3_url = None
            if self.s3_bucket:
                s3_url = await self._upload_to_s3(output_path, job_id)

            # Get final file info
            file_size = output_path.stat().st_size if output_path.exists() else 0

            result = {
                'success': True,
                'output_path': str(output_path),
                'file_size': file_size,
                'segments_merged': len(segment_paths),
                'download_url': s3_url
            }

            logger.info(f"✅ Successfully merged {len(segment_paths)} segments: {file_size/1024/1024:.2f}MB")
            return result

        except Exception as e:
            logger.error(f"❌ Failed to merge segments: {str(e)}")
            raise

    def _build_render_url(self, segment: Dict[str, Any]) -> str:
        """
        Build URL for render page

        Args:
            segment: Segment data

        Returns:
            Render page URL
        """
        base_url = self.render_page_url

        # Add query parameters
        params = [
            f"start={segment.get('start_time', 0)}",
            f"end={segment.get('end_time', 30)}",
            f"worker={segment.get('worker_id', 0)}"
        ]

        return f"{base_url}?{'&'.join(params)}"

    async def _inject_scenario(self, page, segment: Dict[str, Any]):
        """
        Inject scenario data into render page

        Args:
            page: Playwright page object
            segment: Segment data with cues
        """
        cues = segment.get('cues', [])

        # Create scenario object
        scenario = {
            'version': '1.3',
            'cues': cues
        }

        # Inject into page
        await page.evaluate(f'''
            (function() {{
                window.renderScenario = {json.dumps(scenario)};
                if (window.loadScenario) {{
                    window.loadScenario(window.renderScenario);
                }}
            }})()
        ''')

        logger.debug(f"Injected {len(cues)} cues into render page")

    async def _upload_to_s3(self, file_path: Path, job_id: str) -> str:
        """
        Upload file to S3

        Args:
            file_path: Path to file
            job_id: Job identifier

        Returns:
            S3 URL
        """
        try:
            from src.s3 import S3Service
            s3_service = S3Service(self.s3_bucket)

            s3_key = f"rendered/{job_id}/output.mp4"
            s3_url = await s3_service.upload_file(file_path, s3_key)

            logger.info(f"📤 Uploaded to S3: {s3_url}")
            return s3_url

        except Exception as e:
            logger.warning(f"S3 upload failed: {str(e)}")
            return None

    async def cleanup(self):
        """Clean up resources"""
        try:
            # Clean up temp files older than 1 hour
            import time
            current_time = time.time()

            for file_path in self.temp_dir.glob("*"):
                if file_path.is_file():
                    file_age = current_time - file_path.stat().st_mtime
                    if file_age > 3600:  # 1 hour
                        try:
                            file_path.unlink()
                            logger.debug(f"Cleaned up old file: {file_path}")
                        except:
                            pass

        except Exception as e:
            logger.error(f"Cleanup error: {str(e)}")