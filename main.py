#!/usr/bin/env python3
"""
ECG GPU Render Server - Celery Worker
Simplified entry point for GPU rendering tasks
"""

import os
import sys
import argparse
import logging
import subprocess
from typing import Optional

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import settings after loading env vars
from app.core.config import settings

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def setup_logging(log_level: str = "INFO") -> None:
    """Setup centralized logging configuration"""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("/tmp/ecg-render.log") if os.path.exists("/tmp") else logging.NullHandler()
        ]
    )


def run_celery_worker(log_level: str = "info", concurrency: int = 1):
    """Run Celery worker for GPU rendering"""
    import subprocess

    logger = logging.getLogger(__name__)

    # Configuration summary
    logger.info("🔧 ECG GPU Render Server - Celery Worker")
    logger.info(f"   🎮 GPU Mode: {'Enabled' if check_gpu_available() else 'CPU Fallback'}")
    logger.info(f"   👥 Concurrency: {concurrency}")
    logger.info(f"   📦 Broker: {settings.CELERY_BROKER_URL}")
    logger.info(f"   💾 Backend: {settings.CELERY_RESULT_BACKEND}")
    logger.info(f"   ☁️  S3 Bucket: {settings.S3_BUCKET}")

    # Phase 2 optimizations info
    logger.info("   🚀 Phase 2 Optimizations: Enabled")
    logger.info("     - Streaming Pipeline: Active")
    logger.info("     - Memory Optimization: Active")
    logger.info("     - Intelligent Segment Merging: Active")

    logger.info("Starting Celery worker...")

    # Import and run celery worker
    try:
        celery_cmd = [
            sys.executable,
            "app/workers/celery.py"
        ]

        # Run celery worker
        result = subprocess.run(celery_cmd)
        sys.exit(result.returncode)

    except KeyboardInterrupt:
        logger.info("Celery worker stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to start Celery worker: {e}")
        sys.exit(1)


def check_gpu_available() -> bool:
    """Check if GPU is available for rendering"""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def show_system_info():
    """Display system information and configuration"""
    logger = logging.getLogger(__name__)

    logger.info("🎬 ECG GPU Render Server System Information")
    logger.info("=" * 50)

    # GPU Information
    gpu_available = check_gpu_available()
    logger.info(f"🎮 GPU Status: {'Available' if gpu_available else 'Not Available'}")

    if gpu_available:
        try:
            import torch
            gpu_count = torch.cuda.device_count()
            logger.info(f"   GPU Count: {gpu_count}")

            for i in range(gpu_count):
                props = torch.cuda.get_device_properties(i)
                logger.info(f"   GPU {i}: {props.name}")
                logger.info(f"     Memory: {props.total_memory / 1024**3:.1f} GB")
                logger.info(f"     CUDA Cores: {props.multi_processor_count}")
        except Exception as e:
            logger.warning(f"Could not get detailed GPU info: {e}")

    # FFmpeg GPU Encoding
    try:
        import subprocess
        result = subprocess.run(
            ['ffmpeg', '-hide_banner', '-encoders'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if 'h264_nvenc' in result.stdout:
            logger.info("✅ NVENC GPU Encoding: Available")
        else:
            logger.info("⚠️ NVENC GPU Encoding: Not Available")
    except Exception:
        logger.info("❓ NVENC GPU Encoding: Unknown")

    # Environment Configuration
    logger.info("\n📝 Configuration:")
    # Display current settings
    logger.info(f"   REDIS_URL: {settings.REDIS_URL}")
    logger.info(f"   S3_BUCKET: {settings.S3_BUCKET}")
    logger.info(f"   MAX_CONCURRENT_JOBS: {settings.MAX_CONCURRENT_JOBS}")
    logger.info(f"   ENABLE_STREAMING_PIPELINE: {settings.ENABLE_STREAMING_PIPELINE}")
    logger.info(f"   BACKEND_CALLBACK_URL: {settings.BACKEND_CALLBACK_URL}")
    logger.info(f"   CELERY_BROKER_URL: {settings.CELERY_BROKER_URL}")
    logger.info(f"   CELERY_RESULT_BACKEND: {settings.CELERY_RESULT_BACKEND}")

    logger.info("=" * 50)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="ECG GPU Render Server - Celery Worker"
    )

    # Worker configuration
    parser.add_argument("--concurrency", type=int, default=1, help="Celery concurrency")

    # General options
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Logging level"
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help="Show system information and exit"
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.log_level)

    # Show system info if requested
    if args.info:
        show_system_info()
        return

    # Run Celery worker
    try:
        run_celery_worker(
            log_level=args.log_level,
            concurrency=args.concurrency
        )
    except KeyboardInterrupt:
        logger = logging.getLogger(__name__)
        logger.info("Worker stopped by user")
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Worker failed to start: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()