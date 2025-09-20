from typing import List, Union, Optional
from pydantic_settings import BaseSettings
from pydantic import Field, validator
import os


class Settings(BaseSettings):
    # App Settings
    app_name: str = Field(default="ECG GPU Render Server", description="Application name")
    debug: bool = Field(default=False, description="Debug mode")
    log_level: str = Field(default="INFO", description="Logging level")

    # Redis Settings (Celery Broker)
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL for Celery broker and caching"
    )
    CELERY_BROKER_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Celery broker URL"
    )
    CELERY_RESULT_BACKEND: str = Field(
        default="redis://localhost:6379/0",
        description="Celery result backend URL"
    )

    # AWS Settings
    AWS_ACCESS_KEY_ID: str = Field(default="", description="AWS Access Key ID")
    AWS_SECRET_ACCESS_KEY: str = Field(default="", description="AWS Secret Access Key")
    AWS_REGION: str = Field(default="ap-northeast-2", description="AWS Region")

    # S3 Settings
    S3_BUCKET: str = Field(default="ecg-rendered-videos", description="S3 bucket name for rendered videos")
    S3_PRESIGNED_URL_EXPIRE: int = Field(
        default=3600, description="Presigned URL expiration time in seconds"
    )

    # Backend Integration Settings
    BACKEND_CALLBACK_URL: str = Field(
        default="http://localhost:8000",
        description="Backend API URL for progress callbacks"
    )
    CALLBACK_RETRY_COUNT: int = Field(
        default=3, description="Number of callback retry attempts"
    )
    CALLBACK_TIMEOUT: int = Field(
        default=30, description="Callback request timeout in seconds"
    )

    # GPU and Rendering Settings
    MAX_CONCURRENT_JOBS: int = Field(
        default=3, description="Maximum concurrent rendering jobs"
    )
    RENDERING_TIMEOUT: int = Field(
        default=1800, description="Rendering timeout in seconds (30 minutes)"
    )
    TEMP_DIR: str = Field(
        default="/tmp/render", description="Temporary processing directory"
    )

    # Browser Settings
    BROWSER_POOL_SIZE: int = Field(
        default=4, description="Playwright browser pool size"
    )
    BROWSER_TIMEOUT: int = Field(
        default=60000, description="Browser operation timeout in milliseconds"
    )


    # Phase 2 Streaming Pipeline Settings
    ENABLE_STREAMING_PIPELINE: bool = Field(
        default=True, description="Enable Phase 2 streaming pipeline optimization"
    )
    ENABLE_MEMORY_OPTIMIZER: bool = Field(
        default=True, description="Enable automatic memory optimization"
    )

    # FFmpeg Settings
    USE_GPU_ENCODING: bool = Field(
        default=True, description="Use GPU hardware encoding (NVENC)"
    )





    @validator("TEMP_DIR", pre=True)
    def create_temp_dir(cls, v):
        """Ensure temp directory exists"""
        if v and not os.path.exists(v):
            os.makedirs(v, exist_ok=True)
        return v


    @validator("log_level", pre=True)
    def validate_log_level(cls, v):
        """Validate log level"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"log_level must be one of {valid_levels}")
        return v.upper()

    class Config:
        env_file = ".env"
        case_sensitive = True  # GPU settings are case-sensitive
        extra = "ignore"


settings = Settings()