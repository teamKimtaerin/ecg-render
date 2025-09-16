"""
Streaming Pipeline for Real-time Frame Processing
Handles frame queuing, streaming to FFmpeg, and memory management
"""

import asyncio
import logging
import time
import gc
import psutil
from typing import Optional, Dict, Any, AsyncGenerator
from pathlib import Path
from dataclasses import dataclass
from collections import deque
import subprocess

logger = logging.getLogger(__name__)


@dataclass
class FrameData:
    """Frame data container"""
    frame_number: int
    timestamp: float
    data: bytes
    size: int


class AsyncFrameQueue:
    """
    Asynchronous frame queue with memory management
    Implements backpressure and frame dropping policies
    """

    def __init__(self, max_size: int = 60, max_memory_mb: int = 360):
        """
        Initialize frame queue

        Args:
            max_size: Maximum number of frames in queue (default: 60 = 2 seconds at 30fps)
            max_memory_mb: Maximum memory usage in MB (default: 360MB)
        """
        self.max_size = max_size
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.queue = asyncio.Queue(maxsize=max_size)
        self.current_memory = 0
        self.dropped_frames = 0
        self.processed_frames = 0
        self.lock = asyncio.Lock()

    async def put_frame(self, frame_data: bytes, frame_number: int) -> bool:
        """
        Add frame to queue with backpressure handling

        Args:
            frame_data: Frame PNG data
            frame_number: Frame sequence number

        Returns:
            Success status
        """
        frame_size = len(frame_data)

        # Check memory limit
        if self.current_memory + frame_size > self.max_memory_bytes:
            logger.warning(f"Memory limit exceeded. Dropping frame {frame_number}")
            self.dropped_frames += 1
            return False

        # Create frame object
        frame = FrameData(
            frame_number=frame_number,
            timestamp=time.time(),
            data=frame_data,
            size=frame_size
        )

        try:
            # Try to add to queue (non-blocking)
            self.queue.put_nowait(frame)
            async with self.lock:
                self.current_memory += frame_size
            return True

        except asyncio.QueueFull:
            # Queue is full, apply drop policy
            if await self._apply_drop_policy():
                # Retry after dropping old frame
                await self.queue.put(frame)
                async with self.lock:
                    self.current_memory += frame_size
                return True
            else:
                self.dropped_frames += 1
                return False

    async def get_frame(self) -> Optional[FrameData]:
        """
        Get next frame from queue

        Returns:
            Frame data or None if queue is empty
        """
        try:
            frame = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            async with self.lock:
                self.current_memory -= frame.size
            self.processed_frames += 1
            return frame
        except asyncio.TimeoutError:
            return None

    async def _apply_drop_policy(self) -> bool:
        """
        Drop oldest frame when queue is full

        Returns:
            Success status
        """
        try:
            # Get and drop oldest frame
            old_frame = self.queue.get_nowait()
            async with self.lock:
                self.current_memory -= old_frame.size
            self.dropped_frames += 1
            logger.debug(f"Dropped old frame {old_frame.frame_number} due to queue pressure")
            return True
        except asyncio.QueueEmpty:
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics"""
        return {
            "queue_size": self.queue.qsize(),
            "max_size": self.max_size,
            "current_memory_mb": self.current_memory / (1024 * 1024),
            "max_memory_mb": self.max_memory_bytes / (1024 * 1024),
            "processed_frames": self.processed_frames,
            "dropped_frames": self.dropped_frames,
            "drop_rate": self.dropped_frames / max(1, self.processed_frames + self.dropped_frames)
        }


class StreamingPipeline:
    """
    Real-time streaming pipeline to FFmpeg
    Handles frame streaming without disk I/O
    """

    def __init__(self, output_path: str, width: int = 1920, height: int = 1080, fps: int = 30):
        """
        Initialize streaming pipeline

        Args:
            output_path: Output video file path
            width: Video width
            height: Video height
            fps: Frame rate
        """
        self.output_path = output_path
        self.width = width
        self.height = height
        self.fps = fps
        self.process: Optional[asyncio.subprocess.Process] = None
        self.frame_queue = AsyncFrameQueue()
        self.is_running = False
        self.writer_task: Optional[asyncio.Task] = None

    async def start(self, use_gpu: bool = True) -> None:
        """
        Start FFmpeg streaming process

        Args:
            use_gpu: Enable GPU encoding if available
        """
        # Build FFmpeg command
        cmd = self._build_ffmpeg_command(use_gpu)

        # Start FFmpeg process
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        self.is_running = True

        # Start frame writer task
        self.writer_task = asyncio.create_task(self._frame_writer())

        logger.info(f"Streaming pipeline started: {self.output_path}")

    def _build_ffmpeg_command(self, use_gpu: bool) -> list:
        """Build FFmpeg command with optimal settings"""
        cmd = [
            'ffmpeg',
            '-y',  # Overwrite output
            '-f', 'image2pipe',  # Image pipe input
            '-vcodec', 'png',  # PNG decoder
            '-framerate', str(self.fps),
            '-i', '-',  # Read from stdin
        ]

        if use_gpu and self._check_gpu_available():
            # NVIDIA GPU encoding
            cmd.extend([
                '-c:v', 'h264_nvenc',
                '-preset', 'p4',  # Balanced quality/speed
                '-rc', 'vbr',
                '-cq', '23',
                '-b:v', '0',
                '-maxrate', '5M',
                '-bufsize', '10M',
                '-gpu', '0',  # Use first GPU
            ])
            logger.info("Using GPU encoding (h264_nvenc)")
        else:
            # CPU encoding with optimization
            cmd.extend([
                '-c:v', 'libx264',
                '-preset', 'faster',
                '-crf', '23',
                '-tune', 'zerolatency',
                '-x264-params', 'keyint=60:min-keyint=30:scenecut=0',
            ])
            logger.info("Using CPU encoding (libx264)")

        # Output settings
        cmd.extend([
            '-pix_fmt', 'yuv420p',
            '-vf', f'scale={self.width}:{self.height}:flags=lanczos',
            '-movflags', '+faststart',
            '-f', 'mp4',
            self.output_path
        ])

        return cmd

    def _check_gpu_available(self) -> bool:
        """Check if GPU encoding is available"""
        try:
            result = subprocess.run(
                ['ffmpeg', '-hide_banner', '-encoders'],
                capture_output=True,
                text=True,
                timeout=5
            )
            return 'h264_nvenc' in result.stdout
        except:
            return False

    async def _frame_writer(self) -> None:
        """Background task to write frames to FFmpeg"""
        if not self.process or not self.process.stdin:
            logger.error("FFmpeg process not initialized")
            return

        try:
            while self.is_running:
                # Get frame from queue
                frame = await self.frame_queue.get_frame()

                if frame:
                    try:
                        # Write to FFmpeg stdin
                        self.process.stdin.write(frame.data)
                        await self.process.stdin.drain()

                        # Free memory immediately
                        del frame.data
                        gc.collect()

                    except Exception as e:
                        logger.error(f"Error writing frame: {e}")
                        break

                # Small delay to prevent CPU spinning
                await asyncio.sleep(0.001)

        except Exception as e:
            logger.error(f"Frame writer error: {e}")

        finally:
            logger.info("Frame writer task ended")

    async def add_frame(self, frame_data: bytes, frame_number: int) -> bool:
        """
        Add frame to streaming pipeline

        Args:
            frame_data: PNG frame data
            frame_number: Frame sequence number

        Returns:
            Success status
        """
        if not self.is_running:
            logger.error("Pipeline not running")
            return False

        return await self.frame_queue.put_frame(frame_data, frame_number)

    async def finalize(self) -> str:
        """
        Finalize streaming and close FFmpeg

        Returns:
            Output file path
        """
        self.is_running = False

        # Wait for writer task to complete
        if self.writer_task:
            await self.writer_task

        # Close FFmpeg stdin
        if self.process and self.process.stdin:
            self.process.stdin.close()
            await self.process.stdin.wait_closed()

        # Wait for FFmpeg to finish
        if self.process:
            stdout, stderr = await self.process.communicate()

            if self.process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(f"FFmpeg error: {error_msg}")
                raise RuntimeError(f"FFmpeg failed with code {self.process.returncode}")

        # Log statistics
        stats = self.frame_queue.get_stats()
        logger.info(f"Pipeline stats: {stats}")

        logger.info(f"Streaming pipeline completed: {self.output_path}")
        return self.output_path

    def get_stats(self) -> Dict[str, Any]:
        """Get pipeline statistics"""
        return {
            "queue_stats": self.frame_queue.get_stats(),
            "is_running": self.is_running,
            "output_path": self.output_path
        }


class BackpressureManager:
    """
    Manages backpressure in the streaming pipeline
    Monitors memory and adjusts processing rate
    """

    def __init__(self, memory_threshold_mb: int = 500, cpu_threshold: float = 80.0):
        """
        Initialize backpressure manager

        Args:
            memory_threshold_mb: Memory threshold in MB
            cpu_threshold: CPU usage threshold in percent
        """
        self.memory_threshold_bytes = memory_threshold_mb * 1024 * 1024
        self.cpu_threshold = cpu_threshold
        self.slowdown_factor = 1.0
        self.last_check = time.time()

    async def check_pressure(self) -> float:
        """
        Check system pressure and return slowdown factor

        Returns:
            Slowdown factor (1.0 = normal, >1.0 = slow down)
        """
        current_time = time.time()

        # Check every second
        if current_time - self.last_check < 1.0:
            return self.slowdown_factor

        self.last_check = current_time

        # Get system stats
        process = psutil.Process()
        memory_info = process.memory_info()
        cpu_percent = psutil.cpu_percent(interval=0.1)

        # Calculate pressure
        memory_pressure = memory_info.rss / self.memory_threshold_bytes
        cpu_pressure = cpu_percent / self.cpu_threshold

        # Adjust slowdown factor
        max_pressure = max(memory_pressure, cpu_pressure)

        if max_pressure > 1.5:
            self.slowdown_factor = min(3.0, self.slowdown_factor * 1.2)
            logger.warning(f"High pressure detected. Slowdown: {self.slowdown_factor:.1f}x")
        elif max_pressure > 1.0:
            self.slowdown_factor = min(2.0, self.slowdown_factor * 1.1)
        elif max_pressure < 0.7:
            self.slowdown_factor = max(1.0, self.slowdown_factor * 0.9)

        return self.slowdown_factor

    async def apply_backpressure(self) -> None:
        """Apply backpressure by adding delay"""
        factor = await self.check_pressure()
        if factor > 1.0:
            delay = 0.033 * (factor - 1.0)  # Base delay of 33ms (30fps)
            await asyncio.sleep(delay)