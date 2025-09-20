"""
Segment Merger for Parallel Rendering
Simplified coordinator for merging video segments
"""

import asyncio
import logging
import os
import subprocess
from typing import Dict, List, Any, Optional
from pathlib import Path
from dataclasses import dataclass

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


class SegmentMerger:
    """Simplified segment merger for parallel rendering"""

    def __init__(self, job_id: str, output_dir: Path = None):
        """Initialize segment merger"""
        self.job_id = job_id
        self.output_dir = output_dir or Path("/tmp/render")
        self.segments: Dict[int, SegmentInfo] = {}
        self.expected_segments = 4

    def register_segment(self, segment_info: SegmentInfo) -> None:
        """Register a segment"""
        self.segments[segment_info.worker_id] = segment_info
        logger.debug(f"Registered segment {segment_info.worker_id}: {segment_info.status}")

    def update_segment_status(self, worker_id: int, status: str, file_path: str = None) -> None:
        """Update segment status"""
        if worker_id in self.segments:
            self.segments[worker_id].status = status
            if file_path:
                self.segments[worker_id].file_path = file_path

    def are_all_segments_ready(self) -> bool:
        """Check if all segments are ready for merging"""
        completed_segments = [
            seg for seg in self.segments.values()
            if seg.status == 'completed' and os.path.exists(seg.file_path)
        ]
        return len(completed_segments) >= self.expected_segments

    def get_failed_segments(self) -> List[SegmentInfo]:
        """Get list of failed segments"""
        return [seg for seg in self.segments.values() if seg.status == 'failed']

    async def merge_segments(self, output_path: str) -> Dict[str, Any]:
        """Merge segments into final video"""
        try:
            # Get completed segments in order
            completed_segments = []
            for worker_id in sorted(self.segments.keys()):
                segment = self.segments[worker_id]
                if segment.status == 'completed' and os.path.exists(segment.file_path):
                    completed_segments.append(segment.file_path)

            if not completed_segments:
                raise RuntimeError("No completed segments to merge")

            logger.info(f"Merging {len(completed_segments)} segments")

            # Create concat file list
            concat_file = self.output_dir / f"concat_{self.job_id}.txt"
            with open(concat_file, 'w') as f:
                for segment_path in completed_segments:
                    f.write(f"file '{segment_path}'\n")

            # Build FFmpeg command
            cmd = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_file),
                '-c', 'copy',
                output_path
            ]

            # Execute merge
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(f"FFmpeg merge failed: {error_msg}")
                raise RuntimeError(f"Merge failed: {error_msg}")

            # Clean up concat file
            concat_file.unlink(missing_ok=True)

            return {
                'success': True,
                'output_path': output_path,
                'segments_merged': len(completed_segments)
            }

        except Exception as e:
            logger.error(f"Merge failed: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

    async def cleanup_segments(self) -> None:
        """Clean up segment files"""
        for segment in self.segments.values():
            if segment.file_path and os.path.exists(segment.file_path):
                try:
                    os.unlink(segment.file_path)
                    logger.debug(f"Cleaned up segment: {segment.file_path}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup segment {segment.file_path}: {e}")


class ErrorRecovery:
    """Simple error recovery for failed segments"""

    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries
        self.retry_count = 0

    def can_recover(self) -> bool:
        """Check if recovery is possible"""
        return self.retry_count < self.max_retries

    def attempt_recovery(self) -> bool:
        """Attempt recovery"""
        if self.can_recover():
            self.retry_count += 1
            return True
        return False