import { Page } from 'puppeteer';
import { browserManager } from './browser';
import { logger } from '../utils/logger';
import '../types/global';

export interface RenderOptions {
  scenario: any;
  startTime: number;
  endTime: number;
  resolution: {
    width: number;
    height: number;
  };
  fps: number;
  transparent?: boolean;
}

export interface FrameData {
  frameNumber: number;
  timestamp: number;
  data: Buffer;
}

export class OverlayRenderer {
  private page: Page | null = null;
  private pageId: string;

  constructor(pageId: string) {
    this.pageId = pageId;
  }

  async initialize(): Promise<void> {
    try {
      // Create browser page
      this.page = await browserManager.createPage(this.pageId);

      // Set viewport to match resolution
      await this.page.setViewport({
        width: 1920,
        height: 1080,
        deviceScaleFactor: 1
      });

      // Navigate to overlay render page
      const renderPageUrl = `http://localhost:${process.env.PORT || 3000}/static/overlay-render.html`;
      await this.page.goto(renderPageUrl, {
        waitUntil: 'networkidle0'
      });

      // Wait for page to be ready
      await this.page.waitForFunction('() => window.pageReady === true', {
        timeout: 10000
      });

      logger.info(`Overlay renderer initialized for page ${this.pageId}`);

    } catch (error) {
      logger.error('Failed to initialize overlay renderer:', error);
      throw error;
    }
  }

  async loadScenario(scenario: any): Promise<void> {
    if (!this.page) {
      throw new Error('Renderer not initialized');
    }

    try {
      const result = await this.page.evaluate(async (scenarioData: any) => {
        return await window.loadMotionTextScenario!(scenarioData);
      }, scenario);

      if (!result.success) {
        throw new Error(`Failed to load scenario: ${result.error}`);
      }

      logger.info(`Scenario loaded with ${result.cueCount} cues`);

    } catch (error) {
      logger.error('Failed to load scenario:', error);
      throw error;
    }
  }

  async renderFrames(options: RenderOptions): Promise<FrameData[]> {
    if (!this.page) {
      throw new Error('Renderer not initialized');
    }

    const frames: FrameData[] = [];
    const { startTime, endTime, fps, resolution } = options;
    const duration = endTime - startTime;
    const totalFrames = Math.ceil(duration * fps);

    logger.info(`Rendering ${totalFrames} frames from ${startTime}s to ${endTime}s`);

    try {
      // Load scenario
      await this.loadScenario(options.scenario);

      // Set viewport to match resolution
      await this.page.setViewport({
        width: resolution.width,
        height: resolution.height,
        deviceScaleFactor: 1
      });

      // Render each frame
      for (let i = 0; i < totalFrames; i++) {
        const currentTime = startTime + (i / fps);

        // Seek to current time
        await this.page.evaluate(async (time: number) => {
          return await window.seekToTime!(time);
        }, currentTime);

        // Capture frame
        const screenshot = await this.page.screenshot({
          type: 'png',
          omitBackground: options.transparent !== false, // Default to transparent
          clip: {
            x: 0,
            y: 0,
            width: resolution.width,
            height: resolution.height
          }
        });

        frames.push({
          frameNumber: i,
          timestamp: currentTime,
          data: screenshot as Buffer
        });

        // Progress logging
        if (i % 30 === 0) { // Log every second (at 30fps)
          logger.debug(`Rendered frame ${i}/${totalFrames} at time ${currentTime.toFixed(2)}s`);
        }
      }

      logger.info(`Successfully rendered ${frames.length} frames`);
      return frames;

    } catch (error) {
      logger.error('Failed to render frames:', error);
      throw error;
    }
  }

  async renderChunk(options: RenderOptions): Promise<Buffer[]> {
    const frameData = await this.renderFrames(options);
    return frameData.map(f => f.data);
  }

  async cleanup(): Promise<void> {
    try {
      if (this.page) {
        await this.page.evaluate(() => {
          window.cleanup?.();
        });
      }

      await browserManager.closePage(this.pageId);
      this.page = null;

      logger.debug(`Cleaned up renderer for page ${this.pageId}`);

    } catch (error) {
      logger.error('Failed to cleanup renderer:', error);
    }
  }

  async getStatus(): Promise<any> {
    if (!this.page) {
      return { initialized: false };
    }

    try {
      return await this.page.evaluate(() => {
        return window.getRendererStatus?.();
      });
    } catch (error) {
      logger.error('Failed to get renderer status:', error);
      return { initialized: false, error: error };
    }
  }
}