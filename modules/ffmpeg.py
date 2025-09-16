"""
FFmpeg Service for video encoding with GPU acceleration
"""

import os
import asyncio
import subprocess
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class FFmpegStreamingService:
    """FFmpeg service with streaming support for real-time encoding"""

    def __init__(self):
        """Initialize streaming service"""
        self.process = None
        self.output_path = None
        self.gpu_enabled = self.check_gpu_support()

    def check_gpu_support(self) -> bool:
        """Check if NVENC GPU encoding is available"""
        try:
            result = subprocess.run(
                ['ffmpeg', '-hide_banner', '-encoders'],
                capture_output=True,
                text=True,
                timeout=5
            )
            return 'h264_nvenc' in result.stdout
        except Exception as e:
            logger.warning(f"GPU encoding not available: {e}")
            return False

    async def start_streaming(
        self,
        output_path: str,
        width: int = 1920,
        height: int = 1080,
        fps: float = 30,
        quality: int = 90
    ) -> None:
        """
        Start FFmpeg streaming process

        Args:
            output_path: Output file path
            width: Video width
            height: Video height
            fps: Frame rate
            quality: Encoding quality (0-100)
        """
        self.output_path = output_path

        # Build FFmpeg command
        cmd = [
            'ffmpeg',
            '-y',  # Overwrite output
            '-f', 'image2pipe',  # Image pipe input
            '-vcodec', 'png',  # PNG decoder
            '-framerate', str(fps),  # Input framerate
            '-i', '-',  # Read from stdin
        ]

        # Choose encoder based on GPU availability
        if self.gpu_enabled and os.getenv('ENABLE_GPU_ENCODING', 'true').lower() == 'true':
            # NVIDIA GPU encoding
            cq_value = int(51 - (quality / 100) * 51)  # Map quality to CQ
            cmd.extend([
                '-c:v', 'h264_nvenc',  # NVIDIA encoder
                '-preset', 'p4',  # Balanced preset
                '-rc', 'vbr',  # Variable bitrate
                '-cq', str(cq_value),  # Constant quality
                '-b:v', '0',  # Let CQ control bitrate
                '-maxrate', '5M',  # Max bitrate
                '-bufsize', '10M',  # Buffer size
            ])
            logger.info("Using NVIDIA GPU encoding (h264_nvenc)")
        else:
            # CPU encoding (optimized for speed)
            crf_value = int(51 - (quality / 100) * 51)  # Map quality to CRF
            cmd.extend([
                '-c:v', 'libx264',  # CPU encoder
                '-preset', 'faster',  # Fast encoding
                '-crf', str(crf_value),  # Constant rate factor
                '-tune', 'zerolatency',  # Low latency
            ])
            logger.info("Using CPU encoding (libx264)")

        # Common output settings
        cmd.extend([
            '-pix_fmt', 'yuv420p',  # Pixel format for compatibility
            '-vf', f'scale={width}:{height}:flags=lanczos',  # High quality scaling
            '-movflags', '+faststart',  # Optimize for streaming
            '-f', 'mp4',  # Output format
            output_path
        ])

        # Start FFmpeg process
        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            logger.info(f"FFmpeg streaming started: {output_path}")
        except Exception as e:
            logger.error(f"Failed to start FFmpeg: {str(e)}")
            raise

    async def write_frame(self, frame_data: bytes) -> None:
        """
        Write frame data to FFmpeg

        Args:
            frame_data: PNG frame data
        """
        if not self.process or not self.process.stdin:
            raise RuntimeError("FFmpeg process not started")

        try:
            self.process.stdin.write(frame_data)
            await self.process.stdin.drain()
        except Exception as e:
            logger.error(f"Failed to write frame: {str(e)}")
            raise

    async def finalize(self) -> str:
        """
        Finalize streaming and close FFmpeg

        Returns:
            Output file path
        """
        if not self.process:
            raise RuntimeError("FFmpeg process not started")

        try:
            # Close stdin
            if self.process.stdin:
                self.process.stdin.close()
                await self.process.stdin.wait_closed()

            # Wait for process to finish
            stdout, stderr = await self.process.communicate()

            # Check for errors
            if self.process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(f"FFmpeg error: {error_msg}")
                raise RuntimeError(f"FFmpeg failed with code {self.process.returncode}")

            logger.info(f"FFmpeg streaming completed: {self.output_path}")
            return self.output_path

        except Exception as e:
            logger.error(f"Failed to finalize FFmpeg: {str(e)}")
            raise
        finally:
            self.process = None


class FFmpegService:
    """Service for FFmpeg operations with GPU support"""

    def __init__(self, gpu_enabled: bool = True):
        """Initialize FFmpeg service"""
        self.gpu_enabled = gpu_enabled and self.check_gpu_support()

    def check_gpu_support(self) -> bool:
        """Check if NVENC GPU encoding is available"""
        try:
            result = subprocess.run(
                ['ffmpeg', '-hide_banner', '-encoders'],
                capture_output=True,
                text=True,
                timeout=5
            )
            return 'h264_nvenc' in result.stdout
        except Exception as e:
            logger.warning(f"GPU encoding not available: {e}")
            return False

    async def get_video_info(self, video_path: str) -> Dict[str, Any]:
        """Get video information using ffprobe"""
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                str(video_path)
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise Exception(f"ffprobe failed: {stderr.decode()}")

            data = json.loads(stdout.decode())

            # Extract relevant info
            video_stream = next(
                (s for s in data['streams'] if s['codec_type'] == 'video'),
                None
            )

            if not video_stream:
                raise Exception("No video stream found")

            # Calculate FPS
            fps_str = video_stream.get('r_frame_rate', '30/1')
            if '/' in fps_str:
                num, den = map(int, fps_str.split('/'))
                fps = num / den if den != 0 else 30
            else:
                fps = float(fps_str)

            return {
                'duration': float(data['format'].get('duration', 0)),
                'width': int(video_stream.get('width', 1920)),
                'height': int(video_stream.get('height', 1080)),
                'fps': fps,
                'codec': video_stream.get('codec_name', 'unknown'),
                'bitrate': int(data['format'].get('bit_rate', 0)),
                'size': int(data['format'].get('size', 0))
            }

        except Exception as e:
            logger.error(f"Failed to get video info: {e}")
            raise

    async def encode_video_gpu(
        self,
        frames_dir: Path,
        output_path: Path,
        fps: float = 30,
        quality: int = 90,
        width: int = 1920,
        height: int = 1080
    ) -> None:
        """Encode video using GPU acceleration (NVENC)"""

        # Build FFmpeg command
        if self.gpu_enabled:
            cmd = self._build_gpu_encode_cmd(
                frames_dir, output_path, fps, quality, width, height
            )
        else:
            cmd = self._build_cpu_encode_cmd(
                frames_dir, output_path, fps, quality, width, height
            )

        logger.info(f"Encoding video with command: {' '.join(cmd)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise Exception(f"FFmpeg encoding failed: {stderr.decode()}")

            logger.info(f"Video encoded successfully: {output_path}")

        except Exception as e:
            logger.error(f"Failed to encode video: {e}")
            raise

    def _build_gpu_encode_cmd(
        self,
        frames_dir: Path,
        output_path: Path,
        fps: float,
        quality: int,
        width: int,
        height: int
    ) -> List[str]:
        """Build FFmpeg command for GPU encoding"""

        # Map quality (0-100) to CQ value (51-0, lower is better)
        cq_value = int(51 - (quality / 100) * 51)

        return [
            'ffmpeg',
            '-y',  # Overwrite output
            '-framerate', str(fps),
            '-pattern_type', 'glob',
            '-i', str(frames_dir / 'frame_*.png'),
            '-c:v', 'h264_nvenc',  # NVIDIA GPU encoder
            '-preset', 'p7',  # Highest quality preset
            '-rc', 'vbr',  # Variable bitrate
            '-cq', str(cq_value),  # Constant quality
            '-b:v', '10M',  # Target bitrate
            '-maxrate', '15M',  # Max bitrate
            '-bufsize', '20M',  # Buffer size
            '-pix_fmt', 'yuv420p',  # Pixel format
            '-vf', f'scale={width}:{height}:flags=lanczos',  # High-quality scaling
            '-movflags', '+faststart',  # Optimize for streaming
            str(output_path)
        ]

    def _build_cpu_encode_cmd(
        self,
        frames_dir: Path,
        output_path: Path,
        fps: float,
        quality: int,
        width: int,
        height: int
    ) -> List[str]:
        """Build FFmpeg command for CPU encoding"""

        # Map quality (0-100) to CRF value (51-0, lower is better)
        crf_value = int(51 - (quality / 100) * 51)

        return [
            'ffmpeg',
            '-y',  # Overwrite output
            '-framerate', str(fps),
            '-pattern_type', 'glob',
            '-i', str(frames_dir / 'frame_*.png'),
            '-c:v', 'libx264',  # CPU encoder
            '-preset', 'medium',  # Balance speed/quality
            '-crf', str(crf_value),  # Constant rate factor
            '-pix_fmt', 'yuv420p',  # Pixel format
            '-vf', f'scale={width}:{height}:flags=lanczos',  # High-quality scaling
            '-movflags', '+faststart',  # Optimize for streaming
            str(output_path)
        ]