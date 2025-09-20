"""
Browser Manager for Playwright
Simplified browser management for rendering
"""

import logging
from playwright.async_api import async_playwright, Browser
from typing import Optional

logger = logging.getLogger(__name__)


class BrowserManager:
    """Simplified browser manager for rendering"""

    def __init__(self):
        """Initialize browser manager"""
        self.playwright = None

    async def create_browser(self) -> Browser:
        """Create a browser instance"""
        if not self.playwright:
            self.playwright = await async_playwright().start()

        # Basic browser args for rendering
        browser_args = [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-web-security',
            '--single-process',
            '--disable-extensions'
        ]

        browser = await self.playwright.chromium.launch(
            headless=True,
            args=browser_args
        )

        logger.info("Browser created for rendering")
        return browser

    async def cleanup(self):
        """Clean up browser resources"""
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
            logger.debug("Browser manager cleaned up")