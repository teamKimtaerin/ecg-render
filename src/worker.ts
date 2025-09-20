import dotenv from 'dotenv';
import { startWorker } from './queue/worker';
import { logger } from './utils/logger';

// Load environment variables
dotenv.config();

// Start the worker
async function main() {
  try {
    logger.info('Starting ECG-Render worker...');
    logger.info(`Environment: ${process.env.NODE_ENV || 'development'}`);
    logger.info(`Redis: ${process.env.REDIS_HOST || 'localhost'}:${process.env.REDIS_PORT || '6379'}`);
    logger.info(`Max workers: ${process.env.MAX_WORKERS || '2'}`);

    await startWorker();
    
    logger.info('Worker started successfully!');
    logger.info('Waiting for render jobs...');

  } catch (error) {
    logger.error('Failed to start worker:', error);
    process.exit(1);
  }
}

// Run the worker
main();