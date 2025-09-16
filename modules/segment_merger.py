"""
Segment Merger for Parallel Rendering
Coordinates and merges video segments from multiple workers
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from typing import Dict, List, Any, Optional
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime
import json

logger = logging.getLogger(__name__)


@dataclass
class SegmentInfo:
    """Information about a rendered segment"""
    worker_id: int
    segment_id: int
    start_time: float
    end_time: float
    file_path: str
    file_size: int
    frames_processed: int
    status: str  # pending, processing, completed, failed
    error_message: Optional[str] = None
    created_at: datetime = None
    completed_at: datetime = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow()


class SegmentMerger:
    """
    Manages merging of video segments from parallel workers
    Ensures correct ordering and handles failures
    """

    def __init__(self, job_id: str, output_dir: Path = None):
        """
        Initialize segment merger

        Args:
            job_id: Unique job identifier
            output_dir: Directory for output files
        """
        self.job_id = job_id
        self.output_dir = output_dir or Path(tempfile.gettempdir()) / "render" / job_id
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.segments: Dict[int, SegmentInfo] = {}
        self.expected_segments = 0
        self.merge_lock = asyncio.Lock()
        self.is_merging = False

    def register_segment(self, segment_info: SegmentInfo) -> None:
        """
        Register a segment for merging

        Args:
            segment_info: Segment information
        """
        self.segments[segment_info.worker_id] = segment_info
        logger.info(f"Registered segment {segment_info.worker_id} for job {self.job_id}")

    def update_segment_status(
        self,
        worker_id: int,
        status: str,
        file_path: str = None,
        error_message: str = None
    ) -> None:
        """
        Update segment status

        Args:
            worker_id: Worker identifier
            status: New status
            file_path: Path to segment file
            error_message: Error message if failed
        """
        if worker_id in self.segments:
            segment = self.segments[worker_id]
            segment.status = status

            if file_path:
                segment.file_path = file_path
                if os.path.exists(file_path):
                    segment.file_size = os.path.getsize(file_path)

            if error_message:
                segment.error_message = error_message

            if status in ['completed', 'failed']:
                segment.completed_at = datetime.utcnow()

            logger.info(f"Updated segment {worker_id} status to {status}")

    def are_all_segments_ready(self) -> bool:
        """
        Check if all segments are ready for merging

        Returns:
            True if all segments are completed
        """
        if len(self.segments) < self.expected_segments:
            return False

        return all(
            seg.status == 'completed' and seg.file_path and os.path.exists(seg.file_path)
            for seg in self.segments.values()
        )

    def get_failed_segments(self) -> List[SegmentInfo]:
        """
        Get list of failed segments

        Returns:
            List of failed segments
        """
        return [
            seg for seg in self.segments.values()
            if seg.status == 'failed'
        ]

    async def merge_segments(self, output_path: str = None) -> Dict[str, Any]:
        """
        Merge all segments into final video

        Args:
            output_path: Path for final video

        Returns:
            Merge result with output path and statistics
        """
        async with self.merge_lock:
            if self.is_merging:
                raise RuntimeError("Merge already in progress")

            self.is_merging = True

        try:
            # Check if all segments are ready
            if not self.are_all_segments_ready():
                failed = self.get_failed_segments()
                if failed:
                    raise RuntimeError(f"Cannot merge: {len(failed)} segments failed")
                else:
                    raise RuntimeError("Not all segments are ready")

            # Sort segments by worker ID to ensure correct order
            sorted_segments = sorted(self.segments.values(), key=lambda s: s.worker_id)

            # Prepare output path
            if not output_path:
                output_path = str(self.output_dir / f"final_{self.job_id}.mp4")

            # Perform merge
            logger.info(f"Starting merge of {len(sorted_segments)} segments")
            result = await self._perform_merge(sorted_segments, output_path)

            logger.info(f"Merge completed: {output_path}")
            return result

        finally:
            self.is_merging = False

    async def _perform_merge(
        self,
        segments: List[SegmentInfo],
        output_path: str
    ) -> Dict[str, Any]:
        """
        Perform actual merge using FFmpeg

        Args:
            segments: List of segments to merge
            output_path: Output file path

        Returns:
            Merge result
        """
        # Create concat file
        concat_file = self.output_dir / f"concat_{self.job_id}.txt"

        with open(concat_file, 'w') as f:
            for segment in segments:
                # Ensure file exists
                if not os.path.exists(segment.file_path):
                    raise FileNotFoundError(f"Segment file not found: {segment.file_path}")

                f.write(f"file '{os.path.abspath(segment.file_path)}'\n")

        # Build FFmpeg command
        cmd = [
            'ffmpeg',
            '-y',  # Overwrite output
            '-f', 'concat',
            '-safe', '0',
            '-i', str(concat_file),
            '-c', 'copy',  # No re-encoding
            '-movflags', '+faststart',
            output_path
        ]

        # Execute merge
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                raise RuntimeError(f"FFmpeg merge failed: {error_msg}")

            # Get output file info
            output_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0

            # Calculate statistics
            total_frames = sum(seg.frames_processed for seg in segments)
            total_duration = max(seg.end_time for seg in segments)

            result = {
                'success': True,
                'output_path': output_path,
                'file_size': output_size,
                'segments_merged': len(segments),
                'total_frames': total_frames,
                'duration': total_duration,
                'merge_time': (segments[-1].completed_at - segments[0].created_at).total_seconds()
            }

            # Clean up concat file
            concat_file.unlink()

            return result

        except Exception as e:
            logger.error(f"Merge failed: {e}")
            raise

    async def cleanup_segments(self) -> None:
        """Clean up segment files after successful merge"""
        for segment in self.segments.values():
            if segment.file_path and os.path.exists(segment.file_path):
                try:
                    os.unlink(segment.file_path)
                    logger.debug(f"Deleted segment file: {segment.file_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete segment: {e}")

    def get_progress(self) -> Dict[str, Any]:
        """
        Get merge progress

        Returns:
            Progress information
        """
        completed = sum(1 for s in self.segments.values() if s.status == 'completed')
        failed = sum(1 for s in self.segments.values() if s.status == 'failed')
        processing = sum(1 for s in self.segments.values() if s.status == 'processing')

        return {
            'job_id': self.job_id,
            'total_segments': self.expected_segments,
            'registered': len(self.segments),
            'completed': completed,
            'processing': processing,
            'failed': failed,
            'ready_to_merge': self.are_all_segments_ready(),
            'is_merging': self.is_merging,
            'segments': [
                {
                    'worker_id': s.worker_id,
                    'status': s.status,
                    'frames': s.frames_processed,
                    'file_size': s.file_size,
                    'error': s.error_message
                }
                for s in self.segments.values()
            ]
        }


class StreamCoordinator:
    """
    Coordinates multiple streaming pipelines for parallel processing
    Ensures synchronization between workers
    """

    def __init__(self, job_id: str, num_workers: int = 4):
        """
        Initialize stream coordinator

        Args:
            job_id: Job identifier
            num_workers: Number of parallel workers
        """
        self.job_id = job_id
        self.num_workers = num_workers
        self.worker_streams: Dict[int, Any] = {}
        self.merger = SegmentMerger(job_id)
        self.merger.expected_segments = num_workers
        self.start_time = datetime.utcnow()

    async def register_worker_stream(self, worker_id: int, stream: Any) -> None:
        """
        Register a worker's streaming pipeline

        Args:
            worker_id: Worker identifier
            stream: Streaming pipeline instance
        """
        self.worker_streams[worker_id] = stream
        logger.info(f"Registered stream for worker {worker_id}")

    async def worker_completed(
        self,
        worker_id: int,
        output_path: str,
        frames_processed: int,
        start_time: float,
        end_time: float
    ) -> None:
        """
        Handle worker completion

        Args:
            worker_id: Worker identifier
            output_path: Path to segment file
            frames_processed: Number of frames processed
            start_time: Segment start time
            end_time: Segment end time
        """
        # Create segment info
        segment_info = SegmentInfo(
            worker_id=worker_id,
            segment_id=worker_id,
            start_time=start_time,
            end_time=end_time,
            file_path=output_path,
            file_size=os.path.getsize(output_path) if os.path.exists(output_path) else 0,
            frames_processed=frames_processed,
            status='completed'
        )

        # Register with merger
        self.merger.register_segment(segment_info)
        self.merger.update_segment_status(worker_id, 'completed', output_path)

        logger.info(f"Worker {worker_id} completed: {frames_processed} frames")

        # Check if all workers are done
        if self.merger.are_all_segments_ready():
            logger.info("All workers completed. Ready to merge.")

    async def worker_failed(self, worker_id: int, error_message: str) -> None:
        """
        Handle worker failure

        Args:
            worker_id: Worker identifier
            error_message: Error message
        """
        segment_info = SegmentInfo(
            worker_id=worker_id,
            segment_id=worker_id,
            start_time=0,
            end_time=0,
            file_path="",
            file_size=0,
            frames_processed=0,
            status='failed',
            error_message=error_message
        )

        self.merger.register_segment(segment_info)
        logger.error(f"Worker {worker_id} failed: {error_message}")

    async def merge_final_video(self, output_path: str = None) -> Dict[str, Any]:
        """
        Merge all segments into final video

        Args:
            output_path: Path for final video

        Returns:
            Merge result
        """
        result = await self.merger.merge_segments(output_path)

        # Add coordination statistics
        result['total_time'] = (datetime.utcnow() - self.start_time).total_seconds()
        result['workers_used'] = self.num_workers

        return result

    def get_status(self) -> Dict[str, Any]:
        """Get coordinator status"""
        return {
            'job_id': self.job_id,
            'num_workers': self.num_workers,
            'active_streams': len(self.worker_streams),
            'merge_progress': self.merger.get_progress(),
            'elapsed_time': (datetime.utcnow() - self.start_time).total_seconds()
        }


class ErrorRecovery:
    """
    Handles error recovery for failed segments
    Implements retry logic and partial recovery
    """

    def __init__(self, max_retries: int = 3):
        """
        Initialize error recovery

        Args:
            max_retries: Maximum retry attempts
        """
        self.max_retries = max_retries
        self.retry_counts: Dict[int, int] = {}
        self.failed_segments: List[SegmentInfo] = []

    async def handle_segment_failure(
        self,
        segment: SegmentInfo,
        retry_callback: Any = None
    ) -> bool:
        """
        Handle segment failure with retry

        Args:
            segment: Failed segment
            retry_callback: Callback for retry attempt

        Returns:
            True if retry successful
        """
        worker_id = segment.worker_id
        self.retry_counts[worker_id] = self.retry_counts.get(worker_id, 0) + 1

        if self.retry_counts[worker_id] <= self.max_retries:
            logger.info(f"Retrying segment {worker_id} (attempt {self.retry_counts[worker_id]})")

            if retry_callback:
                try:
                    await retry_callback(segment)
                    return True
                except Exception as e:
                    logger.error(f"Retry failed: {e}")

        # Max retries exceeded
        self.failed_segments.append(segment)
        logger.error(f"Segment {worker_id} failed after {self.max_retries} retries")
        return False

    def can_recover(self) -> bool:
        """
        Check if recovery is possible

        Returns:
            True if partial recovery possible
        """
        # Can recover if less than 25% of segments failed
        total_segments = len(self.retry_counts)
        failed_count = len(self.failed_segments)

        if total_segments == 0:
            return True

        failure_rate = failed_count / total_segments
        return failure_rate < 0.25

    async def attempt_partial_merge(
        self,
        segments: List[SegmentInfo],
        output_path: str
    ) -> Dict[str, Any]:
        """
        Attempt partial merge with available segments

        Args:
            segments: Available segments
            output_path: Output path

        Returns:
            Partial merge result
        """
        successful_segments = [
            s for s in segments
            if s.status == 'completed' and s.file_path
        ]

        if not successful_segments:
            raise RuntimeError("No successful segments to merge")

        logger.warning(f"Partial merge: {len(successful_segments)}/{len(segments)} segments")

        # Mark result as partial
        result = {
            'success': True,
            'partial': True,
            'segments_merged': len(successful_segments),
            'segments_failed': len(self.failed_segments),
            'coverage': len(successful_segments) / len(segments)
        }

        return result