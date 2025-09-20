import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import path from 'path';
import { logger } from './utils/logger';
import { renderRouter } from './server/routes/render';
import { healthRouter } from './server/routes/health';

// Load environment variables
dotenv.config();

const app = express();
const PORT = process.env.PORT || 3000;

// Middleware
app.use(cors());
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true }));

// Static files (for rendering page)
app.use('/static', express.static(path.join(__dirname, 'static')));

// Routes
app.use('/api/render', renderRouter);
app.use('/health', healthRouter);

// Error handling middleware
app.use((err: any, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  logger.error('Unhandled error:', err);
  res.status(500).json({
    error: 'Internal server error',
    message: process.env.NODE_ENV === 'development' ? err.message : undefined
  });
});

// Start server
async function startServer() {
  try {
    app.listen(PORT, () => {
      logger.info(`🚀 ECG-Render Node.js server running on port ${PORT}`);
      logger.info(`📦 Environment: ${process.env.NODE_ENV}`);
      logger.info(`🎯 API endpoint: http://localhost:${PORT}/api`);
      logger.info(`📄 Static files: http://localhost:${PORT}/static`);
    });
  } catch (error) {
    logger.error('Failed to start server:', error);
    process.exit(1);
  }
}

startServer();