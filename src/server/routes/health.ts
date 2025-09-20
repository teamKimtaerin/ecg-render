import { Router } from 'express';
import Redis from 'ioredis';

export const healthRouter = Router();

healthRouter.get('/', async (_req, res) => {
  const health = {
    status: 'healthy',
    timestamp: new Date().toISOString(),
    uptime: process.uptime(),
    memory: process.memoryUsage(),
    redis: 'unknown'
  };

  // Check Redis connection
  try {
    const redis = new Redis({
      host: process.env.REDIS_HOST || 'localhost',
      port: parseInt(process.env.REDIS_PORT || '6379'),
      password: process.env.REDIS_PASSWORD
    });
    await redis.ping();
    health.redis = 'connected';
    redis.disconnect();
  } catch (error) {
    health.redis = 'disconnected';
  }

  res.json(health);
});