import puppeteer, { Browser, Page } from 'puppeteer';
import { logger } from '../utils/logger';

export class BrowserManager {
  private browser: Browser | null = null;
  private pages: Map<string, Page> = new Map();

  async initialize(): Promise<void> {
    if (this.browser) {
      return;
    }

    try {
      logger.info('Initializing Puppeteer browser...');

      this.browser = await puppeteer.launch({
        headless: true,
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-accelerated-2d-canvas',
          '--no-first-run',
          '--no-zygote',
          '--single-process', // For Docker
          '--disable-gpu' // For headless
        ]
      });

      logger.info('Browser initialized successfully');
    } catch (error) {
      logger.error('Failed to initialize browser:', error);
      throw error;
    }
  }

  async createPage(pageId: string): Promise<Page> {
    if (!this.browser) {
      await this.initialize();
    }

    try {
      const page = await this.browser!.newPage();

      // Set viewport
      await page.setViewport({
        width: 1920,
        height: 1080,
        deviceScaleFactor: 1
      });

      // Store page reference
      this.pages.set(pageId, page);

      logger.debug(`Created page with ID: ${pageId}`);
      return page;

    } catch (error) {
      logger.error(`Failed to create page ${pageId}:`, error);
      throw error;
    }
  }

  async closePage(pageId: string): Promise<void> {
    const page = this.pages.get(pageId);
    if (page) {
      try {
        await page.close();
        this.pages.delete(pageId);
        logger.debug(`Closed page with ID: ${pageId}`);
      } catch (error) {
        logger.error(`Failed to close page ${pageId}:`, error);
      }
    }
  }

  async shutdown(): Promise<void> {
    try {
      // Close all pages
      for (const [, page] of this.pages) {
        await page.close();
      }
      this.pages.clear();

      // Close browser
      if (this.browser) {
        await this.browser.close();
        this.browser = null;
      }

      logger.info('Browser shut down successfully');
    } catch (error) {
      logger.error('Failed to shut down browser:', error);
    }
  }

  isInitialized(): boolean {
    return this.browser !== null;
  }
}

// Singleton instance
export const browserManager = new BrowserManager();