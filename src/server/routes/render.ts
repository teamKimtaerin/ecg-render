import { Router } from 'express';
import { Queue } from 'bullmq';
import { logger } from '../../utils/logger';
import { v4 as uuidv4 } from 'uuid';

export const renderRouter = Router();

// Initialize BullMQ queue
const renderQueue = new Queue('render', {
  connection: {
    host: process.env.REDIS_HOST || 'localhost',
    port: parseInt(process.env.REDIS_PORT || '6379'),
    password: process.env.REDIS_PASSWORD
  }
});

interface RenderRequest {
  scenario: any; // MotionText scenario
  sourceVideoUrl: string; // S3 URL of original video
  options?: {
    resolution?: {
      width: number;
      height: number;
    };
    fps?: number;
    chunkSize?: number;
    format?: 'mp4' | 'webm';
  };
}

// Export video with overlay
renderRouter.post('/export', async (req, res) => {
  try {
    const { scenario, sourceVideoUrl, options = {} } = req.body as RenderRequest;

    // Validate request
    if (!scenario || !sourceVideoUrl) {
      return res.status(400).json({
        error: 'Missing required fields: scenario and sourceVideoUrl'
      });
    }

    // Generate job ID
    const jobId = uuidv4();

    // Default options
    const jobData = {
      jobId,
      scenario,
      sourceVideoUrl,
      resolution: options.resolution || { width: 1920, height: 1080 },
      fps: options.fps || 30,
      chunkSize: options.chunkSize || 10,
      format: options.format || 'mp4',
      createdAt: new Date().toISOString()
    };

    // Add job to queue
    await renderQueue.add('render-overlay', jobData, {
      jobId,
      removeOnComplete: false,
      removeOnFail: false
    });

    logger.info(`Job ${jobId} added to queue`, { jobId });

    return res.json({
      jobId,
      status: 'queued',
      message: 'Job added to render queue'
    });

  } catch (error) {
    logger.error('Error creating render job:', error);
    return res.status(500).json({
      error: 'Failed to create render job',
      message: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});

// Get job status
renderRouter.get('/status/:jobId', async (req, res) => {
  try {
    const { jobId } = req.params;

    const job = await renderQueue.getJob(jobId);

    if (!job) {
      return res.status(404).json({
        error: 'Job not found'
      });
    }

    const state = await job.getState();
    const progress = job.progress;
    const result = job.returnvalue;
    const failedReason = job.failedReason;

    return res.json({
      jobId,
      state,
      progress,
      result,
      failedReason,
      createdAt: job.timestamp,
      processedAt: job.processedOn,
      finishedAt: job.finishedOn
    });

  } catch (error) {
    logger.error('Error getting job status:', error);
    return res.status(500).json({
      error: 'Failed to get job status',
      message: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});

// Cancel job
renderRouter.delete('/job/:jobId', async (req, res) => {
  try {
    const { jobId } = req.params;

    const job = await renderQueue.getJob(jobId);

    if (!job) {
      return res.status(404).json({
        error: 'Job not found'
      });
    }

    await job.remove();

    return res.json({
      jobId,
      status: 'cancelled',
      message: 'Job cancelled successfully'
    });

  } catch (error) {
    logger.error('Error cancelling job:', error);
    return res.status(500).json({
      error: 'Failed to cancel job',
      message: error instanceof Error ? error.message : 'Unknown error'
    });
  }
});