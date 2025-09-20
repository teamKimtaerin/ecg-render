#!/usr/bin/env python3
"""
ECG GPU Render Server - Unified Entry Point
Supports both Standalone FastAPI server and Celery Worker modes
"""

import os
import sys
import argparse
import logging
from typing import Optional

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

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


def get_runtime_mode() -> str:
    """Determine runtime mode from environment variables and arguments"""
    # Check environment variable first
    env_mode = os.getenv("ECG_RENDER_MODE", "").lower()
    if env_mode in ["standalone", "worker", "celery"]:
        return "worker" if env_mode in ["worker", "celery"] else "standalone"

    # Default to standalone
    return "standalone"


def run_standalone_server(host: str = "0.0.0.0", port: int = 8090, workers: int = 1, log_level: str = "info"):
    """Run FastAPI server in standalone mode"""
    import uvicorn
    from render_server import app

    logger = logging.getLogger(__name__)

    # Configuration summary
    logger.info("🖥️  ECG GPU Render Server - Standalone Mode")
    logger.info(f"   📍 Address: {host}:{port}")
    logger.info(f"   👥 Uvicorn Workers: {workers}")
    logger.info(f"   🔧 Render Workers: {os.getenv('MAX_CONCURRENT_JOBS', '3')}")
    logger.info(f"   🎮 GPU Mode: {'Enabled' if check_gpu_available() else 'CPU Fallback'}")
    logger.info(f"   📦 Redis: {os.getenv('REDIS_URL', 'redis://localhost:6379')}")
    logger.info(f"   ☁️  S3 Bucket: {os.getenv('S3_BUCKET', 'ecg-rendered-videos')}")
    logger.info(f"   🔄 Callback URL: {os.getenv('BACKEND_CALLBACK_URL', 'Not configured')}")

    # Parallel rendering info
    parallel_mode = os.getenv("USE_PARALLEL_RENDERING", "true").lower() == "true"
    browser_pool = os.getenv("BROWSER_POOL_SIZE", "4")
    logger.info(f"   🚀 Parallel Rendering: {'Enabled' if parallel_mode else 'Disabled'}")
    if parallel_mode:
        logger.info(f"   🌐 Browser Pool Size: {browser_pool} per worker")

    logger.info("Starting server...")

    # Run FastAPI server
    uvicorn.run(
        "render_server:app",
        host=host,
        port=port,
        workers=workers,
        access_log=True,
        log_level=log_level,
        reload=False
    )


def run_celery_worker(log_level: str = "info", concurrency: int = 1):
    """Run Celery worker mode"""
    import subprocess

    logger = logging.getLogger(__name__)

    # Configuration summary
    logger.info("🔧 ECG GPU Render Server - Celery Worker Mode")
    logger.info(f"   🎮 GPU Mode: {'Enabled' if check_gpu_available() else 'CPU Fallback'}")
    logger.info(f"   👥 Concurrency: {concurrency}")
    logger.info(f"   📦 Broker: {os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')}")
    logger.info(f"   💾 Backend: {os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/1')}")
    logger.info(f"   ☁️  S3 Bucket: {os.getenv('S3_BUCKET', 'ecg-rendered-videos')}")

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
            "celery_worker.py"
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
    config_vars = [
        ("REDIS_URL", "redis://localhost:6379"),
        ("S3_BUCKET", "ecg-rendered-videos"),
        ("MAX_CONCURRENT_JOBS", "3"),
        ("USE_PARALLEL_RENDERING", "true"),
        ("BROWSER_POOL_SIZE", "4"),
        ("BACKEND_CALLBACK_URL", "Not configured"),
        ("CELERY_BROKER_URL", "redis://localhost:6379/0"),
        ("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
    ]

    for var, default in config_vars:
        value = os.getenv(var, default)
        logger.info(f"   {var}: {value}")

    logger.info("=" * 50)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="ECG GPU Render Server - Unified Entry Point"
    )

    # Mode selection
    parser.add_argument(
        "--mode",
        choices=["standalone", "worker", "auto"],
        default="auto",
        help="Runtime mode: standalone (FastAPI server), worker (Celery), auto (from environment)"
    )

    # Server configuration
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (standalone mode)")
    parser.add_argument("--port", type=int, default=8090, help="Bind port (standalone mode)")
    parser.add_argument("--workers", type=int, default=1, help="Uvicorn workers (standalone mode)")
    parser.add_argument("--concurrency", type=int, default=1, help="Celery concurrency (worker mode)")

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

    # Determine runtime mode
    if args.mode == "auto":
        mode = get_runtime_mode()
    else:
        mode = args.mode

    # Run in selected mode
    try:
        if mode == "standalone":
            run_standalone_server(
                host=args.host,
                port=args.port,
                workers=args.workers,
                log_level=args.log_level
            )
        elif mode == "worker":
            run_celery_worker(
                log_level=args.log_level,
                concurrency=args.concurrency
            )
        else:
            raise ValueError(f"Unknown mode: {mode}")

    except KeyboardInterrupt:
        logger = logging.getLogger(__name__)
        logger.info("Server stopped by user")
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Server failed to start: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()