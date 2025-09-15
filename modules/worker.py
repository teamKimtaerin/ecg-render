"""
GPU Render Worker
Handles the actual rendering process using MotionText and FFmpeg
"""

import os
import json
import asyncio
import tempfile
import shutil
from typing import Dict, Any, Optional
from pathlib import Path
import logging
from playwright.async_api import async_playwright
import subprocess

from modules.queue import RenderJob, RenderQueue
from modules.callbacks import CallbackService
from src.s3 import S3Service
from modules.ffmpeg import FFmpegService

logger = logging.getLogger(__name__)


class RenderWorker:
    """Worker process for GPU rendering"""

    def __init__(self, queue: RenderQueue, config: Dict[str, Any]):
        """Initialize render worker"""
        self.queue = queue
        self.config = config
        self.s3_service = S3Service(config.get("s3_bucket", "ecg-rendered-videos"))
        self.ffmpeg_service = FFmpegService()
        self.callback_service = CallbackService()
        self.temp_dir = Path(config.get("temp_dir", "/tmp/render"))
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.is_running = False
        self.current_job: Optional[RenderJob] = None

    async def start(self):
        """Start worker loop"""
        self.is_running = True
        logger.info("Render worker started")

        while self.is_running:
            try:
                # Get next job from queue
                job = await self.queue.get_next_job()

                if job:
                    self.current_job = job
                    await self.process_job(job)
                    self.current_job = None
                else:
                    # No jobs available, wait a bit
                    await asyncio.sleep(5)

            except Exception as e:
                logger.error(f"Worker error: {e}")
                await asyncio.sleep(5)

    async def stop(self):
        """Stop worker"""
        self.is_running = False
        logger.info("Render worker stopping...")

        # Cancel current job if any
        if self.current_job:
            await self.cancel_current_job()

    async def cancel_current_job(self):
        """Cancel the currently processing job"""
        if self.current_job:
            job_id = self.current_job.job_id
            self.queue.fail_job(job_id, "Job cancelled by worker", "CANCELLED")
            await self.callback_service.send_callback(
                self.current_job.callback_url,
                {
                    "job_id": job_id,
                    "status": "cancelled",
                    "error_message": "Job cancelled",
                    "error_code": "CANCELLED"
                }
            )
            self.current_job = None

    async def process_job(self, job: RenderJob):
        """Process a single render job"""
        job_id = job.job_id
        job_dir = self.temp_dir / job_id

        try:
            logger.info(f"Processing job {job_id}")
            job_dir.mkdir(parents=True, exist_ok=True)

            # Send initial callback
            await self.callback_service.send_callback(
                job.callback_url,
                {
                    "job_id": job_id,
                    "status": "processing",
                    "progress": 0,
                    "message": "Starting render process"
                }
            )

            # Step 1: Download video (10% progress)
            logger.info(f"Downloading video for job {job_id}")
            video_path = await self.download_video(job.video_url, job_dir)
            await self.update_progress(job, 10, "Video downloaded")

            # Step 2: Get video info
            video_info = await self.ffmpeg_service.get_video_info(video_path)
            duration = video_info["duration"]
            fps = video_info["fps"]
            width = job.options.get("width", video_info["width"])
            height = job.options.get("height", video_info["height"])

            # Step 3: Render MotionText (10-70% progress)
            logger.info(f"Rendering MotionText for job {job_id}")
            frames_dir = job_dir / "frames"
            frames_dir.mkdir(exist_ok=True)

            await self.render_motiontext(
                video_path=video_path,
                scenario=job.scenario,
                frames_dir=frames_dir,
                width=width,
                height=height,
                fps=fps,
                duration=duration,
                job=job
            )

            # Step 4: Encode video with GPU (70-90% progress)
            logger.info(f"Encoding video for job {job_id}")
            await self.update_progress(job, 70, "Encoding video")

            output_path = job_dir / f"output_{job_id}.mp4"
            await self.ffmpeg_service.encode_video_gpu(
                frames_dir=frames_dir,
                output_path=output_path,
                fps=fps,
                quality=job.options.get("quality", 90),
                width=width,
                height=height
            )

            await self.update_progress(job, 90, "Video encoded")

            # Step 5: Upload to S3 (90-100% progress)
            logger.info(f"Uploading to S3 for job {job_id}")
            s3_key = f"rendered/{job_id}/output.mp4"
            s3_url = await self.s3_service.upload_file(output_path, s3_key)

            # Get file info
            file_size = output_path.stat().st_size
            output_duration = (await self.ffmpeg_service.get_video_info(str(output_path)))["duration"]

            # Complete job
            self.queue.complete_job(job_id)

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
                    "message": "Rendering completed successfully"
                }
            )

            logger.info(f"Job {job_id} completed successfully")

        except asyncio.CancelledError:
            # Job was cancelled
            logger.info(f"Job {job_id} was cancelled")
            self.queue.fail_job(job_id, "Job cancelled", "CANCELLED")
            raise

        except Exception as e:
            # Job failed
            error_message = str(e)
            logger.error(f"Job {job_id} failed: {error_message}")

            self.queue.fail_job(job_id, error_message)

            await self.callback_service.send_callback(
                job.callback_url,
                {
                    "job_id": job_id,
                    "status": "failed",
                    "error_message": error_message,
                    "error_code": "RENDER_ERROR"
                }
            )

        finally:
            # Cleanup
            if job_dir.exists():
                shutil.rmtree(job_dir, ignore_errors=True)

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

    async def render_motiontext(
        self,
        video_path: Path,
        scenario: Dict[str, Any],
        frames_dir: Path,
        width: int,
        height: int,
        fps: float,
        duration: float,
        job: RenderJob
    ):
        """Render MotionText using Playwright"""

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--use-gl=egl',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-setuid-sandbox'
                ]
            )

            try:
                page = await browser.new_page(
                    viewport={'width': width, 'height': height}
                )

                # Create HTML template for MotionText rendering
                html_content = self.create_motiontext_html(
                    video_path=str(video_path),
                    scenario=scenario,
                    width=width,
                    height=height
                )

                # Save HTML temporarily
                html_path = frames_dir.parent / "motiontext.html"
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(html_content)

                # Load the HTML page
                await page.goto(f'file://{html_path}')

                # Wait for video to load
                await page.wait_for_function('window.videoLoaded === true', timeout=30000)

                # Calculate total frames
                total_frames = int(duration * fps)

                # Render frames
                for frame_num in range(total_frames):
                    # Seek to frame
                    time_seconds = frame_num / fps
                    await page.evaluate(f'window.seekToTime({time_seconds})')

                    # Wait for frame to render
                    await page.wait_for_timeout(50)

                    # Take screenshot
                    frame_path = frames_dir / f"frame_{frame_num:06d}.png"
                    await page.screenshot(path=str(frame_path))

                    # Update progress periodically
                    if frame_num % 30 == 0:  # Every second
                        progress = 10 + int((frame_num / total_frames) * 60)
                        await self.update_progress(
                            job,
                            progress,
                            f"Rendering frame {frame_num}/{total_frames}"
                        )

            finally:
                await browser.close()

    def create_motiontext_html(
        self,
        video_path: str,
        scenario: Dict[str, Any],
        width: int,
        height: int
    ) -> str:
        """Create HTML template for MotionText rendering"""

        # This is a simplified template - you would need the actual MotionText library
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    margin: 0;
                    padding: 0;
                    width: {width}px;
                    height: {height}px;
                    overflow: hidden;
                    position: relative;
                    background: black;
                }}
                #video {{
                    position: absolute;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                }}
                #motiontext-container {{
                    position: absolute;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    pointer-events: none;
                }}
                .subtitle {{
                    position: absolute;
                    color: white;
                    font-family: 'Noto Sans CJK', sans-serif;
                    text-shadow: 2px 2px 4px rgba(0,0,0,0.8);
                    text-align: center;
                    white-space: pre-wrap;
                }}
            </style>
        </head>
        <body>
            <video id="video" src="{video_path}" muted></video>
            <div id="motiontext-container"></div>

            <script>
                window.videoLoaded = false;
                const video = document.getElementById('video');
                const container = document.getElementById('motiontext-container');
                const scenario = {json.dumps(scenario)};

                video.addEventListener('loadeddata', () => {{
                    window.videoLoaded = true;
                }});

                // Seek to specific time
                window.seekToTime = function(timeSeconds) {{
                    return new Promise((resolve) => {{
                        video.currentTime = timeSeconds;
                        video.addEventListener('seeked', () => {{
                            // Render subtitles for current time
                            renderSubtitles(timeSeconds);
                            setTimeout(resolve, 100);
                        }}, {{ once: true }});
                    }});
                }};

                // Simple subtitle renderer (replace with actual MotionText)
                function renderSubtitles(currentTime) {{
                    container.innerHTML = '';

                    // Find active cues
                    if (scenario.cues) {{
                        scenario.cues.forEach(cue => {{
                            if (currentTime >= cue.start && currentTime <= cue.end) {{
                                const subtitle = document.createElement('div');
                                subtitle.className = 'subtitle';
                                subtitle.textContent = cue.text || '';
                                subtitle.style.bottom = '10%';
                                subtitle.style.left = '10%';
                                subtitle.style.right = '10%';
                                subtitle.style.fontSize = '24px';
                                container.appendChild(subtitle);
                            }}
                        }});
                    }}
                }}

                // Load video
                video.load();
            </script>
        </body>
        </html>
        """
        return html

    async def update_progress(self, job: RenderJob, progress: int, message: str = ""):
        """Update job progress and send callback"""
        job.progress = progress
        self.queue.update_job(job)

        await self.callback_service.send_callback(
            job.callback_url,
            {
                "job_id": job.job_id,
                "status": "processing",
                "progress": progress,
                "message": message
            }
        )