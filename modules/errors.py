"""
중앙화된 에러 코드 및 에러 처리 클래스
Backend와 GPU Render Server 간 일관된 에러 처리를 위한 모듈
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


class ErrorCodes:
    """표준화된 에러 코드 상수"""

    # 시스템 에러
    GPU_MEMORY_INSUFFICIENT = "GPU_MEMORY_INSUFFICIENT"
    GPU_NOT_AVAILABLE = "GPU_NOT_AVAILABLE"
    SYSTEM_RESOURCE_ERROR = "SYSTEM_RESOURCE_ERROR"

    # 입력 검증 에러
    INVALID_VIDEO_FORMAT = "INVALID_VIDEO_FORMAT"
    INVALID_VIDEO_URL = "INVALID_VIDEO_URL"
    SCENARIO_PARSE_ERROR = "SCENARIO_PARSE_ERROR"
    INVALID_RENDER_OPTIONS = "INVALID_RENDER_OPTIONS"

    # 렌더링 에러
    RENDERING_TIMEOUT = "RENDERING_TIMEOUT"
    RENDER_ERROR = "RENDER_ERROR"
    BROWSER_ERROR = "BROWSER_ERROR"
    FFMPEG_ERROR = "FFMPEG_ERROR"

    # 네트워크 및 스토리지 에러
    STORAGE_ACCESS_ERROR = "STORAGE_ACCESS_ERROR"
    S3_UPLOAD_ERROR = "S3_UPLOAD_ERROR"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    CALLBACK_FAILED = "CALLBACK_FAILED"

    # 작업 관리 에러
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_ALREADY_EXISTS = "JOB_ALREADY_EXISTS"
    JOB_CANCELLED = "JOB_CANCELLED"
    QUEUE_FULL = "QUEUE_FULL"

    # Phase 2 스트리밍 에러
    STREAMING_ERROR = "STREAMING_ERROR"
    MEMORY_OPTIMIZATION_ERROR = "MEMORY_OPTIMIZATION_ERROR"
    FRAME_DROP_LIMIT_EXCEEDED = "FRAME_DROP_LIMIT_EXCEEDED"

    # 일반 에러
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"


@dataclass
class RenderError:
    """렌더링 에러 정보"""
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    suggestions: Optional[str] = None
    job_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환"""
        return {
            "error_code": self.code,
            "error_message": self.message,
            "details": self.details,
            "suggestions": self.suggestions,
            "job_id": self.job_id
        }

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class RenderException(Exception):
    """렌더링 관련 예외 클래스"""

    def __init__(self, error: RenderError):
        self.error = error
        super().__init__(str(error))

    def to_dict(self) -> Dict[str, Any]:
        return self.error.to_dict()


class ErrorFactory:
    """에러 객체 생성 팩토리"""

    @staticmethod
    def gpu_memory_insufficient(job_id: str = None, required_gb: float = None) -> RenderError:
        """GPU 메모리 부족 에러"""
        details = {"required_memory_gb": required_gb} if required_gb else None
        return RenderError(
            code=ErrorCodes.GPU_MEMORY_INSUFFICIENT,
            message="Insufficient GPU memory for rendering",
            details=details,
            suggestions="Reduce concurrent jobs or use a GPU with more memory",
            job_id=job_id
        )

    @staticmethod
    def invalid_video_format(job_id: str = None, format_info: str = None) -> RenderError:
        """지원하지 않는 비디오 포맷 에러"""
        details = {"format_info": format_info} if format_info else None
        return RenderError(
            code=ErrorCodes.INVALID_VIDEO_FORMAT,
            message="Unsupported video format",
            details=details,
            suggestions="Use supported formats: MP4, MOV, AVI",
            job_id=job_id
        )

    @staticmethod
    def scenario_parse_error(job_id: str = None, parse_details: str = None) -> RenderError:
        """시나리오 파싱 에러"""
        details = {"parse_error": parse_details} if parse_details else None
        return RenderError(
            code=ErrorCodes.SCENARIO_PARSE_ERROR,
            message="Failed to parse scenario data",
            details=details,
            suggestions="Check scenario format and structure",
            job_id=job_id
        )

    @staticmethod
    def rendering_timeout(job_id: str = None, timeout_seconds: int = None) -> RenderError:
        """렌더링 타임아웃 에러"""
        details = {"timeout_seconds": timeout_seconds} if timeout_seconds else None
        return RenderError(
            code=ErrorCodes.RENDERING_TIMEOUT,
            message="Rendering operation timed out",
            details=details,
            suggestions="Try reducing video length or complexity",
            job_id=job_id
        )

    @staticmethod
    def storage_access_error(job_id: str = None, operation: str = None) -> RenderError:
        """스토리지 접근 에러"""
        details = {"operation": operation} if operation else None
        return RenderError(
            code=ErrorCodes.STORAGE_ACCESS_ERROR,
            message="Failed to access storage",
            details=details,
            suggestions="Check AWS credentials and S3 permissions",
            job_id=job_id
        )

    @staticmethod
    def connection_error(job_id: str = None, target: str = None) -> RenderError:
        """연결 에러"""
        details = {"target": target} if target else None
        return RenderError(
            code=ErrorCodes.CONNECTION_ERROR,
            message="Connection failed",
            details=details,
            suggestions="Check network connectivity and target service",
            job_id=job_id
        )

    @staticmethod
    def job_not_found(job_id: str) -> RenderError:
        """작업을 찾을 수 없음"""
        return RenderError(
            code=ErrorCodes.JOB_NOT_FOUND,
            message=f"Job {job_id} not found",
            suggestions="Check job ID and ensure job exists",
            job_id=job_id
        )

    @staticmethod
    def streaming_error(job_id: str = None, pipeline_stage: str = None) -> RenderError:
        """스트리밍 파이프라인 에러"""
        details = {"pipeline_stage": pipeline_stage} if pipeline_stage else None
        return RenderError(
            code=ErrorCodes.STREAMING_ERROR,
            message="Streaming pipeline error",
            details=details,
            suggestions="Check memory usage and pipeline configuration",
            job_id=job_id
        )

    @staticmethod
    def unexpected_error(job_id: str = None, original_error: str = None) -> RenderError:
        """예상치 못한 에러"""
        details = {"original_error": original_error} if original_error else None
        return RenderError(
            code=ErrorCodes.UNEXPECTED_ERROR,
            message="An unexpected error occurred",
            details=details,
            suggestions="Contact support if this error persists",
            job_id=job_id
        )


def handle_render_exception(func):
    """렌더링 예외를 처리하는 데코레이터"""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except RenderException:
            # RenderException은 그대로 전파
            raise
        except Exception as e:
            # 일반 예외는 RenderException으로 변환
            logger.error(f"Unexpected error in {func.__name__}: {e}")
            error = ErrorFactory.unexpected_error(
                original_error=str(e)
            )
            raise RenderException(error)
    return wrapper


async def handle_async_render_exception(func):
    """비동기 렌더링 예외를 처리하는 데코레이터"""
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except RenderException:
            # RenderException은 그대로 전파
            raise
        except Exception as e:
            # 일반 예외는 RenderException으로 변환
            logger.error(f"Unexpected error in {func.__name__}: {e}")
            error = ErrorFactory.unexpected_error(
                original_error=str(e)
            )
            raise RenderException(error)
    return wrapper