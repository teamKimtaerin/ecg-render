import { Worker, Job } from 'bullmq';
import { logger } from '../utils/logger';
import { OverlayRenderer } from '../renderer/overlay';
import { FFmpegPipeline } from '../pipeline/ffmpeg';
import { S3Service } from '../services/s3';
import { browserManager } from '../renderer/browser';
import path from 'path';
import fs from 'fs/promises';

interface RenderJobData {
  jobId: string;
  scenario: any;
  sourceVideoUrl: string;
  resolution: {
    width: number;
    height: number;
  };
  fps: number;
  chunkSize: number;
  format: 'mp4' | 'webm';
}

interface ChunkInfo {
  id: number;
  startTime: number;
  endTime: number;
}

// Initialize browser on startup
async function initializeBrowser() {
  await browserManager.initialize();
  logger.info('Browser initialized for worker');
}

// Divide video into chunks
function divideIntoChunks(duration: number, chunkSize: number): ChunkInfo[] {
  const chunks: ChunkInfo[] = [];
  let currentTime = 0;
  let chunkId = 0;

  while (currentTime < duration) {
    const endTime = Math.min(currentTime + chunkSize, duration);
    chunks.push({
      id: chunkId++,
      startTime: currentTime,
      endTime: endTime
    });
    currentTime = endTime;
  }

  return chunks;
}

// Process single chunk
async function processChunk(
  chunk: ChunkInfo,
  jobData: RenderJobData,
  renderer: OverlayRenderer
): Promise<string> {
  logger.info(`Processing chunk ${chunk.id}: ${chunk.startTime}s - ${chunk.endTime}s`);

  // Render frames for this chunk
  const frames = await renderer.renderChunk({
    scenario: jobData.scenario,
    startTime: chunk.startTime,
    endTime: chunk.endTime,
    resolution: jobData.resolution,
    fps: jobData.fps,
    transparent: true
  });

  // Create transparent video from frames
  const ffmpeg = new FFmpegPipeline();
  const chunkPath = path.join('/tmp', `${jobData.jobId}_chunk_${chunk.id}.webm`);

  await ffmpeg.createTransparentVideo({
    frames,
    fps: jobData.fps,
    outputPath: chunkPath
  });

  return chunkPath;
}

// Main worker function
export async function startWorker() {
  // Initialize browser
  await initializeBrowser();

  const worker = new Worker<RenderJobData>(
    'render',
    async (job: Job<RenderJobData>) => {
      const startTime = Date.now();
      const { jobId, sourceVideoUrl, chunkSize, format } = job.data;

      logger.info(`Starting render job ${jobId}`);

      try {
        // Initialize services
        const s3 = new S3Service();
        const ffmpeg = new FFmpegPipeline();
        const renderer = new OverlayRenderer(jobId);

        // Update job progress
        await job.updateProgress(5);

        // Initialize renderer
        await renderer.initialize();
        await job.updateProgress(10);

        // Download source video from S3
        logger.info('Downloading source video from S3...');
        const localVideoPath = await s3.downloadVideo(sourceVideoUrl, jobId);
        await job.updateProgress(20);

        // Get video duration
        const videoDuration = await ffmpeg.getVideoDuration(localVideoPath);
        logger.info(`Video duration: ${videoDuration}s`);

        // Divide into chunks
        const chunks = divideIntoChunks(videoDuration, chunkSize);
        logger.info(`Divided into ${chunks.length} chunks`);

        // Process each chunk
        const chunkPaths: string[] = [];
        const chunkProgressStep = 50 / chunks.length;

        for (let i = 0; i < chunks.length; i++) {
          const chunk = chunks[i];
          const chunkPath = await processChunk(chunk, job.data, renderer);
          chunkPaths.push(chunkPath);

          // Update progress
          await job.updateProgress(20 + (i + 1) * chunkProgressStep);
        }

        // Merge all transparent chunks
        logger.info('Merging transparent chunks...');
        const mergedOverlayPath = path.join('/tmp', `${jobId}_overlay.webm`);
        await ffmpeg.mergeChunks(chunkPaths, mergedOverlayPath);
        await job.updateProgress(75);

        // Composite with original video
        logger.info('Compositing with original video...');
        const outputPath = path.join('/tmp', `${jobId}_final.${format}`);
        await ffmpeg.compositeVideos({
          sourceVideo: localVideoPath,
          overlayVideo: mergedOverlayPath,
          outputPath,
          format
        });
        await job.updateProgress(90);

        // Upload to S3
        logger.info('Uploading final video to S3...');
        const finalUrl = await s3.uploadVideo(outputPath, jobId);
        await job.updateProgress(95);

        // Cleanup
        await renderer.cleanup();

        // Clean up temporary files
        const tempFiles = [localVideoPath, mergedOverlayPath, outputPath, ...chunkPaths];
        for (const file of tempFiles) {
          try {
            await fs.unlink(file);
          } catch (err) {
            // Ignore cleanup errors
          }
        }

        await job.updateProgress(100);

        const processingTime = (Date.now() - startTime) / 1000;
        logger.info(`Job ${jobId} completed in ${processingTime}s`);

        return {
          success: true,
          jobId,
          finalUrl,
          processingTime,
          chunks: chunks.length
        };

      } catch (error) {
        logger.error(`Job ${jobId} failed:`, error);
        throw error;
      }
    },
    {
      connection: {
        host: process.env.REDIS_HOST || 'localhost',
        port: parseInt(process.env.REDIS_PORT || '6379'),
        password: process.env.REDIS_PASSWORD
      },
      concurrency: parseInt(process.env.MAX_WORKERS || '2'),
      limiter: {
        max: 2,
        duration: 1000 // Max 2 jobs per second
      }
    }
  );

  // Worker event handlers
  worker.on('completed', (job) => {
    logger.info(`Job ${job.id} completed successfully`);
  });

  worker.on('failed', (job, err) => {
    logger.error(`Job ${job?.id} failed:`, err);
  });

  worker.on('error', (err) => {
    logger.error('Worker error:', err);
  });

  // Graceful shutdown
  process.on('SIGTERM', async () => {
    logger.info('SIGTERM received, shutting down worker...');
    await worker.close();
    await browserManager.shutdown();
    process.exit(0);
  });

  process.on('SIGINT', async () => {
    logger.info('SIGINT received, shutting down worker...');
    await worker.close();
    await browserManager.shutdown();
    process.exit(0);
  });

  logger.info('Worker started and listening for jobs');

  return worker;
}