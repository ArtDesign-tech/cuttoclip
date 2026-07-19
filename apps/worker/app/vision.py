"""Local smart-crop analysis for talking-head footage.

Face location comes from YuNet (ONNX Runtime, CPU) and speech activity from
Silero VAD (ONNX Runtime, CPU). The crop follows the most stable face only while
someone is speaking and holds its last position during silence, so the framing
does not drift on reaction shots or pauses.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from statistics import median, pstdev
from typing import Callable, Literal

from .errors import WorkerError

try:
    import cv2
except ImportError:  # The worker remains usable for non-smart layouts.
    cv2 = None

try:
    import numpy as np
except ImportError:
    np = None


YUNET_MAX_SIDE = 640
YUNET_PAD_MULTIPLE = 32
YUNET_SCORE_THRESHOLD = 0.6
YUNET_NMS_THRESHOLD = 0.3
SAMPLE_FPS = 5.0
SMOOTH_PREVIOUS = 0.72
SMOOTH_TARGET = 0.28

VAD_SAMPLE_RATE = 16000
VAD_WINDOW = 512
VAD_CONTEXT = 64
VAD_THRESHOLD = 0.5
VAD_SPEECH_PAD_SECONDS = 0.2
VAD_MERGE_GAP_SECONDS = 0.3
VAD_WINDOW_SECONDS = VAD_WINDOW / VAD_SAMPLE_RATE

# Dual-facecam tracking. Facecam slots are static overlays, so detections are
# associated in both axes and by size. This keeps a large facecam separate from
# a row of small participant thumbnails even when their x positions overlap.
DUAL_MATCH_X_RATIO = 0.08
DUAL_MATCH_Y_RATIO = 0.08
DUAL_MATCH_MIN_SIZE_RATIO = 0.5
DUAL_MATCH_MAX_SIZE_RATIO = 2.0
DUAL_MIN_OBSERVATIONS = 8
DUAL_MIN_PRESENCE = 0.05
DUAL_MIN_SCORE = 0.65
DUAL_MIN_AREA_RATIO = 0.0005
DUAL_MAX_CENTER_STD_RATIO = 0.025
DUAL_MAX_ROI_IOU = 0.25

# Gaming facecams are overlays anchored near a screen edge. YuNet also detects
# faces rendered inside a game, and a dialogue character can remain stable long
# enough to pass the generic face-track thresholds. Keep the central gameplay
# region out of the facecam candidate pool while still allowing top-, bottom-,
# left-, and right-aligned streamer layouts.
GAMING_EDGE_ZONE_RATIO = 0.32
GAMING_MAX_CENTER_STD_RATIO = 0.04
GAMING_MIN_PRESENCE = 0.5

# Facecam card geometry. Each 16:9 card is estimated from the face size and the
# output places two of them beneath the gameplay main area (1616 + 304 = 1920).
FACECAM_ASPECT = 16 / 9
FACECAM_FACE_HEIGHT_RATIO = 0.42
FACECAM_FACE_VERTICAL_BIAS = 0.42
DUAL_MAIN_WIDTH = 1080
DUAL_MAIN_HEIGHT = 1616
DUAL_CARD_WIDTH = 540
DUAL_CARD_HEIGHT = 304
GAMING_SINGLE_HEADER_HEIGHT = 540
GAMING_SINGLE_MAIN_HEIGHT = 1380
GAMING_SINGLE_DIVIDER_HEIGHT = 4

_CREATION_FLAGS = 0x08000000 if os.name == "nt" else 0


@dataclass
class SmartCropResult:
    """Mode-tagged smart-crop analysis.

    `single` carries a crop trajectory (empty means static centre crop); `dual`
    carries two stable facecam ROIs ordered left-to-right in source pixels.
    """

    mode: Literal["single", "dual"]
    track: list[dict[str, float]]
    facecams: list[dict[str, int]]


def _normalized_rect(x: float, y: float, width: float, height: float, frame_width: int, frame_height: int) -> dict[str, float]:
    """Return a browser-friendly crop rectangle clamped to the source frame."""
    x = min(max(0.0, x), max(0.0, frame_width - 1))
    y = min(max(0.0, y), max(0.0, frame_height - 1))
    width = min(max(1.0, width), frame_width - x)
    height = min(max(1.0, height), frame_height - y)
    return {
        "x": x / frame_width,
        "y": y / frame_height,
        "width": width / frame_width,
        "height": height / frame_height,
    }


def _destination_rect(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {"x": x / 1080, "y": y / 1920, "width": width / 1080, "height": height / 1920}


def layout_preview_plan(
    layout: Literal["smart_portrait", "gaming_portrait"],
    frame_width: int,
    frame_height: int,
    result: SmartCropResult | list[dict[str, int]],
    cache_key: str,
) -> dict[str, object]:
    """Convert the exact render analysis into normalized canvas layers.

    Both the web preview and the FFmpeg graph consume the same tracking result
    and geometry constants. This keeps the editor's 9:16 monitor honest without
    generating a second low-resolution proxy video.
    """
    full = _destination_rect(0, 0, 1080, 1920)
    if layout == "smart_portrait":
        smart = result
        if not isinstance(smart, SmartCropResult):
            raise WorkerError("SMART_CROP_FAILED", "Smart portrait preview has no crop result.", retryable=True)
        if smart.mode == "single":
            crop_width = min(float(frame_width), frame_height * 9 / 16)
            scale = 1920 / frame_height
            track = smart.track or [{"time": 0.0, "x": max(0.0, (frame_width - crop_width) / 2) * scale}]
            keyframes = [
                {
                    "atSeconds": max(0.0, float(point["time"])),
                    "layers": [{
                        "source": _normalized_rect(float(point["x"]) / scale, 0, crop_width, frame_height, frame_width, frame_height),
                        "destination": full,
                    }],
                }
                for point in track
            ]
            mode = "single"
        else:
            main_width = min(float(frame_width), frame_height * DUAL_MAIN_WIDTH / DUAL_MAIN_HEIGHT)
            main = _normalized_rect((frame_width - main_width) / 2, 0, main_width, frame_height, frame_width, frame_height)
            layers = [{"source": main, "destination": _destination_rect(0, 0, DUAL_MAIN_WIDTH, DUAL_MAIN_HEIGHT)}]
            for index, camera in enumerate(smart.facecams):
                layers.append({
                    "source": _normalized_rect(camera["x"], camera["y"], camera["w"], camera["h"], frame_width, frame_height),
                    "destination": _destination_rect(index * DUAL_CARD_WIDTH, DUAL_MAIN_HEIGHT, DUAL_CARD_WIDTH, DUAL_CARD_HEIGHT),
                })
            keyframes = [{"atSeconds": 0.0, "layers": layers}]
            mode = "dual"
    else:
        facecams = result
        if isinstance(facecams, SmartCropResult) or len(facecams) not in {1, 2}:
            raise WorkerError("GAMING_FACECAM_FAILED", "Gaming preview requires one or two facecam regions.", retryable=True)
        if len(facecams) == 1:
            camera = facecams[0]
            crop_width = camera["w"] - (camera["w"] % 4)
            crop_height = crop_width // 2
            crop_x = camera["x"] + (camera["w"] - crop_width) // 2
            crop_y = camera["y"] + (camera["h"] - crop_height) // 2
            crop_x -= crop_x % 2
            crop_y -= crop_y % 2
            main_width = min(float(frame_width), frame_height * DUAL_MAIN_WIDTH / GAMING_SINGLE_MAIN_HEIGHT)
            layers = [
                {
                    "source": _normalized_rect(crop_x, crop_y, crop_width, crop_height, frame_width, frame_height),
                    "destination": _destination_rect(0, 0, DUAL_MAIN_WIDTH, GAMING_SINGLE_HEADER_HEIGHT),
                },
                {
                    "source": _normalized_rect((frame_width - main_width) / 2, 0, main_width, frame_height, frame_width, frame_height),
                    "destination": _destination_rect(0, GAMING_SINGLE_HEADER_HEIGHT, DUAL_MAIN_WIDTH, GAMING_SINGLE_MAIN_HEIGHT),
                },
            ]
            mode = "gaming_single"
        else:
            main_width = min(float(frame_width), frame_height * DUAL_MAIN_WIDTH / DUAL_MAIN_HEIGHT)
            layers = []
            for index, camera in enumerate(facecams):
                layers.append({
                    "source": _normalized_rect(camera["x"], camera["y"], camera["w"], camera["h"], frame_width, frame_height),
                    "destination": _destination_rect(index * DUAL_CARD_WIDTH, 0, DUAL_CARD_WIDTH, DUAL_CARD_HEIGHT),
                })
            layers.append({
                "source": _normalized_rect((frame_width - main_width) / 2, 0, main_width, frame_height, frame_width, frame_height),
                "destination": _destination_rect(0, DUAL_CARD_HEIGHT, DUAL_MAIN_WIDTH, DUAL_MAIN_HEIGHT),
            })
            mode = "gaming_dual"
        keyframes = [{"atSeconds": 0.0, "layers": layers}]

    return {
        "layout": layout,
        "mode": mode,
        "canvasWidth": 1080,
        "canvasHeight": 1920,
        "sourceWidth": frame_width,
        "sourceHeight": frame_height,
        "keyframes": keyframes,
        "cacheKey": cache_key,
    }


# --- YuNet decoding -------------------------------------------------------


def _iou(first: list[float], second: list[float]) -> float:
    ax1, ay1, aw, ah = first[0], first[1], first[2], first[3]
    bx1, by1, bw, bh = second[0], second[1], second[2], second[3]
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax1 + aw, bx1 + bw), min(ay1 + ah, by1 + bh)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def nms(boxes: list[list[float]], iou_threshold: float = YUNET_NMS_THRESHOLD) -> list[list[float]]:
    kept: list[list[float]] = []
    for box in sorted(boxes, key=lambda item: item[4], reverse=True):
        if all(_iou(box, existing) <= iou_threshold for existing in kept):
            kept.append(box)
    return kept


def largest_face(boxes: list[list[float]]) -> list[float] | None:
    # Talking-head/podcast content: the largest stable face is the safest
    # active-speaker approximation once VAD confirms someone is talking.
    return max(boxes, key=lambda item: item[2] * item[3], default=None)


def decode_yunet(outputs: list, input_width: int, input_height: int, score_threshold: float = YUNET_SCORE_THRESHOLD) -> list[list[float]]:
    """Decode raw YuNet tensors into [x, y, w, h, score] boxes in input coordinates.

    Outputs are grouped by their spatial length N; the per-stride grid is derived
    from N = (W/stride) * (H/stride). The two single-column tensors are the class
    and objectness scores whose geometric mean is the face score, so their order
    does not matter. The width-4 tensor holds bbox regression; keypoints (width 10)
    are ignored.
    """
    if np is None:
        raise WorkerError("VISION_RUNTIME_UNAVAILABLE", "numpy is required for smart crop.", status_code=503, retryable=True)
    groups: dict[int, dict[str, object]] = {}
    for raw in outputs:
        array = np.asarray(raw, dtype=np.float32)
        if array.ndim == 3:
            array = array.reshape(array.shape[1], array.shape[2])
        elif array.ndim == 1:
            array = array.reshape(-1, 1)
        count, dim = array.shape
        group = groups.setdefault(count, {"scores": [], "bbox": None})
        if dim == 1:
            group["scores"].append(array)  # type: ignore[union-attr]
        elif dim == 4:
            group["bbox"] = array

    faces: list[list[float]] = []
    for count, group in groups.items():
        bbox = group["bbox"]
        score_arrays = group["scores"]
        if bbox is None or not score_arrays:
            continue
        stride = int(round((input_width * input_height / count) ** 0.5))
        if stride <= 0 or input_width % stride:
            continue
        cols = input_width // stride
        product = np.ones((count,), dtype=np.float32)
        for score_array in score_arrays:  # type: ignore[union-attr]
            product *= np.clip(score_array[:, 0], 0.0, 1.0)
        scores = np.sqrt(product)
        for index in range(count):
            score = float(scores[index])
            if score < score_threshold:
                continue
            row, col = divmod(index, cols)
            cx = (col + float(bbox[index, 0])) * stride
            cy = (row + float(bbox[index, 1])) * stride
            width = float(np.exp(bbox[index, 2])) * stride
            height = float(np.exp(bbox[index, 3])) * stride
            faces.append([cx - width / 2, cy - height / 2, width, height, score])
    return faces


def _preprocess_frame(frame):
    height, width = frame.shape[:2]
    scale = min(1.0, YUNET_MAX_SIDE / max(width, height))
    resized_w, resized_h = max(1, round(width * scale)), max(1, round(height * scale))
    resized = cv2.resize(frame, (resized_w, resized_h))
    padded_w = ((resized_w + YUNET_PAD_MULTIPLE - 1) // YUNET_PAD_MULTIPLE) * YUNET_PAD_MULTIPLE
    padded_h = ((resized_h + YUNET_PAD_MULTIPLE - 1) // YUNET_PAD_MULTIPLE) * YUNET_PAD_MULTIPLE
    padded = np.zeros((padded_h, padded_w, 3), dtype=np.uint8)
    padded[:resized_h, :resized_w] = resized
    blob = padded.astype(np.float32).transpose(2, 0, 1)[None]
    return blob, scale, padded_w, padded_h


def _faces_in_original(frame, session, input_name: str) -> list[list[float]]:
    """Detect every face in a frame, returned as [x, y, w, h, score] in source px."""
    blob, scale, padded_w, padded_h = _preprocess_frame(frame)
    outputs = session.run(None, {input_name: blob})
    faces = nms(decode_yunet(outputs, padded_w, padded_h))
    return [[x / scale, y / scale, w / scale, h / scale, score] for x, y, w, h, score in faces]


def _target_from_face(face: list[float], orig_width: int, orig_height: int, crop_width: float) -> tuple[float, float]:
    x, _, width, height, _ = face
    center_x = x + width / 2
    confidence = min(1.0, (width * height) / max(1.0, orig_width * orig_height * 0.08))
    return center_x - crop_width / 2, confidence


# --- Face tracking / dual-facecam decision --------------------------------


def _face_center(face: list[float]) -> tuple[float, float]:
    return face[0] + face[2] / 2, face[1] + face[3] / 2


def _track_reference(track: list[tuple[int, list[float]]]) -> list[float]:
    faces = [face for _, face in track]
    return [
        median(face[0] for face in faces),
        median(face[1] for face in faces),
        median(face[2] for face in faces),
        median(face[3] for face in faces),
        median(face[4] for face in faces),
    ]


def _size_compatible(face: list[float], reference: list[float]) -> bool:
    width_ratio = face[2] / max(reference[2], 1.0)
    height_ratio = face[3] / max(reference[3], 1.0)
    return (
        DUAL_MATCH_MIN_SIZE_RATIO <= width_ratio <= DUAL_MATCH_MAX_SIZE_RATIO
        and DUAL_MATCH_MIN_SIZE_RATIO <= height_ratio <= DUAL_MATCH_MAX_SIZE_RATIO
    )


def _track_faces(
    faces_per_frame: list[list[list[float]]],
    frame_width: int,
    frame_height: int,
) -> list[list[tuple[int, list[float]]]]:
    """Associate static overlay faces across frames using position and box size.

    Matching is one-to-one inside each frame. Tracks deliberately do not expire:
    small thumbnails can be missed for long stretches by the full-frame detector
    and still belong to the same fixed facecam slot when they reappear.
    """
    tracks: list[list[tuple[int, list[float]]]] = []
    max_x = frame_width * DUAL_MATCH_X_RATIO
    max_y = frame_height * DUAL_MATCH_Y_RATIO

    for frame_index, faces in enumerate(faces_per_frame):
        references = [_track_reference(track) for track in tracks]
        possible: list[tuple[float, int, int]] = []
        for face_index, face in enumerate(faces):
            face_x, face_y = _face_center(face)
            for track_index, reference in enumerate(references):
                if not _size_compatible(face, reference):
                    continue
                ref_x, ref_y = _face_center(reference)
                dx = abs(face_x - ref_x)
                dy = abs(face_y - ref_y)
                if dx <= max_x and dy <= max_y:
                    distance = (dx / max(max_x, 1.0)) ** 2 + (dy / max(max_y, 1.0)) ** 2
                    possible.append((distance, track_index, face_index))

        used_tracks: set[int] = set()
        used_faces: set[int] = set()
        for _, track_index, face_index in sorted(possible):
            if track_index in used_tracks or face_index in used_faces:
                continue
            tracks[track_index].append((frame_index, faces[face_index]))
            used_tracks.add(track_index)
            used_faces.add(face_index)

        for face_index, face in enumerate(faces):
            if face_index not in used_faces:
                tracks.append([(frame_index, face)])

    return tracks


def _summarize_cluster(
    observations: list[tuple[int, list[float]]],
    total_samples: int,
    frame_width: int,
    frame_height: int,
) -> dict[str, float]:
    faces = [face for _, face in observations]
    frames = {frame_index for frame_index, _ in observations}
    centers = [_face_center(face) for face in faces]
    center_x = [center[0] for center in centers]
    center_y = [center[1] for center in centers]
    width = median(face[2] for face in faces)
    height = median(face[3] for face in faces)
    return {
        "center_x": median(center_x),
        "center_y": median(center_y),
        "width": width,
        "height": height,
        "score": sum(face[4] for face in faces) / len(faces),
        "observations": float(len(faces)),
        "presence": min(1.0, len(frames) / max(1, total_samples)),
        "area_ratio": (width * height) / max(1.0, frame_width * frame_height),
        "center_std_ratio": max(
            (pstdev(center_x) / frame_width) if len(center_x) > 1 else 0.0,
            (pstdev(center_y) / frame_height) if len(center_y) > 1 else 0.0,
        ),
    }


def _facecam_roi(summary: dict[str, float], frame_width: int, frame_height: int) -> dict[str, int]:
    crop_h = min(float(frame_height), summary["height"] / FACECAM_FACE_HEIGHT_RATIO)
    crop_w = crop_h * FACECAM_ASPECT
    if crop_w > frame_width:
        crop_w = float(frame_width)
        crop_h = crop_w / FACECAM_ASPECT
    x = summary["center_x"] - crop_w / 2
    y = summary["center_y"] - crop_h * FACECAM_FACE_VERTICAL_BIAS
    x = max(0.0, min(x, frame_width - crop_w))
    y = max(0.0, min(y, frame_height - crop_h))
    # yuv420p needs even dimensions; floor to keep the ROI inside the frame.
    return {
        "x": int(x) - (int(x) % 2),
        "y": int(y) - (int(y) % 2),
        "w": int(crop_w) - (int(crop_w) % 2),
        "h": int(crop_h) - (int(crop_h) % 2),
    }


def _roi_iou(first: dict[str, int], second: dict[str, int]) -> float:
    return _iou(
        [float(first["x"]), float(first["y"]), float(first["w"]), float(first["h"]), 1.0],
        [float(second["x"]), float(second["y"]), float(second["w"]), float(second["h"]), 1.0],
    )


def _select_facecam_rois(
    faces_per_frame: list[list[list[float]]],
    frame_width: int,
    frame_height: int,
    limit: int,
    candidate_filter: Callable[[dict[str, float]], bool] | None = None,
    max_center_std_ratio: float = DUAL_MAX_CENTER_STD_RATIO,
    min_presence: float = DUAL_MIN_PRESENCE,
) -> list[dict[str, int]]:
    total_samples = len(faces_per_frame)
    if total_samples == 0:
        return []
    tracks = _track_faces(faces_per_frame, frame_width, frame_height)
    summaries = [_summarize_cluster(track, total_samples, frame_width, frame_height) for track in tracks]
    eligible = [
        summary
        for summary in summaries
        if (
            summary["observations"] >= DUAL_MIN_OBSERVATIONS
            and summary["presence"] >= min_presence
            and summary["score"] >= DUAL_MIN_SCORE
            and summary["area_ratio"] >= DUAL_MIN_AREA_RATIO
            and summary["center_std_ratio"] <= max_center_std_ratio
            and (candidate_filter is None or candidate_filter(summary))
        )
    ]
    ranked = sorted(
        eligible,
        key=lambda summary: (summary["area_ratio"], summary["presence"], summary["score"]),
        reverse=True,
    )
    selected: list[tuple[dict[str, float], dict[str, int]]] = []
    for summary in ranked:
        roi = _facecam_roi(summary, frame_width, frame_height)
        if all(_roi_iou(roi, existing_roi) <= DUAL_MAX_ROI_IOU for _, existing_roi in selected):
            selected.append((summary, roi))
        if len(selected) == limit:
            break
    selected.sort(key=lambda item: (item[0]["center_x"], item[0]["center_y"]))
    return [roi for _, roi in selected]


def decide_facecam_layout(
    faces_per_frame: list[list[list[float]]],
    frame_width: int,
    frame_height: int,
    crop_width: float,
) -> list[dict[str, int]] | None:
    """Return ROIs for exactly two stable facecam slots used by Smart Portrait."""
    # Keep the existing call contract even though overlap, rather than portrait
    # crop width, determines whether two source regions are truly distinct.
    _ = crop_width
    selected = _select_facecam_rois(faces_per_frame, frame_width, frame_height, 2)
    return selected if len(selected) == 2 else None


def decide_gaming_facecam_layout(
    faces_per_frame: list[list[list[float]]],
    frame_width: int,
    frame_height: int,
) -> list[dict[str, int]]:
    """Return up to two stable edge-overlay facecams for Gaming 9:16.

    A stable face in the central gameplay region is usually an NPC or player
    character, not a webcam overlay. YuNet cannot distinguish those by visual
    identity alone, so Gaming mode also requires the track centre to stay in an
    outer screen band.
    """

    def is_edge_overlay(summary: dict[str, float]) -> bool:
        center_x_ratio = summary["center_x"] / max(1.0, frame_width)
        center_y_ratio = summary["center_y"] / max(1.0, frame_height)
        return min(
            center_x_ratio,
            1.0 - center_x_ratio,
            center_y_ratio,
            1.0 - center_y_ratio,
        ) <= GAMING_EDGE_ZONE_RATIO

    return _select_facecam_rois(
        faces_per_frame,
        frame_width,
        frame_height,
        2,
        candidate_filter=is_edge_overlay,
        max_center_std_ratio=GAMING_MAX_CENTER_STD_RATIO,
        min_presence=GAMING_MIN_PRESENCE,
    )


# --- Silero VAD -----------------------------------------------------------


def detect_speech_probabilities(audio, session) -> list[float]:
    """Run Silero VAD over 512-sample windows with a rolling 64-sample context.

    The recurrent state ([2, 1, 128]) carries across windows and is created fresh
    for every clip, so one clip's speech never bleeds into the next.
    """
    if np is None:
        raise WorkerError("VISION_RUNTIME_UNAVAILABLE", "numpy is required for smart crop.", status_code=503, retryable=True)
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((VAD_CONTEXT,), dtype=np.float32)
    sample_rate = np.array(VAD_SAMPLE_RATE, dtype=np.int64)
    probabilities: list[float] = []
    for offset in range(0, len(audio), VAD_WINDOW):
        window = audio[offset : offset + VAD_WINDOW]
        if len(window) < VAD_WINDOW:
            window = np.pad(window, (0, VAD_WINDOW - len(window)))
        features = np.concatenate([context, window]).reshape(1, -1).astype(np.float32)
        results = session.run(None, {"input": features, "state": state, "sr": sample_rate})
        for result in results:
            array = np.asarray(result)
            if array.ndim == 3:
                state = array.astype(np.float32)
            else:
                probabilities.append(float(np.ravel(array)[0]))
        context = window[-VAD_CONTEXT:]
    return probabilities


def build_speech_intervals(
    probabilities: list[float],
    window_seconds: float = VAD_WINDOW_SECONDS,
    threshold: float = VAD_THRESHOLD,
    speech_pad_seconds: float = VAD_SPEECH_PAD_SECONDS,
    merge_gap_seconds: float = VAD_MERGE_GAP_SECONDS,
) -> list[list[float]]:
    raw: list[list[float]] = []
    start_index: int | None = None
    for index, probability in enumerate(probabilities):
        if probability >= threshold and start_index is None:
            start_index = index
        elif probability < threshold and start_index is not None:
            raw.append([start_index * window_seconds, index * window_seconds])
            start_index = None
    if start_index is not None:
        raw.append([start_index * window_seconds, len(probabilities) * window_seconds])

    # Bridge short silences first so padding does not fragment one utterance.
    merged: list[list[float]] = []
    for interval in raw:
        if merged and interval[0] - merged[-1][1] <= merge_gap_seconds:
            merged[-1][1] = interval[1]
        else:
            merged.append(list(interval))
    return [[max(0.0, start - speech_pad_seconds), end + speech_pad_seconds] for start, end in merged]


def is_speech(time_seconds: float, intervals: list[list[float]]) -> bool:
    return any(start <= time_seconds <= end for start, end in intervals)


# --- Track assembly -------------------------------------------------------


def build_track(
    sample_times: list[float],
    start: float,
    max_x: float,
    output_scale: float,
    speech_intervals: list[list[float]],
    detect: Callable[[float], tuple[float, float] | None],
) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    previous_x = max_x / 2
    for timestamp in sample_times:
        relative = timestamp - start
        confidence = 0.0
        if is_speech(relative, speech_intervals):
            found = detect(timestamp)
            if found is not None:
                target_x, confidence = found
                clamped = min(max(0.0, target_x), max_x)
                previous_x = previous_x * SMOOTH_PREVIOUS + clamped * SMOOTH_TARGET
        points.append({"time": relative, "x": round(previous_x * output_scale, 2), "confidence": confidence})
    return points


def _load_session(model_path: str | Path):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])


def _extract_clip_audio(ffmpeg: str, source: str | Path, start: float, end: float):
    duration = max(0.0, end - start)
    completed = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(VAD_SAMPLE_RATE),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        creationflags=_CREATION_FLAGS,
    )
    if completed.returncode != 0 or not completed.stdout:
        # No audio stream (or a decode failure): treat the clip as silent so the
        # crop stays centered rather than failing the render.
        return np.array([], dtype=np.float32)
    return np.frombuffer(completed.stdout, dtype=np.float32).copy()


def _sample_video_faces(
    video_path: str | Path,
    start: float,
    end: float,
    yunet,
    yunet_input: str,
    sample_fps: float,
) -> tuple[int, int, list[float], list[list[list[float]]]]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise WorkerError("SMART_CROP_SOURCE_UNREADABLE", "The source video could not be opened for face analysis.", retryable=True)
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 30)
        if width <= 0 or height <= 0:
            raise WorkerError("SMART_CROP_SOURCE_UNREADABLE", "The source video has no readable dimensions.", retryable=True)
        frame_step = max(1, round(fps / sample_fps))
        effective_step = frame_step / max(fps, 1.0)
        sample_times: list[float] = []
        faces_per_frame: list[list[list[float]]] = []
        capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start) * 1000)
        moment = max(0.0, start)
        while moment < end:
            ok, frame = capture.read()
            if not ok:
                break
            sample_times.append(moment)
            faces_per_frame.append(_faces_in_original(frame, yunet, yunet_input))
            for _ in range(frame_step - 1):
                if not capture.grab():
                    break
            moment += effective_step
        return width, height, sample_times, faces_per_frame
    finally:
        capture.release()


def smart_crop_track(
    video_path: str | Path,
    start: float,
    end: float,
    yunet_path: str | Path,
    silero_path: str | Path,
    ffmpeg: str,
    sample_fps: float = SAMPLE_FPS,
) -> SmartCropResult:
    if cv2 is None or np is None:
        raise WorkerError("VISION_RUNTIME_UNAVAILABLE", "OpenCV and numpy are required for smart crop.", status_code=503, retryable=True)
    try:
        yunet = _load_session(yunet_path)
        yunet_input = yunet.get_inputs()[0].name

        audio = _extract_clip_audio(ffmpeg, video_path, start, end)
        speech_intervals: list[list[float]] = []
        if audio.size:
            silero = _load_session(silero_path)
            speech_intervals = build_speech_intervals(detect_speech_probabilities(audio, silero))

        width, height, sample_times, faces_per_frame = _sample_video_faces(
            video_path, start, end, yunet, yunet_input, sample_fps
        )
        crop_width = height * 9 / 16
        max_x = max(0.0, width - crop_width)
        output_scale = 1920 / height

        facecams = decide_facecam_layout(faces_per_frame, width, height, crop_width)
        if facecams is not None:
            return SmartCropResult(mode="dual", track=[], facecams=facecams)

        faces_by_time = dict(zip(sample_times, faces_per_frame, strict=True))

        def detect(moment: float) -> tuple[float, float] | None:
            face = largest_face(faces_by_time.get(moment, []))
            if face is None:
                return None
            return _target_from_face(face, width, height, crop_width)

        track = build_track(sample_times, start, max_x, output_scale, speech_intervals, detect)
        return SmartCropResult(mode="single", track=track, facecams=[])
    except WorkerError:
        raise
    except Exception as error:  # ONNX Runtime / decode failures fail only this clip.
        raise WorkerError(
            "SMART_CROP_FAILED",
            "Smart portrait tracking failed for this clip.",
            retryable=True,
            details=str(error),
        ) from error


def gaming_facecam_track(
    video_path: str | Path,
    start: float,
    end: float,
    yunet_path: str | Path,
    sample_fps: float = SAMPLE_FPS,
) -> list[dict[str, int]]:
    if cv2 is None or np is None:
        raise WorkerError("VISION_RUNTIME_UNAVAILABLE", "OpenCV and numpy are required for Gaming 9:16.", status_code=503, retryable=True)
    try:
        yunet = _load_session(yunet_path)
        yunet_input = yunet.get_inputs()[0].name
        width, height, _, faces_per_frame = _sample_video_faces(
            video_path, start, end, yunet, yunet_input, sample_fps
        )
        return decide_gaming_facecam_layout(faces_per_frame, width, height)
    except WorkerError:
        raise
    except Exception as error:
        raise WorkerError(
            "GAMING_FACECAM_FAILED",
            "Gaming facecam analysis failed for this clip.",
            retryable=True,
            details=str(error),
        ) from error


def ffmpeg_crop_commands(track: list[dict[str, float]]) -> str:
    return "\n".join(f"{point['time']:.3f} crop@smart x {point['x']:.2f};" for point in track)


def dual_facecam_filter(facecams: list[dict[str, int]]) -> str:
    """Build a filter_complex that stacks a centered gameplay main area over two
    16:9 facecam cards. Output is a single [vout] label at 1080x1920, even-safe."""
    if len(facecams) != 2:
        raise WorkerError("SMART_CROP_FAILED", "Dual facecam layout requires exactly two ROIs.", retryable=True)
    left, right = facecams
    gameplay = (
        f"[0:v]crop=ih*{DUAL_MAIN_WIDTH}/{DUAL_MAIN_HEIGHT}:ih:(iw-ow)/2:0,"
        f"scale={DUAL_MAIN_WIDTH}:{DUAL_MAIN_HEIGHT},setsar=1[main]"
    )
    branches = [gameplay]
    for index, cam in enumerate((left, right)):
        branches.append(
            f"[0:v]crop={cam['w']}:{cam['h']}:{cam['x']}:{cam['y']},"
            f"scale={DUAL_CARD_WIDTH}:{DUAL_CARD_HEIGHT},setsar=1[cam{index}]"
        )
    branches.append("[cam0][cam1]hstack=inputs=2[cams]")
    branches.append("[main][cams]vstack=inputs=2[stacked]")
    return ";".join(branches)


def gaming_facecam_filter(facecams: list[dict[str, int]]) -> str:
    """Place one full-width or two side-by-side facecams above gameplay."""
    if len(facecams) not in {1, 2}:
        raise WorkerError(
            "GAMING_FACECAM_FAILED",
            "Gaming 9:16 requires one or two facecam ROIs.",
            retryable=True,
        )

    if len(facecams) == 1:
        cam = facecams[0]
        # The detector returns a 16:9 ROI. Trim it around the same centre to 2:1
        # before scaling so the 1080x540 header is filled without stretching.
        crop_width = cam["w"] - (cam["w"] % 4)
        crop_height = crop_width // 2
        crop_x = cam["x"] + (cam["w"] - crop_width) // 2
        crop_y = cam["y"] + (cam["h"] - crop_height) // 2
        crop_x -= crop_x % 2
        crop_y -= crop_y % 2
        gameplay = (
            f"[0:v]crop=ih*{DUAL_MAIN_WIDTH}/{GAMING_SINGLE_MAIN_HEIGHT}:ih:(iw-ow)/2:0,"
            f"scale={DUAL_MAIN_WIDTH}:{GAMING_SINGLE_MAIN_HEIGHT},setsar=1[main]"
        )
        facecam = (
            f"[0:v]crop={crop_width}:{crop_height}:{crop_x}:{crop_y},"
            f"scale={DUAL_MAIN_WIDTH}:{GAMING_SINGLE_HEADER_HEIGHT},setsar=1,"
            f"drawbox=x=0:y={GAMING_SINGLE_HEADER_HEIGHT - GAMING_SINGLE_DIVIDER_HEIGHT}:"
            f"w=iw:h={GAMING_SINGLE_DIVIDER_HEIGHT}:color=black:t=fill[cams]"
        )
        return ";".join([gameplay, facecam, "[cams][main]vstack=inputs=2[stacked]"])

    gameplay = (
        f"[0:v]crop=ih*{DUAL_MAIN_WIDTH}/{DUAL_MAIN_HEIGHT}:ih:(iw-ow)/2:0,"
        f"scale={DUAL_MAIN_WIDTH}:{DUAL_MAIN_HEIGHT},setsar=1[main]"
    )
    branches = [gameplay]
    for index, cam in enumerate(facecams):
        branches.append(
            f"[0:v]crop={cam['w']}:{cam['h']}:{cam['x']}:{cam['y']},"
            f"scale={DUAL_CARD_WIDTH}:{DUAL_CARD_HEIGHT},setsar=1[cam{index}]"
        )
    branches.append("[cam0][cam1]hstack=inputs=2[cams]")
    branches.append("[cams][main]vstack=inputs=2[stacked]")
    return ";".join(branches)
