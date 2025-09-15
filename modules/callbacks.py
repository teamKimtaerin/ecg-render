"""
Callback Service for sending progress updates to Backend
"""

import aiohttp
import asyncio
import logging
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class CallbackService:
    """Service for sending callbacks to Backend API"""

    def __init__(self, timeout: int = 30, retry_count: int = 3):
        """Initialize callback service"""
        self.timeout = timeout
        self.retry_count = retry_count
        self.session = None

    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()

    async def send_callback(
        self,
        callback_url: str,
        data: Dict[str, Any],
        retry_count: int = None
    ) -> bool:
        """Send callback to Backend with retry logic"""

        # Add timestamp
        data['timestamp'] = datetime.utcnow().isoformat()

        # Create session if not exists
        if not self.session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )

        retry_attempts = retry_count or self.retry_count
        for attempt in range(retry_attempts):
            try:
                logger.debug(f"Sending callback to {callback_url}, attempt {attempt + 1}")

                async with self.session.post(
                    callback_url,
                    json=data,
                    headers={
                        'Content-Type': 'application/json',
                        'User-Agent': 'GPU-Render-Server/1.0'
                    }
                ) as response:
                    if response.status == 200:
                        logger.info(f"Callback sent successfully: {data.get('status')}")
                        return True
                    else:
                        logger.warning(
                            f"Callback failed with status {response.status}: "
                            f"{await response.text()}"
                        )

            except asyncio.TimeoutError:
                logger.error(f"Callback timeout on attempt {attempt + 1}")

            except aiohttp.ClientError as e:
                logger.error(f"Callback error on attempt {attempt + 1}: {e}")

            except Exception as e:
                logger.error(f"Unexpected callback error: {e}")

            # Wait before retry
            if attempt < retry_attempts - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

        logger.error(f"Failed to send callback after {retry_attempts} attempts")
        return False

    async def send_progress(
        self,
        callback_url: str,
        job_id: str,
        progress: int,
        message: str = "",
        estimated_time_remaining: int = None
    ) -> bool:
        """Send progress update callback"""

        data = {
            "job_id": job_id,
            "status": "processing",
            "progress": progress,
            "message": message
        }

        if estimated_time_remaining is not None:
            data["estimated_time_remaining"] = estimated_time_remaining

        return await self.send_callback(callback_url, data)

    async def send_completion(
        self,
        callback_url: str,
        job_id: str,
        download_url: str,
        file_size: int,
        duration: float
    ) -> bool:
        """Send completion callback"""

        data = {
            "job_id": job_id,
            "status": "completed",
            "progress": 100,
            "download_url": download_url,
            "file_size": file_size,
            "duration": duration,
            "message": "Rendering completed successfully"
        }

        return await self.send_callback(callback_url, data)

    async def send_error(
        self,
        callback_url: str,
        job_id: str,
        error_message: str,
        error_code: str = "RENDER_ERROR",
        details: Dict[str, Any] = None
    ) -> bool:
        """Send error callback"""

        data = {
            "job_id": job_id,
            "status": "failed",
            "error_message": error_message,
            "error_code": error_code
        }

        if details:
            data["details"] = details

        return await self.send_callback(callback_url, data)

    async def close(self):
        """Close the session"""
        if self.session:
            await self.session.close()
            self.session = None