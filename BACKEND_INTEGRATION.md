# Backend Integration Document

## ECG-Render to ECG-Backend Function Migration

This document outlines functionality that should be moved from the ECG GPU Render Server to the ECG Backend API Server for better architectural separation and reduced complexity.

## Functions to Move to Backend

### 1. **Job Orchestration and Management**

**Current Location**: Distributed across multiple files in ecg-render
**Should Move to**: `ecg-backend/app/api/v1/render.py`

**Functions to Move**:
- Job queue management and prioritization
- Segment allocation logic (deciding how to split video into 4 segments)
- Worker assignment and load balancing
- Job retry logic and failure handling
- Job cancellation and cleanup

**Why**: The backend should be responsible for orchestrating jobs, not the render workers. This reduces complexity in the GPU server.

**Impact**:
- Removes complex job management from render server
- Centralizes job control in backend where it belongs
- Simplifies worker-only architecture

### 2. **Progress Aggregation and Status Management**

**Current Location**: `app/core/redis.py` and distributed logic
**Should Move to**: `ecg-backend/app/services/render_service.py`

**Functions to Move**:
- Aggregate worker progress into overall job progress
- Calculate estimated completion times
- Handle worker failure recovery
- Determine when job is complete (all segments finished)
- Send progress updates to frontend via WebSocket

**Why**: Progress aggregation requires business logic that belongs in the backend, not in individual workers.

**Backend Implementation**:
```python
class RenderProgressService:
    async def aggregate_worker_progress(self, job_id: str) -> dict:
        """Aggregate individual worker progress into job progress"""

    async def handle_worker_failure(self, job_id: str, worker_id: int):
        """Reassign failed worker segments to available workers"""

    async def calculate_eta(self, job_id: str) -> int:
        """Calculate estimated time remaining"""
```

### 3. **Video Preprocessing and Metadata Extraction**

**Current Location**: `app/services/` in ecg-render
**Should Move to**: `ecg-backend/app/services/video_service.py`

**Functions to Move**:
- Video download from S3
- Video metadata extraction (duration, fps, resolution)
- Video format validation and conversion
- Frame count calculation for segment division
- Thumbnail generation

**Why**: Video preprocessing is business logic, not rendering logic. Backend should prepare all data before sending to render workers.

**Backend Implementation**:
```python
class VideoPreprocessingService:
    async def extract_metadata(self, video_url: str) -> dict:
        """Extract video metadata for segment planning"""

    async def calculate_segments(self, duration: float, worker_count: int) -> list:
        """Calculate optimal segment division"""

    async def validate_video_format(self, video_url: str) -> bool:
        """Validate video format compatibility"""
```

### 4. **S3 Management and File Operations**

**Current Location**: `app/services/s3.py` in ecg-render
**Should Move to**: `ecg-backend/app/services/storage_service.py`

**Functions to Move**:
- Generate presigned URLs for video download
- Manage video file lifecycle (upload, processing, cleanup)
- Handle file naming conventions and organization
- Cleanup temporary files after processing
- Generate final download URLs

**Why**: File management is a business concern, not a rendering concern. Backend should handle all storage operations.

**Backend Implementation**:
```python
class VideoStorageService:
    async def get_processing_url(self, video_id: str) -> str:
        """Get temporary URL for worker processing"""

    async def store_final_video(self, job_id: str, file_data: bytes) -> str:
        """Store final rendered video and return download URL"""

    async def cleanup_job_files(self, job_id: str):
        """Clean up all temporary files for job"""
```

### 5. **Scenario Processing and Validation**

**Current Location**: Logic scattered in `render_engine.py`
**Should Move to**: `ecg-backend/app/services/scenario_service.py`

**Functions to Move**:
- Scenario format validation and parsing
- Split scenario cues by time segments for workers
- Merge and validate cue timing
- Handle cue overlaps and conflicts
- Apply scenario templates and defaults

**Why**: Scenario processing is business logic that should be handled before reaching render workers.

**Backend Implementation**:
```python
class ScenarioProcessingService:
    async def validate_scenario(self, scenario: dict) -> bool:
        """Validate scenario format and content"""

    async def split_by_segments(self, scenario: dict, segments: list) -> list:
        """Split scenario cues by time segments for workers"""

    async def merge_segment_results(self, segment_results: list) -> dict:
        """Merge results from multiple workers"""
```

## Simplified GPU Render Server Architecture

After moving the above functions to backend, the GPU Render Server becomes much simpler:

### **New Simplified Structure**:
```
ecg-render/
├── main.py                    # Celery worker entry point only
├── render_engine.py          # Core rendering: Playwright + FFmpeg only
├── app/
│   ├── pipeline/             # Streaming, memory, FFmpeg
│   ├── services/             # Browser management only
│   └── core/                 # Config, errors (minimal)
```

### **Simplified Responsibilities**:
1. **Receive segment rendering tasks** from backend via Celery
2. **Render video segments** using Playwright + FFmpeg
3. **Report progress and results** back to backend
4. **Stream-optimized frame processing** (Phase 2 pipeline)

### **What Stays in GPU Render Server**:
- Core rendering logic (Playwright + FFmpeg)
- GPU memory management and optimization
- Streaming pipeline and frame processing
- Browser pool management
- Low-level performance optimizations

## Integration Protocol

### **Backend → GPU Render Server**:
```python
# Backend sends fully prepared segment tasks
segment_task = {
    "job_id": "uuid",
    "worker_id": 0,
    "start_time": 0.0,
    "end_time": 7.5,
    "start_frame": 0,
    "end_frame": 225,
    "cues": [...],  # Pre-filtered for this segment
    "video_url": "s3://presigned-url",  # Ready for download
    "scenario_metadata": {...}  # Pre-extracted metadata
}
```

### **GPU Render Server → Backend**:
```python
# Worker reports simple results
segment_result = {
    "worker_id": 0,
    "success": True,
    "frames_processed": 225,
    "output_path": "/tmp/segment_0.mp4",
    "file_size": 1048576,
    "processing_time": 12.5
}
```

## Benefits of This Architecture

### **Reduced Complexity**:
- GPU server focuses only on rendering
- No complex orchestration logic in workers
- Cleaner separation of concerns

### **Better Scalability**:
- Backend can manage multiple GPU servers
- Easier to add/remove render workers
- Better resource utilization

### **Improved Reliability**:
- Job management centralized in backend
- Better error handling and recovery
- Easier debugging and monitoring

### **Easier Maintenance**:
- Smaller, focused codebases
- Clear responsibility boundaries
- Simpler testing and deployment

## Migration Steps

1. **Phase 1**: Move job orchestration to backend
2. **Phase 2**: Move video preprocessing to backend
3. **Phase 3**: Move S3 management to backend
4. **Phase 4**: Move scenario processing to backend
5. **Phase 5**: Simplify GPU server to worker-only mode

Each phase can be done incrementally while maintaining backward compatibility.

## Current Status After Refactoring

✅ **Completed in ecg-render**:
- Removed redundant dual-mode complexity
- Simplified memory management (68% reduction)
- Consolidated FFmpeg services
- Streamlined render engine logic
- Cleaned up configuration

🔄 **Ready for Backend Migration**:
- Job orchestration logic
- Progress aggregation
- Video preprocessing
- S3 file management
- Scenario processing

The codebase is now ready for these functions to be moved to the backend server for a cleaner architecture.