# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a GPU-accelerated MotionText video rendering server that processes video files with subtitle overlays. The system integrates with ML Audio Server for automatic subtitle generation and uses Playwright for browser-based rendering, Redis for job queuing, and FFmpeg GPU encoding for video output.

### System Integration

The ECG Render system consists of three main servers:
- **Backend API** (Port 8000) - Client requests, database management, orchestration
- **ML Audio Server** (Port 8080) - Audio analysis, speech recognition, speaker diarization
- **GPU Render Server** (Port 8090) - Video rendering with subtitle overlays

## Architecture

### Core Components

- `render_server.py` - Main FastAPI server with endpoints for job submission and status
- `server.py` - Alternative server entry point with different import paths
- `celery_worker.py` - Celery worker for distributed task processing
- `render_engine.py` - GPU render engine with Phase 2 streaming pipeline integration
- `modules/` - Core rendering functionality:
  - `queue.py` - Redis-based job queue system with RenderJob dataclass
  - `worker.py` - RenderWorker class that handles Playwright rendering and FFmpeg encoding
  - `parallel_worker.py` - Parallel rendering worker with browser pool management
  - `callbacks.py` - CallbackService for progress updates to backend
  - `ffmpeg.py` - GPU-accelerated video encoding service with streaming support
  - **Phase 2 Components** (NEW):
    - `streaming_pipeline.py` - Real-time frame streaming without disk I/O
    - `segment_merger.py` - Intelligent segment merging with error recovery
    - `memory_optimizer.py` - Dynamic memory management and optimization
- `utils/` - Utility modules:
  - `browser_manager.py` - Playwright browser lifecycle management
  - `redis_manager.py` - Redis status updates for worker coordination
- `src/` - Legacy utilities:
  - `s3.py` - AWS S3 integration for video upload/download
  - `logger.py` - Centralized logging configuration

### Complete Workflow

## Upload Phase (Audio Analysis)

**Flow Diagram**: Frontend → Backend API → S3 Storage → ML Audio Server → Callback → Frontend

**Process Steps**:
1. **Video Upload**: Frontend uploads video to S3 via presigned URLs
2. **Analysis Request**: Backend API triggers ML Audio Server analysis
3. **Audio Processing**: ML Audio Server fetches video and performs:
   - Speech recognition using WhisperX
   - Speaker diarization with pyannote
   - Emotion analysis and timing extraction
4. **Results Callback**: ML Audio Server sends analysis results to Backend API
5. **Status Polling**: Frontend polls Backend API for completion status
6. **Subtitle Generation**: Backend converts analysis to MotionText scenario format

## Export Phase (GPU Rendering)

**Flow Diagram**: Frontend → Backend API → GPU Render Server → S3 → Callback → Frontend

**Process Steps**:
1. **Scenario Submission**: Frontend submits edited subtitle scenario via Backend API
2. **Render Request**: Backend API sends rendering job to GPU Render Server (`POST /render`)
3. **GPU Processing**: GPU Render Server performs:
   - Video download from S3
   - Playwright-based MotionText rendering (20-40× faster than CPU)
   - FFmpeg GPU encoding with NVENC hardware acceleration
4. **Progress Updates**: Real-time callbacks to Backend API with rendering progress
5. **S3 Upload**: Rendered video uploaded to S3 storage
6. **Completion Callback**: Download URL sent to Backend API
7. **File Download**: Frontend downloads via File System Access API

### GPU Render Server Data Flow

1. Jobs submitted via `/render` endpoint (main) or `/api/render/process` (legacy)
2. Jobs queued in Redis with RenderJob structure using camelCase fields
3. RenderWorker instances process jobs using Playwright + FFmpeg GPU encoding
4. Progress callbacks sent to backend via CallbackService with retry logic
5. Final videos uploaded to S3 and download URLs returned
6. Comprehensive error handling with specific ErrorCodes class

## Development Commands

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Run main server
python render_server.py --host 0.0.0.0 --port 8090

# Alternative server entry point
python server.py --host 0.0.0.0 --port 8090
```

### Docker Development
```bash
# Build image
docker build -t ecg-gpu-render .

# Run with GPU support (requires nvidia-docker2)
docker run --gpus all -p 8090:8090 ecg-gpu-render

# Full stack with Redis
docker-compose up -d

# With monitoring
docker-compose --profile monitoring up -d
```

### Testing
```bash
# Health check
curl http://localhost:8090/health

# Queue status check
curl http://localhost:8090/queue/status

# Full integration test (with ML Audio analysis results)
curl -X POST http://localhost:8090/render \
  -H "Content-Type: application/json" \
  -d '{
    "jobId": "test-001",
    "videoUrl": "https://s3.amazonaws.com/bucket/analyzed-video.mp4",
    "scenario": {
      "version": "1.3",
      "cues": [
        {
          "text": "안녕하세요",
          "start": 0.5,
          "end": 2.3,
          "speaker": "Speaker_01",
          "emotion": "neutral",
          "style": {
            "fontSize": "24px",
            "color": "white",
            "fontFamily": "Noto Sans CJK"
          }
        }
      ]
    },
    "options": {
      "width": 1920,
      "height": 1080,
      "fps": 30
    },
    "callbackUrl": "http://backend-api:8000/api/render/callback"
  }'

# Basic render job test (minimal)
curl -X POST http://localhost:8090/render \
  -H "Content-Type: application/json" \
  -d '{
    "jobId": "test-simple",
    "videoUrl": "https://example.com/test.mp4",
    "scenario": {"cues": []},
    "options": {},
    "callbackUrl": "http://localhost:8000/callback"
  }'
```

## Environment Variables

Required for operation:
- `S3_BUCKET` - AWS S3 bucket for rendered videos
- `REDIS_URL` - Redis connection URL (default: redis://localhost:6379)
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` - AWS credentials
- `BACKEND_CALLBACK_URL` - Backend endpoint for progress callbacks
- `MAX_CONCURRENT_JOBS` - Concurrent rendering limit (default: 3)
- `CALLBACK_RETRY_COUNT` - Callback retry attempts (default: 3)
- `CALLBACK_TIMEOUT` - Callback timeout seconds (default: 30)
- `LOG_LEVEL` - Logging level (default: INFO)
- `TEMP_DIR` - Temporary processing directory (default: /tmp/render)

## GPU Requirements

- NVIDIA GPU with CUDA 11.8+ support
- ~3-4GB VRAM per concurrent job
- FFmpeg with NVENC hardware encoding support

## API Endpoints

### Main Endpoints
- `POST /render` - Submit rendering job (main endpoint for backend integration)
- `GET /api/render/{job_id}/status` - Check job status
- `POST /api/render/{job_id}/cancel` - Cancel job
- `GET /health` - Server health check with GPU and queue info
- `GET /queue/status` - Detailed queue status with wait times

### Legacy Endpoints (for backward compatibility)
- `GET /api/render/queue/status` - Basic queue status
- `POST /api/render/queue/clear` - Clear queue (admin only)

## Request/Response Format

### Render Request (POST /render)
```json
{
  "jobId": "550e8400-e29b-41d4-a716-446655440000",
  "videoUrl": "https://s3.amazonaws.com/bucket/video.mp4",
  "scenario": {
    "version": "1.3",
    "cues": [
      {
        "text": "Hello World",
        "start": 0,
        "end": 3,
        "style": {
          "fontSize": "24px",
          "color": "white",
          "fontFamily": "Noto Sans CJK"
        }
      }
    ]
  },
  "options": {
    "width": 1920,
    "height": 1080,
    "fps": 30,
    "quality": 90,
    "format": "mp4"
  },
  "callbackUrl": "https://backend.example.com/api/render/callback"
}
```

### Render Response
```json
{
  "status": "accepted",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Job queued for processing",
  "estimated_time": 180
}
```

### Health Check Response (Phase 2 Enhanced)
```json
{
  "status": "healthy",
  "gpu_count": 1,
  "available_memory": "20GB",
  "queue_length": 3,
  "timestamp": "2025-01-15T10:30:00Z",
  "workers": 3,
  "parallel_rendering": true,
  "browser_pool_size": 4,
  "streaming": {
    "pipeline_active": true,
    "total_frames_processed": 145230,
    "total_frames_dropped": 58,
    "average_drop_rate": 0.0004
  },
  "memory": {
    "process_mb": 1823.4,
    "system_available_gb": 28.5,
    "gpu_available_gb": 18.2
  }
}
```

## Error Codes

The server implements a comprehensive ErrorCodes class with specific error codes:
- `GPU_MEMORY_INSUFFICIENT` - Not enough GPU memory
- `INVALID_VIDEO_FORMAT` - Unsupported video format
- `SCENARIO_PARSE_ERROR` - Invalid scenario data
- `RENDERING_TIMEOUT` - Rendering took too long (30min timeout)
- `STORAGE_ACCESS_ERROR` - S3 access issues
- `CONNECTION_ERROR` - Network connectivity issues
- `TIMEOUT_ERROR` - General timeout errors
- `RENDER_ERROR` - General rendering errors
- `CANCELLED` - Job was cancelled

Error responses include optional `details` object with additional context and suggestions.

## Implementation Notes

### Backend Integration
- Uses camelCase field names for API requests (jobId, videoUrl, callbackUrl)
- Supports both new `/render` endpoint and legacy `/api/render/process`
- Implements exponential backoff for callback retries
- CallbackService supports configurable timeout and retry parameters
- Integrates with ML Audio Server analysis results
- Supports speaker identification and emotion data in scenarios
- Compatible with WhisperX speech recognition output format

### Worker Architecture
- RenderWorker processes jobs asynchronously from Redis queue
- Downloads videos from S3 for processing
- Uses Playwright for browser-based MotionText rendering (20-40× faster than CPU)
- FFmpeg GPU encoding with NVENC hardware acceleration
- Supports complex subtitle scenarios with speaker diarization and emotions
- Automatic cleanup of temporary files after processing
- Progress tracking with real-time callbacks to Backend API

### Monitoring
- Health endpoint provides GPU memory usage and queue statistics
- Queue status endpoint includes estimated wait times and processing jobs
- Comprehensive logging with configurable levels
- Integration monitoring across all three servers (Backend, ML Audio, GPU Render)
- Performance metrics: 20-40× rendering speed improvement over CPU
- Real-time progress updates via WebSocket to frontend

## Phase 2: Streaming Pipeline Optimization

### Overview
Phase 2 introduces significant performance improvements through streaming pipeline optimization, reducing memory usage by 70% and enabling 2-3x more concurrent jobs.

### Key Features
- **Streaming Pipeline**: Direct frame streaming to FFmpeg without disk I/O
- **Memory Optimization**: Dynamic memory management with adaptive garbage collection
- **Intelligent Merging**: Segment merging with error recovery and partial merge capability
- **Backpressure Management**: Prevents system overload through adaptive rate control

### Performance Improvements
| Metric | Phase 1 | Phase 2 | Improvement |
|--------|---------|---------|-------------|
| Memory per Worker | ~6GB | ~2GB | -70% |
| Frame Processing | Disk I/O | Memory Stream | -50% latency |
| Concurrent Jobs | 3-4 | 8-10 | +150% |
| Frame Drop Rate | 5-10% | <1% | -90% |

### New Components
- `modules/streaming_pipeline.py` - Real-time frame streaming with AsyncFrameQueue
- `modules/segment_merger.py` - Coordinates segment merging with error recovery
- `modules/memory_optimizer.py` - Memory monitoring and optimization strategies

### Integration with Celery
The Phase 2 optimizations are automatically active when using the Celery worker mode:
```bash
# Run with Phase 2 optimizations
python celery_worker.py

# Or in Docker
docker-compose -f docker-compose.celery.yml up
```

### Monitoring Phase 2 Metrics
```bash
# Check streaming metrics
curl http://localhost:8090/health | jq '.streaming'

# Monitor memory usage
curl http://localhost:8090/health | jq '.memory'

# Test Phase 2 components
python test_phase2_streaming.py
```

## Common Issues

- **GPU not detected**: Check `nvidia-smi` and CUDA installation
- **Memory errors**: Reduce `MAX_CONCURRENT_JOBS` value
- **Redis connection**: Verify Redis server with `redis-cli ping`
- **Callback failures**: Check `CALLBACK_RETRY_COUNT` and `CALLBACK_TIMEOUT` settings
- **High frame drops**: Check backpressure settings and reduce concurrent jobs
- **Memory leaks**: Enable `ENABLE_MEMORY_OPTIMIZER=true` for automatic cleanup