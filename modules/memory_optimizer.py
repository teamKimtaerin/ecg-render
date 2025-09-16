"""
Memory Optimizer for GPU Render Server
Manages memory allocation, monitoring, and optimization strategies
"""

import asyncio
import gc
import logging
import psutil
import torch
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class MemorySnapshot:
    """Memory usage snapshot"""
    timestamp: datetime
    process_rss: int  # Resident Set Size in bytes
    process_vms: int  # Virtual Memory Size in bytes
    system_available: int
    system_percent: float
    gpu_allocated: Optional[int] = None
    gpu_reserved: Optional[int] = None
    gpu_free: Optional[int] = None


class MemoryMonitor:
    """
    Real-time memory monitoring with predictive analysis
    """

    def __init__(self, interval: float = 1.0, history_size: int = 60):
        """
        Initialize memory monitor

        Args:
            interval: Monitoring interval in seconds
            history_size: Number of snapshots to keep
        """
        self.interval = interval
        self.history_size = history_size
        self.history: List[MemorySnapshot] = []
        self.is_monitoring = False
        self.monitor_task: Optional[asyncio.Task] = None
        self.process = psutil.Process()

    async def start(self) -> None:
        """Start monitoring"""
        if self.is_monitoring:
            return

        self.is_monitoring = True
        self.monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Memory monitoring started")

    async def stop(self) -> None:
        """Stop monitoring"""
        self.is_monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Memory monitoring stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop"""
        while self.is_monitoring:
            try:
                snapshot = await self._capture_snapshot()
                self.history.append(snapshot)

                # Maintain history size
                if len(self.history) > self.history_size:
                    self.history.pop(0)

                # Check for memory pressure
                if self._is_memory_critical(snapshot):
                    await self._handle_memory_pressure(snapshot)

                await asyncio.sleep(self.interval)

            except Exception as e:
                logger.error(f"Monitor error: {e}")
                await asyncio.sleep(self.interval)

    async def _capture_snapshot(self) -> MemorySnapshot:
        """Capture current memory state"""
        # Process memory
        mem_info = self.process.memory_info()
        
        # System memory
        system_mem = psutil.virtual_memory()

        snapshot = MemorySnapshot(
            timestamp=datetime.now(),
            process_rss=mem_info.rss,
            process_vms=mem_info.vms,
            system_available=system_mem.available,
            system_percent=system_mem.percent
        )

        # GPU memory if available
        if torch.cuda.is_available():
            try:
                snapshot.gpu_allocated = torch.cuda.memory_allocated()
                snapshot.gpu_reserved = torch.cuda.memory_reserved()
                props = torch.cuda.get_device_properties(0)
                snapshot.gpu_free = props.total_memory - snapshot.gpu_reserved
            except Exception as e:
                logger.debug(f"GPU memory check failed: {e}")

        return snapshot

    def _is_memory_critical(self, snapshot: MemorySnapshot) -> bool:
        """Check if memory usage is critical"""
        # System memory > 90%
        if snapshot.system_percent > 90:
            return True

        # Process using > 2GB
        if snapshot.process_rss > 2 * 1024 * 1024 * 1024:
            return True

        # GPU memory < 1GB free
        if snapshot.gpu_free and snapshot.gpu_free < 1024 * 1024 * 1024:
            return True

        return False

    async def _handle_memory_pressure(self, snapshot: MemorySnapshot) -> None:
        """Handle critical memory situation"""
        logger.warning(f"Memory pressure detected: RSS={snapshot.process_rss / 1024**2:.1f}MB, System={snapshot.system_percent:.1f}%")
        
        # Force garbage collection
        gc.collect()
        
        # Clear GPU cache if available
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def get_memory_trend(self) -> Dict[str, Any]:
        """Analyze memory usage trend"""
        if len(self.history) < 2:
            return {"trend": "unknown", "rate": 0}

        # Calculate trend over last 10 snapshots
        recent = self.history[-10:] if len(self.history) >= 10 else self.history
        
        rss_values = [s.process_rss for s in recent]
        time_diffs = [(recent[i].timestamp - recent[i-1].timestamp).total_seconds() 
                      for i in range(1, len(recent))]
        
        if not time_diffs:
            return {"trend": "stable", "rate": 0}

        # Calculate rate of change (bytes per second)
        total_change = rss_values[-1] - rss_values[0]
        total_time = sum(time_diffs)
        rate = total_change / total_time if total_time > 0 else 0

        trend = "increasing" if rate > 1024 * 1024 else "stable" if abs(rate) < 1024 * 1024 else "decreasing"

        return {
            "trend": trend,
            "rate": rate,
            "rate_mb_per_min": rate * 60 / (1024 * 1024)
        }

    def predict_oom_time(self) -> Optional[float]:
        """Predict time until out of memory"""
        trend = self.get_memory_trend()
        
        if trend["trend"] != "increasing":
            return None

        if not self.history:
            return None

        current = self.history[-1]
        rate = trend["rate"]  # bytes per second

        # Calculate remaining memory
        system_mem = psutil.virtual_memory()
        remaining = system_mem.available

        if rate <= 0:
            return None

        # Time until OOM in seconds
        time_to_oom = remaining / rate
        
        # Only warn if less than 5 minutes
        if time_to_oom < 300:
            return time_to_oom
        
        return None


class MemoryOptimizer:
    """
    Comprehensive memory optimization strategies
    """

    def __init__(self, target_memory_mb: int = 2048, gpu_memory_mb: int = 4096):
        """
        Initialize memory optimizer

        Args:
            target_memory_mb: Target process memory in MB
            gpu_memory_mb: Target GPU memory in MB
        """
        self.target_memory_bytes = target_memory_mb * 1024 * 1024
        self.gpu_memory_bytes = gpu_memory_mb * 1024 * 1024
        self.monitor = MemoryMonitor()
        self.optimization_history: List[Dict[str, Any]] = []

    async def start(self) -> None:
        """Start optimizer"""
        await self.monitor.start()
        logger.info(f"Memory optimizer started (target: {self.target_memory_bytes / 1024**2:.0f}MB)")

    async def stop(self) -> None:
        """Stop optimizer"""
        await self.monitor.stop()

    async def optimize_for_render(self, estimated_frames: int) -> Dict[str, Any]:
        """
        Optimize memory for rendering task

        Args:
            estimated_frames: Estimated number of frames

        Returns:
            Optimization recommendations
        """
        # Estimate memory requirements
        frame_size = 1920 * 1080 * 4  # RGBA at 1080p
        estimated_memory = estimated_frames * frame_size

        # Get current state
        snapshot = await self.monitor._capture_snapshot()
        
        recommendations = {
            "can_proceed": True,
            "optimizations": [],
            "estimated_memory_mb": estimated_memory / (1024 * 1024),
            "available_memory_mb": snapshot.system_available / (1024 * 1024)
        }

        # Check if optimization needed
        if estimated_memory > snapshot.system_available * 0.5:
            recommendations["optimizations"].append("reduce_frame_buffer")
            recommendations["optimizations"].append("enable_streaming")

        if snapshot.gpu_free and estimated_memory > snapshot.gpu_free * 0.7:
            recommendations["optimizations"].append("clear_gpu_cache")
            recommendations["optimizations"].append("reduce_batch_size")

        # Apply immediate optimizations
        if "clear_gpu_cache" in recommendations["optimizations"]:
            await self._clear_gpu_cache()

        if "reduce_frame_buffer" in recommendations["optimizations"]:
            recommendations["frame_buffer_size"] = 30  # Reduce from 60

        return recommendations

    async def _clear_gpu_cache(self) -> None:
        """Clear GPU memory cache"""
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            logger.info("GPU cache cleared")

    def optimize_frame_queue(self, current_size: int, drop_rate: float) -> int:
        """
        Dynamically adjust frame queue size

        Args:
            current_size: Current queue size
            drop_rate: Current frame drop rate

        Returns:
            Optimized queue size
        """
        # If high drop rate, reduce queue size
        if drop_rate > 0.05:  # >5% drops
            return max(15, current_size - 10)
        
        # If low drop rate and memory available, increase
        if drop_rate < 0.01:  # <1% drops
            snapshot = self.monitor.history[-1] if self.monitor.history else None
            if snapshot and snapshot.system_percent < 70:
                return min(90, current_size + 10)
        
        return current_size

    async def garbage_collect(self, level: int = 0) -> Dict[str, Any]:
        """
        Perform garbage collection

        Args:
            level: GC level (0=quick, 1=normal, 2=aggressive)

        Returns:
            Collection statistics
        """
        before = psutil.Process().memory_info().rss
        
        # Python GC
        if level >= 0:
            collected = gc.collect(0)
        if level >= 1:
            collected = gc.collect(1)
        if level >= 2:
            collected = gc.collect(2)
            gc.collect()  # Extra pass
        
        # GPU cleanup
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        
        after = psutil.Process().memory_info().rss
        freed = before - after
        
        result = {
            "objects_collected": collected,
            "memory_freed_mb": freed / (1024 * 1024),
            "level": level,
            "timestamp": datetime.now().isoformat()
        }
        
        self.optimization_history.append(result)
        logger.info(f"GC freed {result['memory_freed_mb']:.1f}MB")
        
        return result

    def get_optimization_stats(self) -> Dict[str, Any]:
        """Get optimization statistics"""
        if not self.monitor.history:
            return {"status": "no_data"}

        current = self.monitor.history[-1]
        trend = self.monitor.get_memory_trend()
        oom_time = self.monitor.predict_oom_time()

        stats = {
            "current_memory_mb": current.process_rss / (1024 * 1024),
            "system_memory_percent": current.system_percent,
            "memory_trend": trend["trend"],
            "memory_rate_mb_per_min": trend.get("rate_mb_per_min", 0),
            "optimization_count": len(self.optimization_history),
            "last_gc": self.optimization_history[-1] if self.optimization_history else None
        }

        if current.gpu_allocated:
            stats["gpu_allocated_mb"] = current.gpu_allocated / (1024 * 1024)
            stats["gpu_free_mb"] = current.gpu_free / (1024 * 1024) if current.gpu_free else 0

        if oom_time:
            stats["oom_warning"] = f"Out of memory predicted in {oom_time / 60:.1f} minutes"

        return stats


class FrameBufferOptimizer:
    """
    Optimizes frame buffering strategies based on memory pressure
    """

    def __init__(self, initial_buffer_size: int = 60):
        """
        Initialize frame buffer optimizer

        Args:
            initial_buffer_size: Initial buffer size in frames
        """
        self.buffer_size = initial_buffer_size
        self.min_buffer = 15  # Minimum 0.5 seconds at 30fps
        self.max_buffer = 120  # Maximum 4 seconds at 30fps
        self.adjustment_history: List[Tuple[datetime, int, str]] = []

    def adjust_buffer_size(
        self,
        memory_pressure: float,
        drop_rate: float,
        processing_speed: float
    ) -> int:
        """
        Dynamically adjust buffer size

        Args:
            memory_pressure: Current memory pressure (0-1)
            drop_rate: Frame drop rate (0-1)
            processing_speed: Frames per second being processed

        Returns:
            New buffer size
        """
        old_size = self.buffer_size
        reason = ""

        # High memory pressure - reduce buffer
        if memory_pressure > 0.8:
            self.buffer_size = max(self.min_buffer, int(self.buffer_size * 0.7))
            reason = "high_memory_pressure"
        
        # High drop rate - increase buffer if memory allows
        elif drop_rate > 0.05 and memory_pressure < 0.6:
            self.buffer_size = min(self.max_buffer, int(self.buffer_size * 1.3))
            reason = "high_drop_rate"
        
        # Low drop rate and low memory pressure - optimize for latency
        elif drop_rate < 0.01 and memory_pressure < 0.5:
            # Optimal buffer is ~1 second of frames
            optimal = int(processing_speed)
            self.buffer_size = max(self.min_buffer, min(self.max_buffer, optimal))
            reason = "optimize_latency"
        
        # Log adjustment
        if self.buffer_size != old_size:
            self.adjustment_history.append(
                (datetime.now(), self.buffer_size, reason)
            )
            logger.info(f"Buffer adjusted: {old_size} -> {self.buffer_size} ({reason})")

        return self.buffer_size

    def get_buffer_stats(self) -> Dict[str, Any]:
        """Get buffer optimization statistics"""
        return {
            "current_size": self.buffer_size,
            "min_size": self.min_buffer,
            "max_size": self.max_buffer,
            "adjustments": len(self.adjustment_history),
            "last_adjustment": self.adjustment_history[-1] if self.adjustment_history else None
        }


class GPUMemoryManager:
    """
    Manages GPU memory allocation and optimization
    """

    def __init__(self):
        """
        Initialize GPU memory manager
        """
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.is_gpu_available = torch.cuda.is_available()
        self.reserved_memory: Dict[str, int] = {}

    def check_availability(self, required_mb: int) -> bool:
        """
        Check if required GPU memory is available

        Args:
            required_mb: Required memory in MB

        Returns:
            True if available
        """
        if not self.is_gpu_available:
            return False

        try:
            props = torch.cuda.get_device_properties(0)
            free = props.total_memory - torch.cuda.memory_reserved()
            return free >= required_mb * 1024 * 1024
        except:
            return False

    async def reserve_memory(self, task_id: str, size_mb: int) -> bool:
        """
        Reserve GPU memory for task

        Args:
            task_id: Task identifier
            size_mb: Memory to reserve in MB

        Returns:
            Success status
        """
        if not self.check_availability(size_mb):
            return False

        self.reserved_memory[task_id] = size_mb
        logger.info(f"Reserved {size_mb}MB GPU memory for {task_id}")
        return True

    async def release_memory(self, task_id: str) -> None:
        """
        Release reserved GPU memory

        Args:
            task_id: Task identifier
        """
        if task_id in self.reserved_memory:
            size = self.reserved_memory.pop(task_id)
            torch.cuda.empty_cache()
            logger.info(f"Released {size}MB GPU memory from {task_id}")

    def get_gpu_stats(self) -> Dict[str, Any]:
        """Get GPU memory statistics"""
        if not self.is_gpu_available:
            return {"available": False}

        try:
            props = torch.cuda.get_device_properties(0)
            allocated = torch.cuda.memory_allocated()
            reserved = torch.cuda.memory_reserved()
            
            return {
                "available": True,
                "device_name": props.name,
                "total_memory_gb": props.total_memory / (1024**3),
                "allocated_mb": allocated / (1024**2),
                "reserved_mb": reserved / (1024**2),
                "free_mb": (props.total_memory - reserved) / (1024**2),
                "tasks_reserved": len(self.reserved_memory),
                "total_reserved_mb": sum(self.reserved_memory.values())
            }
        except Exception as e:
            logger.error(f"GPU stats error: {e}")
            return {"available": False, "error": str(e)}


# Global instances
_memory_optimizer: Optional[MemoryOptimizer] = None
_gpu_manager: Optional[GPUMemoryManager] = None


def get_memory_optimizer() -> MemoryOptimizer:
    """Get global memory optimizer instance"""
    global _memory_optimizer
    if _memory_optimizer is None:
        _memory_optimizer = MemoryOptimizer()
    return _memory_optimizer


def get_gpu_manager() -> GPUMemoryManager:
    """Get global GPU memory manager instance"""
    global _gpu_manager
    if _gpu_manager is None:
        _gpu_manager = GPUMemoryManager()
    return _gpu_manager