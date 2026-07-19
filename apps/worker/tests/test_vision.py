from __future__ import annotations

import math

import numpy as np
import pytest

from app import vision
from app.vision import (
    build_speech_intervals,
    build_track,
    decide_facecam_layout,
    decide_gaming_facecam_layout,
    decode_yunet,
    detect_speech_probabilities,
    dual_facecam_filter,
    gaming_facecam_filter,
    is_speech,
    largest_face,
    layout_preview_plan,
    nms,
    SmartCropResult,
)


# --- YuNet decoding -------------------------------------------------------


def _yunet_outputs(input_side: int, stride: int, face_index: int, *, dw: float = 0.0, score: float = 0.9):
    cols = input_side // stride
    count = cols * cols
    bbox = np.zeros((count, 4), dtype=np.float32)
    # Center the face inside its grid cell and size it via the log-space width.
    bbox[face_index] = [0.5, 0.5, dw, dw]
    cls = np.full((count, 1), 0.1, dtype=np.float32)
    obj = np.full((count, 1), 0.1, dtype=np.float32)
    cls[face_index] = obj[face_index] = score
    return [cls, bbox, obj]


def test_decode_yunet_recovers_single_face_box() -> None:
    outputs = _yunet_outputs(64, 32, face_index=3)  # row 1, col 1
    boxes = decode_yunet(outputs, 64, 64)
    assert len(boxes) == 1
    x, y, w, h, score = boxes[0]
    assert (x, y, w, h) == pytest.approx((32.0, 32.0, 32.0, 32.0))
    assert score == pytest.approx(0.9)


def test_decode_yunet_drops_low_confidence_cells() -> None:
    outputs = _yunet_outputs(64, 32, face_index=0, score=0.5)  # below 0.6 threshold
    assert decode_yunet(outputs, 64, 64) == []


def test_nms_removes_overlapping_lower_score_boxes() -> None:
    strong = [10.0, 10.0, 40.0, 40.0, 0.95]
    overlap = [12.0, 12.0, 40.0, 40.0, 0.80]
    far = [200.0, 200.0, 40.0, 40.0, 0.90]
    kept = nms([overlap, strong, far], iou_threshold=0.3)
    assert strong in kept and far in kept
    assert overlap not in kept


def test_largest_face_prefers_biggest_area() -> None:
    small = [0.0, 0.0, 10.0, 10.0, 0.99]
    big = [0.0, 0.0, 30.0, 40.0, 0.70]
    assert largest_face([small, big]) is big
    assert largest_face([]) is None


# --- Track smoothing / hold-crop -----------------------------------------


def test_build_track_applies_hysteresis_smoothing_during_speech() -> None:
    # One speech interval covering the whole clip; detect always returns x=100.
    track = build_track(
        sample_times=[0.0, 0.2, 0.4],
        start=0.0,
        max_x=200.0,
        output_scale=1.0,
        speech_intervals=[[0.0, 1.0]],
        detect=lambda _t: (100.0, 0.8),
    )
    # previous starts at max_x/2 = 100, target is also 100 -> stays at 100.
    assert [point["x"] for point in track] == pytest.approx([100.0, 100.0, 100.0])

    moved = build_track(
        sample_times=[0.0, 0.2],
        start=0.0,
        max_x=200.0,
        output_scale=1.0,
        speech_intervals=[[0.0, 1.0]],
        detect=lambda _t: (0.0, 0.8),
    )
    # 100*0.72 + 0*0.28 = 72, then 72*0.72 + 0*0.28 = 51.84
    assert [point["x"] for point in moved] == pytest.approx([72.0, 51.84])


def test_build_track_holds_last_crop_during_silence() -> None:
    calls: list[float] = []

    def detect(timestamp: float):
        calls.append(timestamp)
        return (0.0, 0.9)

    track = build_track(
        sample_times=[0.0, 0.2, 0.4, 0.6],
        start=0.0,
        max_x=200.0,
        output_scale=1.0,
        speech_intervals=[[0.0, 0.25]],  # speech only for the first two samples
        detect=detect,
    )
    xs = [point["x"] for point in track]
    # After silence starts, x is held at the last speaking value.
    assert xs[2] == pytest.approx(xs[1])
    assert xs[3] == pytest.approx(xs[1])
    assert calls == [0.0, 0.2]  # detector never runs during silence


def test_build_track_center_crops_when_no_speech() -> None:
    track = build_track(
        sample_times=[0.0, 0.2, 0.4],
        start=0.0,
        max_x=200.0,
        output_scale=1.0,
        speech_intervals=[],
        detect=lambda _t: pytest.fail("detector must not run without speech"),
    )
    assert [point["x"] for point in track] == pytest.approx([100.0, 100.0, 100.0])
    assert all(point["confidence"] == 0.0 for point in track)


# --- Silero VAD -----------------------------------------------------------


def test_build_speech_intervals_merges_short_gaps_and_pads() -> None:
    window = vision.VAD_WINDOW_SECONDS
    # speech, one-window gap, speech again -> merged because gap < 0.3s.
    probabilities = [0.9, 0.9, 0.1, 0.9, 0.9]
    intervals = build_speech_intervals(probabilities, window_seconds=window)
    assert len(intervals) == 1
    start, end = intervals[0]
    assert start == pytest.approx(max(0.0, 0.0 - vision.VAD_SPEECH_PAD_SECONDS))
    assert end == pytest.approx(5 * window + vision.VAD_SPEECH_PAD_SECONDS)


def test_build_speech_intervals_keeps_distinct_regions() -> None:
    # A long silence (many windows) must not be bridged.
    probabilities = [0.9] + [0.1] * 40 + [0.9]
    intervals = build_speech_intervals(probabilities)
    assert len(intervals) == 2


def test_is_speech_checks_membership() -> None:
    intervals = [[1.0, 2.0], [5.0, 6.0]]
    assert is_speech(1.5, intervals) is True
    assert is_speech(3.0, intervals) is False


class _FakeSileroSession:
    """Emits high probability for loud windows and a fresh recurrent state."""

    def __init__(self) -> None:
        self.seen_shapes: list[tuple[int, ...]] = []

    def run(self, _outputs, feeds):
        features = feeds["input"]
        state = feeds["state"]
        self.seen_shapes.append(tuple(features.shape))
        assert state.shape == (2, 1, 128)
        energy = float(np.abs(features).mean())
        probability = np.array([[1.0 if energy > 0.5 else 0.0]], dtype=np.float32)
        return [probability, state + 0.0]


def test_detect_speech_probabilities_uses_64_plus_512_window_contract() -> None:
    session = _FakeSileroSession()
    audio = np.concatenate([np.ones(vision.VAD_WINDOW, dtype=np.float32), np.zeros(vision.VAD_WINDOW, dtype=np.float32)])
    probabilities = detect_speech_probabilities(audio, session)
    assert probabilities == [1.0, 0.0]
    # 64 context samples prepended to each 512-sample window.
    assert session.seen_shapes == [(1, vision.VAD_CONTEXT + vision.VAD_WINDOW)] * 2


def test_detect_speech_probabilities_resets_state_between_calls() -> None:
    session = _FakeSileroSession()
    audio = np.ones(vision.VAD_WINDOW, dtype=np.float32)
    first = detect_speech_probabilities(audio, session)
    second = detect_speech_probabilities(audio, session)
    assert first == second  # a fresh state each call keeps clips independent


# --- Dual facecam clustering / decision -----------------------------------

_FW, _FH = 1920, 1080
_CROP_WIDTH = _FH * 9 / 16  # 607.5


def _dual_frames(center_lists, *, w=120, h=120, score=0.9, frames=10, y=100.0):
    """Build faces_per_frame; each entry in center_lists is a per-cluster list of
    horizontal centres cycled across frames (single-element list = stable face)."""
    per_frame = []
    for index in range(frames):
        faces = []
        for centers in center_lists:
            cx = centers[index % len(centers)]
            faces.append([cx - w / 2, float(y), float(w), float(h), score])
        per_frame.append(faces)
    return per_frame


def test_decide_facecam_layout_returns_two_ordered_rois() -> None:
    frames = _dual_frames([[1700], [200]])  # right passed first
    rois = decide_facecam_layout(frames, _FW, _FH, _CROP_WIDTH)
    assert rois is not None and len(rois) == 2
    left, right = rois
    assert left["x"] < right["x"]  # ordered left-to-right regardless of input order
    assert left["w"] == right["w"] and left["w"] % 2 == 0
    assert left["h"] % 2 == 0
    assert 0 <= left["x"] and right["x"] + right["w"] <= _FW


def test_decide_facecam_layout_selects_two_largest_of_three_faces() -> None:
    frames = []
    for _ in range(10):
        frames.append([
            [1640.0, 100.0, 160.0, 160.0, 0.9],
            [140.0, 100.0, 140.0, 140.0, 0.9],
            [900.0, 100.0, 80.0, 80.0, 0.9],
        ])
    rois = decide_facecam_layout(frames, _FW, _FH, _CROP_WIDTH)
    assert rois is not None
    assert rois[0]["x"] < 200
    # The crop is clamped at the source edge, but it must still belong to the
    # large right-hand face rather than the smaller face around x=940.
    right_center = rois[1]["x"] + rois[1]["w"] / 2
    assert right_center > 1500


def test_decide_facecam_layout_selects_large_face_and_best_intermittent_thumbnail() -> None:
    frames = []
    for index in range(200):
        faces = [[140.0, 160.0, 120.0, 120.0, 0.9]]
        if index % 10 == 0:
            faces.extend([
                [670.0, 940.0, 60.0, 60.0, 0.78],
                [970.0, 945.0, 48.0, 48.0, 0.75],
                [1270.0, 950.0, 40.0, 40.0, 0.72],
            ])
        frames.append(faces)
    rois = decide_facecam_layout(frames, _FW, _FH, _CROP_WIDTH)
    assert rois is not None
    assert rois[0]["x"] < 200
    second_center = rois[1]["x"] + rois[1]["w"] / 2
    assert second_center == pytest.approx(700.0, abs=2.0)


def test_decide_facecam_layout_rejects_single_face() -> None:
    frames = _dual_frames([[960]])
    assert decide_facecam_layout(frames, _FW, _FH, _CROP_WIDTH) is None


def test_decide_facecam_layout_rejects_faces_too_close_for_separate_crops() -> None:
    # Both faces track separately, but their estimated 16:9 source ROIs overlap.
    frames = _dual_frames([[1050], [1250]])
    assert decide_facecam_layout(frames, _FW, _FH, _CROP_WIDTH) is None


def test_decide_facecam_layout_rejects_unstable_cluster() -> None:
    # Right cluster wanders enough to exceed the center-stability threshold.
    frames = _dual_frames([[200], [1600, 1750, 1900]])
    assert decide_facecam_layout(frames, _FW, _FH, _CROP_WIDTH) is None


def test_decide_facecam_layout_rejects_moving_gameplay_face() -> None:
    frames = []
    for index in range(20):
        moving_x = 1200.0 + math.sin(index / 2) * 100.0
        moving_y = 420.0 + math.cos(index / 2) * 50.0
        frames.append([
            [140.0, 100.0, 120.0, 120.0, 0.9],
            [moving_x - 60.0, moving_y - 60.0, 120.0, 120.0, 0.9],
        ])
    assert decide_facecam_layout(frames, _FW, _FH, _CROP_WIDTH) is None


def test_decide_facecam_layout_rejects_low_presence() -> None:
    # Two stable, separated faces but each only appears in 3/10 frames.
    frames = _dual_frames([[200], [1700]], frames=10)
    for index, faces in enumerate(frames):
        if index % 10 >= 3:
            faces.clear()
    assert decide_facecam_layout(frames, _FW, _FH, _CROP_WIDTH) is None


def test_decide_facecam_layout_rejects_low_confidence_faces() -> None:
    frames = _dual_frames([[200], [1700]], score=0.5)
    assert decide_facecam_layout(frames, _FW, _FH, _CROP_WIDTH) is None


def test_decide_facecam_layout_rejects_no_faces() -> None:
    assert decide_facecam_layout([[], [], []], _FW, _FH, _CROP_WIDTH) is None
    assert decide_facecam_layout([], _FW, _FH, _CROP_WIDTH) is None


def test_decide_gaming_facecam_layout_accepts_single_stable_face() -> None:
    rois = decide_gaming_facecam_layout(_dual_frames([[960]]), _FW, _FH)
    assert len(rois) == 1
    assert rois[0]["w"] % 2 == 0 and rois[0]["h"] % 2 == 0


def test_decide_gaming_facecam_layout_returns_two_ordered_faces() -> None:
    rois = decide_gaming_facecam_layout(_dual_frames([[1700], [200]]), _FW, _FH)
    assert len(rois) == 2
    assert rois[0]["x"] < rois[1]["x"]


def test_decide_gaming_facecam_layout_rejects_central_game_character() -> None:
    frames = []
    for _ in range(20):
        frames.append([
            [140.0, 820.0, 120.0, 120.0, 0.92],  # streamer overlay, bottom-left
            [900.0, 340.0, 120.0, 120.0, 0.91],  # stable dialogue character
        ])

    rois = decide_gaming_facecam_layout(frames, _FW, _FH)

    assert len(rois) == 1
    assert rois[0]["x"] < 300


def test_decide_gaming_facecam_layout_rejects_central_face_without_overlay() -> None:
    frames = [
        [[900.0, 340.0, 120.0, 120.0, 0.91]]
        for _ in range(20)
    ]

    assert decide_gaming_facecam_layout(frames, _FW, _FH) == []


def test_decide_gaming_facecam_layout_allows_natural_facecam_movement() -> None:
    # About 3.1% horizontal centre deviation: too mobile for a fixed dual-card
    # slot, but normal head movement for a streamer overlay in Gaming mode.
    frames = _dual_frames([[140, 260]], frames=20, y=820.0)

    rois = decide_gaming_facecam_layout(frames, _FW, _FH)

    assert len(rois) == 1
    assert rois[0]["x"] < 300


def test_decide_gaming_facecam_layout_rejects_short_lived_edge_character() -> None:
    frames = []
    for index in range(20):
        faces = [[140.0, 820.0, 120.0, 120.0, 0.92]]
        if index < 4:
            faces.append([900.0, 80.0, 120.0, 120.0, 0.91])
        frames.append(faces)

    rois = decide_gaming_facecam_layout(frames, _FW, _FH)

    assert len(rois) == 1
    assert rois[0]["x"] < 300


def test_decide_gaming_facecam_layout_returns_empty_without_eligible_face() -> None:
    assert decide_gaming_facecam_layout([], _FW, _FH) == []
    assert decide_gaming_facecam_layout(_dual_frames([[960]], score=0.5), _FW, _FH) == []


# --- Dual / gaming facecam filter graphs ----------------------------------


def test_dual_facecam_filter_builds_stacked_1080x1920_graph() -> None:
    cams = [
        {"x": 0, "y": 40, "w": 506, "h": 284},
        {"x": 1412, "y": 40, "w": 506, "h": 284},
    ]
    graph = dual_facecam_filter(cams)
    assert "scale=1080:1616" in graph  # gameplay main area
    assert "crop=506:284:0:40" in graph
    assert "crop=506:284:1412:40" in graph
    assert "scale=540:304" in graph  # each 16:9 card
    assert "[cam0][cam1]hstack=inputs=2[cams]" in graph
    assert "[main][cams]vstack=inputs=2[stacked]" in graph


def test_dual_facecam_filter_requires_exactly_two_rois() -> None:
    from app.errors import WorkerError

    with pytest.raises(WorkerError):
        dual_facecam_filter([{"x": 0, "y": 0, "w": 100, "h": 100}])


def test_gaming_facecam_filter_fills_header_with_single_facecam() -> None:
    graph = gaming_facecam_filter([{"x": 0, "y": 40, "w": 506, "h": 284}])
    assert "crop=ih*1080/1380:ih:(iw-ow)/2:0" in graph
    assert "scale=1080:1380" in graph
    assert "crop=504:252:0:56" in graph  # centred 2:1 crop, even-safe
    assert "scale=1080:540" in graph
    assert "drawbox=x=0:y=536:w=iw:h=4:color=black:t=fill[cams]" in graph
    assert "pad=" not in graph
    assert "[cams][main]vstack=inputs=2[stacked]" in graph


def test_gaming_facecam_filter_stacks_two_facecams_above_gameplay() -> None:
    graph = gaming_facecam_filter([
        {"x": 0, "y": 40, "w": 506, "h": 284},
        {"x": 1412, "y": 40, "w": 506, "h": 284},
    ])
    assert "scale=1080:1616" in graph
    assert graph.count("scale=540:304") == 2
    assert "[cam0][cam1]hstack=inputs=2[cams]" in graph
    assert "[cams][main]vstack=inputs=2[stacked]" in graph


def test_layout_preview_plan_normalizes_smart_tracking_keyframes() -> None:
    result = SmartCropResult(
        mode="single",
        track=[{"time": 0.0, "x": 0.0}, {"time": 0.2, "x": 960.0}],
        facecams=[],
    )

    plan = layout_preview_plan("smart_portrait", 1920, 1080, result, "smart-key")

    assert plan["mode"] == "single"
    assert len(plan["keyframes"]) == 2
    first_source = plan["keyframes"][0]["layers"][0]["source"]
    last_source = plan["keyframes"][1]["layers"][0]["source"]
    assert first_source == pytest.approx({"x": 0, "y": 0, "width": 607.5 / 1920, "height": 1})
    assert last_source["x"] == pytest.approx(540 / 1920)
    assert plan["keyframes"][0]["layers"][0]["destination"] == {"x": 0, "y": 0, "width": 1, "height": 1}


def test_layout_preview_plan_matches_dual_and_gaming_layer_geometry() -> None:
    cameras = [
        {"x": 0, "y": 40, "w": 506, "h": 284},
        {"x": 1412, "y": 40, "w": 506, "h": 284},
    ]
    dual = layout_preview_plan("smart_portrait", 1920, 1080, SmartCropResult(mode="dual", track=[], facecams=cameras), "dual-key")
    gaming = layout_preview_plan("gaming_portrait", 1920, 1080, cameras, "gaming-key")

    assert dual["mode"] == "dual"
    assert len(dual["keyframes"][0]["layers"]) == 3
    assert dual["keyframes"][0]["layers"][0]["destination"]["height"] == pytest.approx(1616 / 1920)
    assert dual["keyframes"][0]["layers"][2]["destination"]["x"] == pytest.approx(0.5)
    assert gaming["mode"] == "gaming_dual"
    assert len(gaming["keyframes"][0]["layers"]) == 3
    assert gaming["keyframes"][0]["layers"][2]["destination"]["y"] == pytest.approx(304 / 1920)


@pytest.mark.parametrize("facecams", [[], [{"x": 0, "y": 0, "w": 100, "h": 100}] * 3])
def test_gaming_facecam_filter_requires_one_or_two_rois(facecams) -> None:
    from app.errors import WorkerError

    with pytest.raises(WorkerError):
        gaming_facecam_filter(facecams)
