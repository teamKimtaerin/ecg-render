"""
Browser Manager for Playwright
Manages single browser instance for segment rendering with memory optimization
"""

import logging
from playwright.async_api import async_playwright, Browser, Page
from typing import Optional

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manages Playwright browser instances with memory optimization"""

    def __init__(self):
        """Initialize browser manager"""
        self.playwright = None
        self.browser = None

    async def create_browser(self) -> Browser:
        """
        Create an optimized browser instance

        Returns:
            Browser instance
        """
        if not self.playwright:
            self.playwright = await async_playwright().start()

        # Browser arguments for memory optimization
        browser_args = [
            # GPU and rendering optimization
            '--disable-gpu',  # Disable GPU in headless mode
            '--disable-dev-shm-usage',  # Overcome limited resource problems
            '--disable-web-security',  # Disable CORS for local files
            '--no-sandbox',  # Required for Docker
            '--disable-setuid-sandbox',

            # Memory optimization
            '--single-process',  # Run in single process mode
            '--max_old_space_size=512',  # Limit V8 memory to 512MB
            '--disable-features=IsolateOrigins,site-per-process',  # Reduce process count

            # Performance optimization
            '--disable-blink-features=AutomationControlled',  # Hide automation
            '--disable-background-networking',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-breakpad',
            '--disable-client-side-phishing-detection',
            '--disable-component-extensions-with-background-pages',
            '--disable-default-apps',
            '--disable-extensions',
            '--disable-features=TranslateUI',
            '--disable-hang-monitor',
            '--disable-ipc-flooding-protection',
            '--disable-popup-blocking',
            '--disable-prompt-on-repost',
            '--disable-renderer-backgrounding',
            '--disable-sync',
            '--metrics-recording-only',
            '--no-first-run',
            '--safebrowsing-disable-auto-update',
            '--password-store=basic',
            '--use-mock-keychain',

            # Additional optimizations
            '--disable-software-rasterizer',
            '--disable-features=AudioServiceOutOfProcess',
            '--disable-features=BackForwardCache',
            '--disable-features=RendererCodeIntegrity',
            '--disable-features=ResizeObserver',
            '--disable-features=WebRtcHideLocalIpsWithMdns',
            '--disable-features=site-per-process',

            # Headless specific
            '--headless=new',  # Use new headless mode
            '--hide-scrollbars',
            '--mute-audio',
        ]

        try:
            # Launch browser
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=browser_args,
                # Performance settings
                chrome_sandbox=False,
                handle_sigint=False,
                handle_sigterm=False,
                handle_sighup=False,
            )

            logger.info("✅ Browser instance created with memory optimization")
            return self.browser

        except Exception as e:
            logger.error(f"Failed to create browser: {str(e)}")
            raise

    async def create_page(self, browser: Browser, width: int = 1920, height: int = 1080) -> Page:
        """
        Create an optimized page

        Args:
            browser: Browser instance
            width: Page width
            height: Page height

        Returns:
            Page instance
        """
        # Create context with specific viewport
        context = await browser.new_context(
            viewport={'width': width, 'height': height},
            device_scale_factor=1,
            ignore_https_errors=True,
            # Disable unnecessary features
            java_script_enabled=True,  # Required for rendering
            has_touch=False,
            is_mobile=False,
            locale='en-US',
            timezone_id='UTC',
            # Permissions
            permissions=[],
            geolocation=None,
            # Media
            color_scheme='light',
            reduced_motion='reduce',
            forced_colors='none',
            # Security
            bypass_csp=True,  # Bypass Content Security Policy
            # Storage
            storage_state=None,
            # Network
            offline=False,
            http_credentials=None,
            proxy=None,
        )

        # Create page
        page = await context.new_page()

        # Set default timeouts
        page.set_default_timeout(30000)  # 30 seconds
        page.set_default_navigation_timeout(30000)

        # Optimize page settings
        await page.add_init_script("""
            // Disable animations for faster rendering
            const style = document.createElement('style');
            style.innerHTML = `
                *, *::before, *::after {
                    animation-duration: 0s !important;
                    animation-delay: 0s !important;
                    transition-duration: 0s !important;
                    transition-delay: 0s !important;
                }
            `;
            document.head.appendChild(style);

            // Disable smooth scrolling
            document.documentElement.style.scrollBehavior = 'auto';

            // Optimize rendering
            if (window.requestIdleCallback) {
                window.requestIdleCallback = () => {};
            }
        """)

        logger.debug(f"Created page with viewport {width}x{height}")
        return page

    async def cleanup(self):
        """Clean up browser resources"""
        try:
            if self.browser:
                await self.browser.close()
                self.browser = None
                logger.debug("Browser closed")

            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
                logger.debug("Playwright stopped")

        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}")

    async def __aenter__(self):
        """Context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        await self.cleanup()