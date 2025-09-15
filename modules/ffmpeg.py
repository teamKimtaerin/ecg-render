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