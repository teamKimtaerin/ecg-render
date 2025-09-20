"""
FFmpeg Service for video encoding with GPU acceleration
Consolidated service supporting both streaming and batch encoding
"""

import os
import asyncio
import subprocess
import logging
from typing import List

logger = logging.getLogger(__name__)


class FFmpegService:
    """Unified FFmpeg service for streaming and batch encoding"""

    def __init__(self):
        """Initialize FFmpeg service"""
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
        Start FFmpeg streaming process for real-time encoding

        Args:
            output_path: Output file path
            width: Video width
            height: Video height
            fps: Frame rate
            quality: Encoding quality (0-100)
        """
        self.output_path = output_path
        cmd = self._build_streaming_cmd(output_path, width, height, fps, quality)

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
            if self.process.stdin:
                self.process.stdin.close()
                await self.process.stdin.wait_closed()

            stdout, stderr = await self.process.communicate()

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



    def _build_streaming_cmd(
        self,
        output_path: str,
        width: int,
        height: int,
        fps: float,
        quality: int
    ) -> List[str]:
        """Build FFmpeg command for streaming mode"""
        cmd = [
            'ffmpeg', '-y',
            '-f', 'image2pipe',
            '-vcodec', 'png',
            '-framerate', str(fps),
            '-i', '-'
        ]

        if self.gpu_enabled and os.getenv('ENABLE_GPU_ENCODING', 'true').lower() == 'true':
            cq_value = int(51 - (quality / 100) * 51)
            cmd.extend([
                '-c:v', 'h264_nvenc',
                '-preset', 'p4',
                '-rc', 'vbr',
                '-cq', str(cq_value),
                '-b:v', '0',
                '-maxrate', '5M',
                '-bufsize', '10M'
            ])
            logger.info("Using NVIDIA GPU encoding (h264_nvenc)")
        else:
            crf_value = int(51 - (quality / 100) * 51)
            cmd.extend([
                '-c:v', 'libx264',
                '-preset', 'faster',
                '-crf', str(crf_value),
                '-tune', 'zerolatency'
            ])
            logger.info("Using CPU encoding (libx264)")

        cmd.extend([
            '-pix_fmt', 'yuv420p',
            '-vf', f'scale={width}:{height}:flags=lanczos',
            '-movflags', '+faststart',
            '-f', 'mp4',
            output_path
        ])
        return cmd

