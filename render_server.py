"""
GPU Render Server for MotionText Video Processing
EC2 GPU 인스턴스에서 실행되는 렌더링 서버
"""

import sys
from pathlib import Path
import logging
import os
import warnings
from typing import Dict, Any, Optional
from datetime import datetime
import asyncio
import uuid

from dotenv import load_dotenv
load_dotenv()

warnings.filterwarnings("ignore", category=UserWarning)

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uvicorn

from modules.queue import RenderQueue, RenderJob
from modules.worker import RenderWorker
from modules.parallel_worker import ParallelRenderWorker
from src.logger import get_logger

# Error code constants
class ErrorCodes:
    GPU_MEMORY_INSUFFICIENT = "GPU_MEMORY_INSUFFICIENT"
    INVALID_VIDEO_FORMAT = "INVALID_VIDEO_FORMAT"
    SCENARIO_PARSE_ERROR = "SCENARIO_PARSE_ERROR"
    RENDERING_TIMEOUT = "RENDERING_TIMEOUT"
    STORAGE_ACCESS_ERROR = "STORAGE_ACCESS_ERROR"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    RENDER_ERROR = "RENDER_ERROR"
    CANCELLED = "CANCELLED"

# FastAPI 앱 생성
app = FastAPI(
    title="GPU Render Server",
    description="GPU 기반 MotionText 비디오 렌더링 서버",
    version="1.0.0",
)

# 환경 설정
S3_BUCKET = os.getenv("S3_BUCKET", "ecg-rendered-videos")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
BACKEND_CALLBACK_URL = os.getenv("BACKEND_CALLBACK_URL", "")
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "3"))
TEMP_DIR = os.getenv("TEMP_DIR", "/tmp/render")
CALLBACK_RETRY_COUNT = int(os.getenv("CALLBACK_RETRY_COUNT", "3"))
CALLBACK_TIMEOUT = int(os.getenv("CALLBACK_TIMEOUT", "30"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Parallel rendering configuration
USE_PARALLEL_RENDERING = os.getenv("USE_PARALLEL_RENDERING", "true").lower() == "true"
BROWSER_POOL_SIZE = int(os.getenv("BROWSER_POOL_SIZE", "4"))

# Render mode configuration (celery or standalone)
RENDER_MODE = os.getenv("RENDER_MODE", "standalone")  # "celery" or "standalone"

# 로거 설정
logger = get_logger(__name__)

# 전역 객체
render_queue: Optional[RenderQueue] = None
render_workers: list[RenderWorker] = []
worker_tasks: list[asyncio.Task] = []


# ========== Pydantic Models ==========

class RenderRequest(BaseModel):
    """렌더링 요청 모델"""
    jobId: str
    videoUrl: str
    scenario: Dict[str, Any]  # MotionText 시나리오
    options: Dict[str, Any] = {
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "quality": 90,
        "format": "mp4"
    }
    callbackUrl: str


class RenderResponse(BaseModel):
    """렌더링 응답 모델"""
    status: str
    job_id: str
    message: str
    estimated_time: Optional[int] = None


class JobStatus(BaseModel):
    """작업 상태 모델"""
    job_id: str
    status: str
    progress: int
    message: Optional[str] = None
    download_url: Optional[str] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    estimated_time_remaining: Optional[int] = None


# ========== Startup/Shutdown Events ==========

@app.on_event("startup")
async def startup_event():
    """서버 시작 시 초기화"""
    global render_queue, render_workers, worker_tasks

    logger.info("GPU 렌더링 서버 초기화 중...")

    # Redis 큐 초기화
    render_queue = RenderQueue(redis_url=REDIS_URL)

    # 워커 설정
    worker_config = {
        "s3_bucket": S3_BUCKET,
        "temp_dir": TEMP_DIR,
    }

    # 워커 생성 및 시작
    if USE_PARALLEL_RENDERING:
        # Use parallel workers for better performance
        logger.info(f"🚀 Using PARALLEL rendering with {BROWSER_POOL_SIZE} browser instances per worker")

        for i in range(MAX_CONCURRENT_JOBS):
            worker = ParallelRenderWorker(
                render_queue,
                worker_config,
                pool_size=BROWSER_POOL_SIZE
            )
            render_workers.append(worker)

            # 비동기 태스크로 워커 시작
            task = asyncio.create_task(worker.start())
            worker_tasks.append(task)

            logger.info(f"병렬 렌더링 워커 {i+1} 시작됨 (pool size: {BROWSER_POOL_SIZE})")
    else:
        # Use legacy sequential workers
        logger.info("Using legacy sequential rendering")

        for i in range(MAX_CONCURRENT_JOBS):
            worker = RenderWorker(render_queue, worker_config)
            render_workers.append(worker)

            # 비동기 태스크로 워커 시작
            task = asyncio.create_task(worker.start())
            worker_tasks.append(task)

            logger.info(f"렌더링 워커 {i+1} 시작됨")

    # GPU 상태 확인
    check_gpu_status()

    if USE_PARALLEL_RENDERING:
        total_browsers = MAX_CONCURRENT_JOBS * BROWSER_POOL_SIZE
        logger.info(f"✅ GPU 렌더링 서버 준비 완료 (워커: {MAX_CONCURRENT_JOBS}개, 총 브라우저: {total_browsers}개)")
    else:
        logger.info(f"✅ GPU 렌더링 서버 준비 완료 (워커: {MAX_CONCURRENT_JOBS}개)")


@app.on_event("shutdown")
async def shutdown_event():
    """서버 종료 시 정리"""
    global render_workers, worker_tasks

    logger.info("GPU 렌더링 서버 종료 중...")

    # 모든 워커 정지
    for worker in render_workers:
        await worker.stop()

    # 워커 태스크 취소
    for task in worker_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    logger.info("✅ GPU 렌더링 서버 종료 완료")


# ========== Helper Functions ==========

def check_gpu_status():
    """GPU 상태 확인"""
    try:
        import torch

        if torch.cuda.is_available():
            gpu_count = torch.cuda.device_count()
            logger.info(f"🎮 GPU 사용 가능: {gpu_count}개")

            for i in range(gpu_count):
                props = torch.cuda.get_device_properties(i)
                logger.info(f"  GPU {i}: {props.name}")
                logger.info(f"    - 메모리: {props.total_memory / 1024**3:.1f} GB")
                logger.info(f"    - CUDA 코어: {props.multi_processor_count}")
        else:
            logger.warning("⚠️ GPU를 사용할 수 없습니다. CPU 모드로 실행됩니다.")

    except ImportError:
        logger.warning("PyTorch가 설치되지 않았습니다.")

    # FFmpeg GPU 인코딩 확인
    try:
        import subprocess
        result = subprocess.run(
            ['ffmpeg', '-hide_banner', '-encoders'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if 'h264_nvenc' in result.stdout:
            logger.info("✅ NVENC GPU 인코딩 사용 가능")
        else:
            logger.warning("⚠️ NVENC GPU 인코딩을 사용할 수 없습니다.")

    except Exception as e:
        logger.error(f"FFmpeg 확인 실패: {e}")


# ========== API Endpoints ==========

@app.post("/render", response_model=RenderResponse)
async def process_render_job(request: RenderRequest):
    """렌더링 작업 수신 및 큐에 추가"""
    try:
        logger.info(f"렌더링 요청 수신 - job_id: {request.jobId}")

        # RenderJob 생성
        job = RenderJob(
            job_id=request.jobId,
            video_url=request.videoUrl,
            scenario=request.scenario,
            options=request.options,
            callback_url=request.callbackUrl
        )

        # 큐에 추가
        await render_queue.add_job(job)

        logger.info(f"작업이 큐에 추가됨 - job_id: {request.jobId}")

        return RenderResponse(
            status="accepted",
            job_id=request.jobId,
            message="Job queued for processing",
            estimated_time=180  # 기본 예상 시간 3분
        )

    except Exception as e:
        logger.error(f"작업 추가 실패: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue job: {str(e)}"
        )


@app.post("/api/render/{job_id}/cancel")
async def cancel_render_job(job_id: str):
    """렌더링 작업 취소"""
    try:
        success = render_queue.cancel_job(job_id)

        if success:
            logger.info(f"작업 취소됨 - job_id: {job_id}")
            return {"success": True, "message": "Job cancelled successfully"}
        else:
            return {"success": False, "message": "Job not found or already completed"}

    except Exception as e:
        logger.error(f"작업 취소 실패: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cancel job: {str(e)}"
        )


@app.get("/api/render/{job_id}/status", response_model=JobStatus)
async def get_job_status(job_id: str):
    """작업 상태 조회"""
    try:
        job = render_queue.get_job(job_id)

        if not job:
            raise HTTPException(
                status_code=404,
                detail="Job not found"
            )

        return JobStatus(
            job_id=job.job_id,
            status=job.status,
            progress=job.progress,
            message=f"Job is {job.status}",
            error_message=job.error_message
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"상태 조회 실패: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get job status: {str(e)}"
        )


@app.get("/health")
async def health_check():
    """헬스체크 엔드포인트"""
    try:
        queue_status = render_queue.get_queue_status() if render_queue else {}

        # GPU 메모리 체크
        gpu_info = {}
        try:
            import torch
            if torch.cuda.is_available():
                for i in range(torch.cuda.device_count()):
                    allocated = torch.cuda.memory_allocated(i) / 1024**3
                    reserved = torch.cuda.memory_reserved(i) / 1024**3
                    gpu_info[f"gpu_{i}"] = {
                        "allocated_gb": round(allocated, 2),
                        "reserved_gb": round(reserved, 2)
                    }
        except:
            pass

        return {
            "status": "healthy",
            "gpu_count": len(gpu_info) if gpu_info else 0,
            "available_memory": f"{sum([24 - info.get('reserved_gb', 0) for info in gpu_info.values()])}GB" if gpu_info else "Unknown",
            "queue_length": queue_status.get('pending', 0) if queue_status else 0,
            "timestamp": datetime.now().isoformat(),
            "queue": queue_status,
            "gpu": gpu_info,
            "workers": len(render_workers),
            "parallel_rendering": USE_PARALLEL_RENDERING,
            "browser_pool_size": BROWSER_POOL_SIZE if USE_PARALLEL_RENDERING else 0
        }

    except Exception as e:
        logger.error(f"헬스체크 실패: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


@app.get("/queue/status")
async def get_queue_status():
    """큐 상태 조회"""
    try:
        status = render_queue.get_queue_status()
        # 예상 대기 시간 계산 (관예상 작업별 3분)
        pending_jobs = status.get('pending', 0)
        processing_jobs = status.get('processing', 0)
        estimated_wait_time = pending_jobs * 180  # 3분 per job

        return {
            "pending_jobs": pending_jobs,
            "processing_jobs": processing_jobs,
            "estimated_wait_time": estimated_wait_time,
            "detailed_status": status
        }

    except Exception as e:
        logger.error(f"큐 상태 조회 실패: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get queue status: {str(e)}"
        )


@app.post("/api/render/queue/clear")
async def clear_queue():
    """큐 초기화 (테스트용)"""
    try:
        if os.getenv("ENVIRONMENT") != "production":
            render_queue.clear_queue()
            return {"success": True, "message": "Queue cleared"}
        else:
            raise HTTPException(
                status_code=403,
                detail="Queue clearing is disabled in production"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"큐 초기화 실패: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear queue: {str(e)}"
        )


# ========== Main ==========

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GPU Render Server")
    parser.add_argument("--host", default="0.0.0.0", help="바인드 호스트")
    parser.add_argument("--port", type=int, default=8090, help="바인드 포트")
    parser.add_argument("--workers", type=int, default=1, help="유비콘 워커 수")
    parser.add_argument(
        "--log-level", default="info",
        choices=["debug", "info", "warning", "error"]
    )
    parser.add_argument(
        "--mode", default=RENDER_MODE,
        choices=["standalone", "celery"],
        help="실행 모드: standalone (독립 서버) 또는 celery (워커)"
    )

    args = parser.parse_args()

    # Override render mode if specified
    if args.mode:
        RENDER_MODE = args.mode

    # 로깅 설정
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("🚀 GPU 렌더링 서버 시작")
    logger.info(f"   실행 모드: {RENDER_MODE}")
    logger.info(f"   호스트: {args.host}:{args.port}")
    logger.info(f"   Redis: {REDIS_URL}")
    logger.info(f"   S3 버킷: {S3_BUCKET}")

    if RENDER_MODE == "celery":
        # Celery worker mode
        logger.info("🔧 Celery Worker 모드로 실행")
        logger.info("   Celery worker를 시작하려면 다음 명령어를 사용하세요:")
        logger.info("   python celery_worker.py")
        logger.info("")
        logger.info("   또는 Docker Compose로 실행:")
        logger.info("   docker-compose -f docker-compose.celery.yml up -d")

        # Import and run celery worker
        import subprocess
        import sys

        celery_cmd = [
            sys.executable,
            "celery_worker.py"
        ]

        logger.info(f"Celery worker 시작 중...")
        subprocess.run(celery_cmd)

    else:
        # Standalone server mode
        logger.info("🖥️ Standalone 서버 모드로 실행")
        logger.info(f"   유비콘 워커: {args.workers}")
        logger.info(f"   렌더링 워커: {MAX_CONCURRENT_JOBS}")
        logger.info(f"   콜백 URL: {BACKEND_CALLBACK_URL if BACKEND_CALLBACK_URL else 'Not configured'}")
        logger.info(f"   병렬 렌더링: {'활성화' if USE_PARALLEL_RENDERING else '비활성화'}")
        if USE_PARALLEL_RENDERING:
            logger.info(f"   브라우저 풀 크기: {BROWSER_POOL_SIZE}개/워커")

        # FastAPI 서버 실행
        uvicorn.run(
            "render_server:app",
            host=args.host,
            port=args.port,
            workers=args.workers,
            access_log=True,
            log_level=args.log_level,
            reload=False
        )