"""
Worker Pool Manager for parallel browser rendering
Manages multiple Playwright browser instances for concurrent segment processing
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
import json

logger = logging.getLogger(__name__)


@dataclass
class BrowserWorker:
    """Individual browser worker instance"""
    worker_id: int
    browser: Browser
    context: BrowserContext
    is_busy: bool = False
    current_job: Optional[str] = None


class WorkerPoolManager:
    """Manages a pool of browser instances for parallel rendering"""

    def __init__(self, pool_size: int = 4):
        """
        Initialize worker pool manager

        Args:
            pool_size: Number of concurrent browser instances (default: 4)
        """
        self.pool_size = pool_size
        self.workers: Dict[int, BrowserWorker] = {}
        self.playwright = None
        self.available_workers: asyncio.Queue = asyncio.Queue()
        self.is_initialized = False

    async def initialize(self):
        """Initialize the worker pool with browser instances"""
        if self.is_initialized:
            logger.warning("Worker pool already initialized")
            return

        logger.info(f"Initializing worker pool with {self.pool_size} workers")

        # Start Playwright
        self.playwright = await async_playwright().start()

        # Create browser instances
        for worker_id in range(self.pool_size):
            try:
                # Launch browser with GPU acceleration
                browser = await self.playwright.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-web-security',
                        '--disable-features=VizDisplayCompositor',
                        '--enable-gpu',
                        '--use-gl=egl',  # Use EGL for headless GPU
                        '--use-angle=gl',  # Use OpenGL ES backend
                        '--enable-accelerated-video-decode',
                        '--enable-accelerated-mjpeg-decode',
                        '--disable-blink-features=AutomationControlled',
                        '--window-size=1920,1080'
                    ]
                )

                # Create context with viewport
                context = await browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    device_scale_factor=1,
                    ignore_https_errors=True
                )

                # Create worker
                worker = BrowserWorker(
                    worker_id=worker_id,
                    browser=browser,
                    context=context
                )

                self.workers[worker_id] = worker
                await self.available_workers.put(worker_id)

                logger.info(f"✅ Worker {worker_id} initialized")

            except Exception as e:
                logger.error(f"Failed to initialize worker {worker_id}: {e}")
                raise

        self.is_initialized = True
        logger.info(f"✅ Worker pool initialized with {self.pool_size} workers")

    async def get_available_worker(self, timeout: float = 30) -> Optional[BrowserWorker]:
        """
        Get an available worker from the pool

        Args:
            timeout: Maximum time to wait for a worker (seconds)

        Returns:
            Available BrowserWorker or None if timeout
        """
        try:
            # Wait for available worker with timeout
            worker_id = await asyncio.wait_for(
                self.available_workers.get(),
                timeout=timeout
            )

            worker = self.workers[worker_id]
            worker.is_busy = True

            logger.debug(f"Worker {worker_id} assigned")
            return worker

        except asyncio.TimeoutError:
            logger.warning(f"No available workers after {timeout}s timeout")
            return None

    async def release_worker(self, worker: BrowserWorker):
        """
        Release a worker back to the pool

        Args:
            worker: Worker to release
        """
        worker.is_busy = False
        worker.current_job = None

        # Clear browser context state
        try:
            # Close all pages except blank
            pages = worker.context.pages
            for page in pages:
                await page.close()
        except Exception as e:
            logger.error(f"Error cleaning worker {worker.worker_id}: {e}")

        # Put back in available queue
        await self.available_workers.put(worker.worker_id)
        logger.debug(f"Worker {worker.worker_id} released")

    async def render_segment(
        self,
        worker: BrowserWorker,
        video_path: str,
        segment: Dict[str, Any],
        scenario: Dict[str, Any],
        width: int = 1920,
        height: int = 1080
    ) -> List[bytes]:
        """
        Render a video segment using a specific worker

        Args:
            worker: Browser worker to use
            video_path: Path to video file
            segment: Segment information (start_time, end_time, worker_id)
            scenario: MotionText scenario data
            width: Video width
            height: Video height

        Returns:
            List of frame data as bytes
        """
        worker.current_job = f"segment_{segment['start_time']}_{segment['end_time']}"
        frames_data = []

        try:
            # Create new page for rendering
            page = await worker.context.new_page()
            await page.set_viewport_size({'width': width, 'height': height})

            logger.info(f"Worker {worker.worker_id}: Rendering segment {segment['start_time']}-{segment['end_time']}s")

            # Build segment-specific scenario
            segment_scenario = self._build_segment_scenario(
                scenario,
                segment['start_time'],
                segment['end_time']
            )

            # Create and load HTML content
            html_content = self._create_motiontext_html(
                video_path,
                segment_scenario,
                width,
                height,
                segment['start_time']
            )

            # Load HTML as data URL
            await page.goto(f"data:text/html,{html_content}")

            # Wait for video to load
            await page.wait_for_function('window.videoLoaded === true', timeout=30000)

            # Calculate frames for this segment
            fps = 30
            duration = segment['end_time'] - segment['start_time']
            total_frames = int(duration * fps)

            # Render frames
            for frame_num in range(total_frames):
                current_time = segment['start_time'] + (frame_num / fps)

                # Seek to time
                await page.evaluate(f'window.seekToTime({current_time})')
                await page.wait_for_timeout(50)  # Wait for frame to stabilize

                # Capture screenshot
                screenshot = await page.screenshot(
                    type='png',
                    full_page=False,
                    clip={'x': 0, 'y': 0, 'width': width, 'height': height}
                )

                frames_data.append(screenshot)

                # Progress logging
                if frame_num % 30 == 0:  # Every second
                    progress = (frame_num / total_frames) * 100
                    logger.debug(f"Worker {worker.worker_id}: {frame_num}/{total_frames} frames ({progress:.1f}%)")

            await page.close()

            logger.info(f"✅ Worker {worker.worker_id}: Segment complete ({len(frames_data)} frames)")
            return frames_data

        except Exception as e:
            logger.error(f"Worker {worker.worker_id} rendering error: {e}")
            raise

    def _build_segment_scenario(
        self,
        scenario: Dict[str, Any],
        start_time: float,
        end_time: float
    ) -> Dict[str, Any]:
        """
        Build scenario for specific segment

        Args:
            scenario: Full scenario
            start_time: Segment start time
            end_time: Segment end time

        Returns:
            Adjusted scenario for segment
        """
        segment_cues = []

        # Filter and adjust cues for this segment
        for cue in scenario.get('cues', []):
            cue_start = cue.get('start', 0)
            cue_end = cue.get('end', 0)

            # Check if cue overlaps with segment
            if cue_end > start_time and cue_start < end_time:
                # Adjust cue timing relative to segment
                adjusted_cue = cue.copy()
                adjusted_cue['start'] = max(0, cue_start - start_time)
                adjusted_cue['end'] = min(end_time - start_time, cue_end - start_time)
                segment_cues.append(adjusted_cue)

        # Create segment scenario
        segment_scenario = scenario.copy()
        segment_scenario['cues'] = segment_cues

        return segment_scenario

    def _create_motiontext_html(
        self,
        video_path: str,
        scenario: Dict[str, Any],
        width: int,
        height: int,
        start_time: float = 0
    ) -> str:
        """
        Create HTML template for MotionText rendering

        Args:
            video_path: Path to video file
            scenario: MotionText scenario
            width: Video width
            height: Video height
            start_time: Start time for video

        Returns:
            HTML content as string
        """
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
                const segmentStartTime = {start_time};

                video.addEventListener('loadeddata', () => {{
                    window.videoLoaded = true;
                    video.currentTime = segmentStartTime;
                }});

                // Seek to specific time
                window.seekToTime = function(timeSeconds) {{
                    return new Promise((resolve) => {{
                        video.currentTime = timeSeconds;
                        video.addEventListener('seeked', () => {{
                            // Render subtitles for current time
                            renderSubtitles(timeSeconds - segmentStartTime);
                            setTimeout(resolve, 100);
                        }}, {{ once: true }});
                    }});
                }};

                // Subtitle renderer (simplified - replace with actual MotionText)
                function renderSubtitles(relativeTime) {{
                    container.innerHTML = '';

                    // Find active cues
                    if (scenario.cues) {{
                        scenario.cues.forEach(cue => {{
                            if (relativeTime >= cue.start && relativeTime <= cue.end) {{
                                const subtitle = document.createElement('div');
                                subtitle.className = 'subtitle';
                                subtitle.textContent = cue.text || '';
                                subtitle.style.bottom = '10%';
                                subtitle.style.left = '10%';
                                subtitle.style.right = '10%';
                                subtitle.style.fontSize = '24px';

                                // Apply style if provided
                                if (cue.style) {{
                                    if (cue.style.fontSize) subtitle.style.fontSize = cue.style.fontSize;
                                    if (cue.style.color) subtitle.style.color = cue.style.color;
                                    if (cue.style.fontFamily) subtitle.style.fontFamily = cue.style.fontFamily;
                                }}

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

    async def cleanup(self):
        """Clean up all browser instances"""
        if not self.is_initialized:
            return

        logger.info("Cleaning up worker pool...")

        # Close all browsers
        for worker_id, worker in self.workers.items():
            try:
                await worker.context.close()
                await worker.browser.close()
                logger.debug(f"Worker {worker_id} closed")
            except Exception as e:
                logger.error(f"Error closing worker {worker_id}: {e}")

        # Stop Playwright
        if self.playwright:
            await self.playwright.stop()

        self.workers.clear()
        self.is_initialized = False

        logger.info("✅ Worker pool cleaned up")

    def get_pool_status(self) -> Dict[str, Any]:
        """Get current pool status"""
        busy_workers = [w for w in self.workers.values() if w.is_busy]
        available_count = self.available_workers.qsize()

        return {
            "total_workers": self.pool_size,
            "busy_workers": len(busy_workers),
            "available_workers": available_count,
            "worker_details": [
                {
                    "worker_id": w.worker_id,
                    "is_busy": w.is_busy,
                    "current_job": w.current_job
                }
                for w in self.workers.values()
            ]
        }