"""
Segment Optimizer for intelligent video segmentation
Analyzes subtitle density and complexity to optimize segment distribution
"""

import logging
from typing import Dict, List, Any, Tuple, Optional
from dataclasses import dataclass
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class VideoSegment:
    """Represents a video segment for parallel processing"""
    segment_id: int
    worker_id: int
    start_time: float
    end_time: float
    cues: List[Dict[str, Any]]
    complexity_score: float
    estimated_frames: int


class SegmentOptimizer:
    """Optimizes video segmentation based on subtitle complexity"""

    def __init__(self, target_segment_duration: float = 30.0):
        """
        Initialize segment optimizer

        Args:
            target_segment_duration: Target duration for each segment in seconds
        """
        self.target_segment_duration = target_segment_duration
        self.min_segment_duration = 5.0  # Minimum 5 seconds
        self.max_segment_duration = 60.0  # Maximum 60 seconds

    def analyze_scenario_complexity(self, scenario: Dict[str, Any]) -> Dict[float, float]:
        """
        Analyze the complexity of the scenario over time

        Args:
            scenario: MotionText scenario with cues

        Returns:
            Dictionary mapping time (seconds) to complexity score
        """
        complexity_map = {}
        cues = scenario.get('cues', [])

        if not cues:
            return complexity_map

        # Determine video duration
        max_time = max(cue.get('end', 0) for cue in cues)

        # Calculate complexity for each second
        for second in range(int(max_time) + 1):
            complexity = 0.0

            # Find all active cues at this time
            active_cues = [
                cue for cue in cues
                if cue.get('start', 0) <= second <= cue.get('end', 0)
            ]

            # Calculate complexity based on multiple factors
            for cue in active_cues:
                # Base complexity for each cue
                complexity += 1.0

                # Text length factor
                text = cue.get('text', '')
                complexity += len(text) * 0.01

                # Style complexity
                if cue.get('style'):
                    style = cue['style']
                    # Complex fonts add complexity
                    if style.get('fontFamily') and 'CJK' in style.get('fontFamily', ''):
                        complexity += 0.5

                # Animation complexity (if animation data exists)
                if cue.get('animation'):
                    animation = cue['animation']
                    if animation.get('type') in ['elastic', 'bounce']:
                        complexity += 1.5
                    elif animation.get('type') in ['fade', 'slide']:
                        complexity += 0.5

                # Speaker/emotion complexity
                if cue.get('emotion') and cue['emotion'] != 'neutral':
                    complexity += 0.3

            # Overlapping cues increase complexity exponentially
            if len(active_cues) > 1:
                complexity *= (1 + 0.5 * (len(active_cues) - 1))

            complexity_map[float(second)] = complexity

        return complexity_map

    def calculate_cue_complexity(self, cue: Dict[str, Any]) -> float:
        """
        Calculate complexity score for a single cue

        Args:
            cue: Subtitle cue data

        Returns:
            Complexity score
        """
        complexity = 1.0  # Base complexity

        # Text length
        text = cue.get('text', '')
        complexity += len(text) * 0.01

        # CJK characters are more complex to render
        if any('\u4e00' <= char <= '\u9fff' for char in text):
            complexity += 0.5

        # Style complexity
        if cue.get('style'):
            style = cue['style']
            if style.get('fontSize'):
                # Larger fonts need more processing
                try:
                    size = int(style['fontSize'].replace('px', ''))
                    if size > 30:
                        complexity += 0.3
                except:
                    pass

        # Duration factor (longer cues are more complex)
        duration = cue.get('end', 0) - cue.get('start', 0)
        if duration > 5:
            complexity += 0.2

        return complexity

    def smart_segment_split(
        self,
        scenario: Dict[str, Any],
        video_duration: float,
        num_workers: int = 4
    ) -> List[VideoSegment]:
        """
        Split video into optimal segments for parallel processing

        Args:
            scenario: MotionText scenario
            video_duration: Total video duration in seconds
            num_workers: Number of available workers

        Returns:
            List of VideoSegment objects
        """
        segments = []
        cues = scenario.get('cues', [])

        if not cues:
            # No subtitles, split evenly
            return self._even_split(video_duration, num_workers)

        # Analyze complexity
        complexity_map = self.analyze_scenario_complexity(scenario)

        # Find optimal split points
        split_points = self._find_optimal_split_points(
            complexity_map,
            video_duration,
            num_workers
        )

        # Create segments
        for i in range(len(split_points) - 1):
            start_time = split_points[i]
            end_time = split_points[i + 1]

            # Find cues for this segment
            segment_cues = self._get_segment_cues(cues, start_time, end_time)

            # Calculate complexity score
            complexity_score = self._calculate_segment_complexity(
                complexity_map,
                start_time,
                end_time
            )

            # Estimate frames (30 fps)
            estimated_frames = int((end_time - start_time) * 30)

            segment = VideoSegment(
                segment_id=i,
                worker_id=i % num_workers,
                start_time=start_time,
                end_time=end_time,
                cues=segment_cues,
                complexity_score=complexity_score,
                estimated_frames=estimated_frames
            )

            segments.append(segment)

        logger.info(f"Created {len(segments)} segments for {num_workers} workers")
        return segments

    def _even_split(self, duration: float, num_workers: int) -> List[VideoSegment]:
        """
        Split video evenly when no complexity data available

        Args:
            duration: Video duration
            num_workers: Number of workers

        Returns:
            List of evenly split segments
        """
        segments = []
        segment_duration = duration / num_workers

        for i in range(num_workers):
            start_time = i * segment_duration
            end_time = min((i + 1) * segment_duration, duration)

            segment = VideoSegment(
                segment_id=i,
                worker_id=i,
                start_time=start_time,
                end_time=end_time,
                cues=[],
                complexity_score=1.0,
                estimated_frames=int((end_time - start_time) * 30)
            )

            segments.append(segment)

        return segments

    def _find_optimal_split_points(
        self,
        complexity_map: Dict[float, float],
        duration: float,
        num_workers: int
    ) -> List[float]:
        """
        Find optimal points to split the video

        Args:
            complexity_map: Time to complexity mapping
            duration: Total video duration
            num_workers: Number of workers

        Returns:
            List of split points (including 0 and duration)
        """
        # Start with even distribution
        split_points = [0.0]

        if not complexity_map:
            # No complexity data, split evenly
            segment_duration = duration / num_workers
            for i in range(1, num_workers):
                split_points.append(i * segment_duration)
            split_points.append(duration)
            return split_points

        # Calculate total complexity
        total_complexity = sum(complexity_map.values())
        target_complexity_per_segment = total_complexity / num_workers

        # Find split points based on complexity distribution
        current_complexity = 0.0
        last_split = 0.0

        for second in sorted(complexity_map.keys()):
            current_complexity += complexity_map.get(second, 0)

            # Check if we should split here
            if current_complexity >= target_complexity_per_segment:
                # Find the best split point (prefer silence/low complexity)
                best_split = self._find_best_split_near(
                    complexity_map,
                    second,
                    last_split,
                    min(duration, second + 5)
                )

                if best_split > last_split + self.min_segment_duration:
                    split_points.append(best_split)
                    current_complexity = 0.0
                    last_split = best_split

                    # Stop if we have enough segments
                    if len(split_points) >= num_workers:
                        break

        # Ensure we have the right number of segments
        while len(split_points) < num_workers:
            # Add more split points if needed
            largest_gap_index = 0
            largest_gap = 0

            for i in range(len(split_points) - 1):
                gap = split_points[i + 1] - split_points[i]
                if gap > largest_gap:
                    largest_gap = gap
                    largest_gap_index = i

            # Split the largest segment
            if largest_gap > self.min_segment_duration * 2:
                mid_point = (split_points[largest_gap_index] + split_points[largest_gap_index + 1]) / 2
                split_points.insert(largest_gap_index + 1, mid_point)
            else:
                break

        # Add end point
        if split_points[-1] < duration:
            split_points.append(duration)

        return sorted(split_points)

    def _find_best_split_near(
        self,
        complexity_map: Dict[float, float],
        target: float,
        min_time: float,
        max_time: float
    ) -> float:
        """
        Find the best split point near target time

        Args:
            complexity_map: Complexity data
            target: Target split time
            min_time: Minimum allowed time
            max_time: Maximum allowed time

        Returns:
            Best split point
        """
        best_time = target
        min_complexity = float('inf')

        # Search for low complexity point near target
        search_range = range(int(max(min_time, target - 2)), int(min(max_time, target + 3)))

        for second in search_range:
            complexity = complexity_map.get(float(second), 0)

            # Prefer splitting at low complexity points
            if complexity < min_complexity:
                min_complexity = complexity
                best_time = float(second)

            # Perfect if we find a silence (no cues)
            if complexity == 0:
                return float(second)

        return best_time

    def _get_segment_cues(
        self,
        cues: List[Dict[str, Any]],
        start_time: float,
        end_time: float
    ) -> List[Dict[str, Any]]:
        """
        Get cues that overlap with segment time range

        Args:
            cues: All cues
            start_time: Segment start
            end_time: Segment end

        Returns:
            List of cues for this segment
        """
        segment_cues = []

        for cue in cues:
            cue_start = cue.get('start', 0)
            cue_end = cue.get('end', 0)

            # Check if cue overlaps with segment
            if cue_end > start_time and cue_start < end_time:
                segment_cues.append(cue)

        return segment_cues

    def _calculate_segment_complexity(
        self,
        complexity_map: Dict[float, float],
        start_time: float,
        end_time: float
    ) -> float:
        """
        Calculate total complexity for a segment

        Args:
            complexity_map: Complexity data
            start_time: Segment start
            end_time: Segment end

        Returns:
            Total complexity score
        """
        total_complexity = 0.0

        for second in range(int(start_time), int(end_time) + 1):
            total_complexity += complexity_map.get(float(second), 0)

        return total_complexity

    def adaptive_worker_allocation(
        self,
        segments: List[VideoSegment],
        num_workers: int
    ) -> Dict[int, str]:
        """
        Allocate workers based on segment complexity

        Args:
            segments: List of segments
            num_workers: Available workers

        Returns:
            Worker allocation strategy
        """
        allocation = {}

        # Sort segments by complexity
        sorted_segments = sorted(segments, key=lambda s: s.complexity_score, reverse=True)

        # Assign high-performance mode to complex segments
        high_perf_count = max(1, num_workers // 3)
        standard_count = max(1, num_workers // 3)

        for i, segment in enumerate(sorted_segments):
            if i < high_perf_count:
                allocation[segment.worker_id] = 'high_performance'
            elif i < high_perf_count + standard_count:
                allocation[segment.worker_id] = 'standard'
            else:
                allocation[segment.worker_id] = 'fast'

        return allocation

    def get_segment_info(self, segments: List[VideoSegment]) -> Dict[str, Any]:
        """
        Get summary information about segments

        Args:
            segments: List of segments

        Returns:
            Summary statistics
        """
        if not segments:
            return {}

        total_complexity = sum(s.complexity_score for s in segments)
        total_frames = sum(s.estimated_frames for s in segments)
        durations = [s.end_time - s.start_time for s in segments]

        return {
            "segment_count": len(segments),
            "total_complexity": total_complexity,
            "avg_complexity": total_complexity / len(segments),
            "total_frames": total_frames,
            "avg_duration": sum(durations) / len(durations),
            "min_duration": min(durations),
            "max_duration": max(durations),
            "segments": [
                {
                    "id": s.segment_id,
                    "worker": s.worker_id,
                    "start": s.start_time,
                    "end": s.end_time,
                    "duration": s.end_time - s.start_time,
                    "cues": len(s.cues),
                    "complexity": s.complexity_score,
                    "frames": s.estimated_frames
                }
                for s in segments
            ]
        }