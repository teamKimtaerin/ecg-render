# ECG-Render Server

Node.js-based video rendering server with MotionText overlay using Puppeteer, BullMQ, and FFmpeg.

## Overview

This server processes videos by:
1. Receiving scenario.json (subtitle data) and source MP4 video
2. Rendering transparent overlay animations using MotionText
3. Compositing the overlay with the original video
4. Outputting the final video with animations

## Architecture

```
Frontend
    ↓
[API Server (:3000)]
    ↓
BullMQ → Redis
    ↓
[Worker Processes]
    ├── Puppeteer (MotionText rendering)
    ├── FFmpeg (Video processing)
    └── S3 (Storage)
```

## Quick Start

### Prerequisites

- Node.js 18+
- Redis server
- FFmpeg with VP9 support
- Chromium/Chrome browser
- AWS S3 credentials (optional)

### Installation

```bash
# Clone the repository
git clone https://github.com/your-org/ecg-render.git
cd ecg-render

# Install dependencies
npm install

# Copy environment variables
cp .env.example .env
# Edit .env with your configuration

# Build TypeScript
npm run build
```

### Development

```bash
# Start Redis (if not running)
redis-server

# Terminal 1: Start API server
npm run dev

# Terminal 2: Start worker
npm run dev:worker
```

### Production

```bash
# Using Docker Compose
docker-compose up -d

# Or manually
npm run build
npm start        # API server
npm run start:worker  # Worker process
```

## API Endpoints

### Submit Render Job

```http
POST /api/render
Content-Type: application/json

{
  "scenario": { ... },  // MotionText scenario object
  "sourceVideoUrl": "s3://bucket/video.mp4",
  "options": {
    "resolution": { "width": 1920, "height": 1080 },
    "fps": 30,
    "format": "mp4"
  }
}
```

### Get Job Status

```http
GET /api/render/:jobId/status
```

### Health Check

```http
GET /health
```

## Configuration

### Environment Variables

See `.env.example` for all configuration options:

- `PORT`: API server port (default: 3000)
- `REDIS_HOST/PORT`: Redis connection
- `AWS_*`: S3 credentials
- `MAX_WORKERS`: Worker concurrency
- `CHUNK_SIZE_SECONDS`: Video chunk size
- `USE_NVENC`: Enable GPU encoding

### Worker Scaling

Adjust worker count based on your system:

```bash
# Single worker
MAX_WORKERS=1 npm run start:worker

# Multiple workers
MAX_WORKERS=4 npm run start:worker
```

## Docker Deployment

### Build Images

```bash
# Build all services
docker-compose build

# Or build individually
docker build -t ecg-render:server .
docker build -t ecg-render:worker -f Dockerfile.worker .
```

### Run with Docker Compose

```bash
# Start all services
docker-compose up -d

# Scale workers
docker-compose up -d --scale worker=3

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### GPU Support (NVIDIA)

For GPU-accelerated encoding with NVENC:

```yaml
# docker-compose.gpu.yml
services:
  worker:
    runtime: nvidia
    environment:
      - USE_NVENC=true
      - NVIDIA_VISIBLE_DEVICES=all
```

```bash
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up
```

## Processing Flow

1. **Job Submission**: API receives render request
2. **Queue**: Job added to BullMQ queue in Redis  
3. **Chunking**: Video divided into segments (default: 10s)
4. **Rendering**: Each chunk processed in parallel:
   - Puppeteer renders MotionText animations
   - Captures transparent PNG frames
   - FFmpeg creates WebM with alpha channel
5. **Merging**: Chunks combined into single overlay
6. **Compositing**: Overlay merged with original video
7. **Upload**: Final video uploaded to S3
8. **Callback**: Status sent to callback URL

## Performance

- **Chunk Processing**: ~1-2s per second of video
- **Memory Usage**: ~500MB per worker
- **CPU Usage**: 100-200% per worker during rendering
- **GPU Encoding**: 2-3x faster with NVENC

### Optimization Tips

1. Increase chunk size for longer videos
2. Use GPU encoding when available
3. Scale workers based on CPU cores
4. Use SSD for temporary files
5. Optimize Redis memory settings

## Troubleshooting

### Common Issues

**Puppeteer fails to launch**
```bash
# Install Chrome dependencies
sudo apt-get install -y libx11-xcb1 libxcomposite1 libxdamage1 libxi6 libxext6 libxtst6 libnss3 libcups2 libxss1 libxrandr2 libasound2 libpangocairo-1.0-0 libatk1.0-0 libcairo-gobject2 libgtk-3-0 libgdk-pixbuf2.0-0
```

**FFmpeg VP9 not supported**
```bash
# Compile FFmpeg with VP9
sudo apt-get install -y libvpx-dev
# Or use Docker image with FFmpeg pre-installed
```

**Redis connection refused**
```bash
# Check Redis is running
redis-cli ping
# Should return: PONG
```

**Out of memory errors**
- Reduce `MAX_WORKERS`
- Increase `CHUNK_SIZE_SECONDS`
- Add swap space

## Development

### Project Structure

```
ecg-render/
├── src/
│   ├── server.ts          # Express API server
│   ├── worker.ts          # BullMQ worker entry
│   ├── api/               # API routes
│   ├── queue/             # Job queue management
│   ├── renderer/          # Puppeteer rendering
│   ├── pipeline/          # FFmpeg processing
│   ├── services/          # S3, callbacks
│   ├── static/            # HTML render page
│   └── utils/             # Logging, helpers
├── dist/                  # Compiled JavaScript
├── logs/                  # Application logs
├── package.json
├── tsconfig.json
├── Dockerfile
├── Dockerfile.worker
└── docker-compose.yml
```

### Testing

```bash
# Run tests
npm test

# Test render with sample data
curl -X POST http://localhost:3000/api/render \
  -H "Content-Type: application/json" \
  -d @test/sample-job.json
```

### Debugging

```bash
# Enable debug logging
LOG_LEVEL=debug npm run dev

# Inspect worker
node --inspect dist/worker.js

# Monitor Redis
redis-cli monitor

# Watch queue
npx bull-dashboard
```

## License

MIT

## Support

For issues and questions:
- GitHub Issues: [ecg-render/issues](https://github.com/your-org/ecg-render/issues)
- Documentation: [docs/](./docs)
- Email: support@yourcompany.com