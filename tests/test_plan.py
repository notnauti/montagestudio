"""Validator rules from data/(C) scene-plan-contract.md, with a fake prober (no ffmpeg needed)."""

import copy
import json

import pytest

from montage.plan import PlanError, load_plan, parse_plan
from montage.probe import MediaInfo, ProbeError

# The minimal valid plan from the contract note, verbatim.
MINIMAL = {
    "meta": {"title": "slice1-smoke", "resolution": [1920, 1080], "fps": 30, "durationFrames": 300},
    "tracks": [
        {"id": "narration", "type": "audio", "clips": [
            {"id": "vo_01", "src": "./assets/vo_01.wav", "startFrame": 0, "durationFrames": 290},
        ]},
        {"id": "visuals", "type": "video", "clips": [
            {"id": "shot_a", "src": "./assets/archive.mp4", "startFrame": 0, "durationFrames": 165,
             "sourceInFrame": 120, "transition": {"type": "crossfade", "durationFrames": 15}},
            {"id": "shot_b", "src": "./assets/diagram.png", "startFrame": 150, "durationFrames": 150,
             "motion": {"scale": [1.0, 1.08], "easing": "easeInOutCubic"}},
        ]},
    ],
}


def fake_probe(path):
    return {
        ".wav": MediaInfo(False, True, 20.0),
        ".mp4": MediaInfo(True, False, 20.0),
        ".png": MediaInfo(True, False, None),
    }[path.suffix]


@pytest.fixture
def ws(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in ("vo_01.wav", "archive.mp4", "diagram.png", "music.wav", "short.mp4"):
        (assets / name).write_bytes(b"")
    (tmp_path / "outside.mp4").write_bytes(b"")
    return tmp_path


def plan():
    return copy.deepcopy(MINIMAL)


def video(p):
    return p["tracks"][1]["clips"]


def audio(p):
    return p["tracks"][0]["clips"]


def errors_for(p, ws, probe=fake_probe):
    with pytest.raises(PlanError) as exc:
        parse_plan(p, ws, probe=probe)
    return exc.value.errors


def test_minimal_plan_from_contract_is_valid(ws):
    result = parse_plan(plan(), ws, probe=fake_probe)
    assert result.meta.duration_frames == 300
    a, b = result.video.clips
    assert (a.source_in_frame, a.crossfade_frames, a.fit) == (120, 15, "cover")
    assert b.is_still and b.motion.easing == "easeInOutCubic" and b.source_in_frame == 0
    assert result.audio[0].clips[0].volume == 1.0


def test_load_plan_resolves_src_against_plan_directory(ws):
    (ws / "scene_plan.json").write_text(json.dumps(plan()), encoding="utf-8")
    result = load_plan(ws / "scene_plan.json", probe=fake_probe)
    assert result.video.clips[0].src == (ws / "assets" / "archive.mp4").resolve()


def test_bad_json_is_a_plan_error(ws):
    (ws / "scene_plan.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(PlanError):
        load_plan(ws / "scene_plan.json", probe=fake_probe)


def _set(path, value):
    def mutate(p):
        target = p
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return mutate


def _del(path):
    def mutate(p):
        target = p
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return mutate


V, A = ["tracks", 1, "clips"], ["tracks", 0, "clips"]

CASES = {
    "unknown top-level field": (_set(["captions"], []), "unsupported field 'captions'"),
    "unknown clip field": (_set(V + [0, "speed"], 2), "unsupported field 'speed'"),
    "unknown motion field": (_set(V + [1, "motion", "position"], [0, 0]), "unsupported field 'position'"),
    "odd resolution": (_set(["meta", "resolution"], [1921, 1080]), "even"),
    "fractional fps": (_set(["meta", "fps"], 29.97), "fps"),
    "bool fps": (_set(["meta", "fps"], True), "fps"),
    "missing title": (_del(["meta", "title"]), "missing 'title'"),
    "second video track": (lambda p: p["tracks"].append(copy.deepcopy(p["tracks"][1]) | {"id": "v2"}),
                           "exactly one video track"),
    "no audio track": (lambda p: p["tracks"].pop(0), "at least one audio track"),
    "gap between video clips": (lambda p: video(p)[1].update(startFrame=160, durationFrames=140), "expected 150"),
    "hard cut must butt": (_del(V + [0, "transition"]), "expected 165"),
    "first clip not at 0": (_set(V + [0, "startFrame"], 5), "must start at frame 0"),
    "transition on last clip": (_set(V + [1, "transition"], {"type": "crossfade", "durationFrames": 10}),
                                "cannot have a transition"),
    "non-crossfade transition": (_set(V + [0, "transition", "type"], "wipe"), "only 'crossfade'"),
    "crossfade as long as clip": (lambda p: (video(p)[0]["transition"].update(durationFrames=165),
                                             video(p)[1].update(startFrame=0, durationFrames=300)),
                                  "must be shorter"),
    "zoom below 1": (_set(V + [1, "motion", "scale"], [0.9, 1.0]), "scale"),
    "unknown easing": (_set(V + [1, "motion", "easing"], "bounce"), "easing"),
    "src outside assets": (_set(V + [0, "src"], "./outside.mp4"), "inside assets/"),
    "src missing": (_set(V + [0, "src"], "./assets/nope.mp4"), "file not found"),
    "video track ends early": (_set(V + [1, "durationFrames"], 140), "ends at frame 290"),
    "clip past meta duration": (_set(A + [0, "durationFrames"], 400), "past meta.durationFrames"),
    "audio overlap in one track": (lambda p: audio(p).append(
        {"id": "vo_02", "src": "./assets/music.wav", "startFrame": 100, "durationFrames": 50}), "overlapping"),
    "unsorted clips": (lambda p: audio(p).insert(0, {"id": "vo_00", "src": "./assets/music.wav",
                                                       "startFrame": 295, "durationFrames": 5}), "sorted"),
    "duplicate clip id": (_set(V + [1, "id"], "vo_01"), "duplicate clip id"),
    "still image on audio track": (_set(A + [0, "src"], "./assets/diagram.png"), "cannot use a still"),
    "volume above 1": (_set(A + [0, "volume"], 1.5), "volume"),
    "unknown fit": (_set(V + [0, "fit"], "stretch"), "fit"),
    "source too short for in-point": (_set(V + [0, "sourceInFrame"], 500), "clip needs"),
}


@pytest.mark.parametrize("mutate,expected", CASES.values(), ids=CASES.keys())
def test_rule_violation_is_reported(ws, mutate, expected):
    p = plan()
    mutate(p)
    errs = errors_for(p, ws)
    assert any(expected in e for e in errs), errs


def test_all_errors_reported_together(ws):
    p = plan()
    p["meta"]["resolution"] = [1921, 1080]
    video(p)[1]["motion"]["easing"] = "bounce"
    audio(p)[0]["volume"] = 2
    errs = errors_for(p, ws)
    assert len(errs) == 3, errs


def test_media_without_expected_stream(ws):
    probe = lambda path: MediaInfo(False, False, 20.0)  # noqa: E731
    errs = errors_for(plan(), ws, probe=probe)
    assert any("no audio stream" in e for e in errs) and any("no video stream" in e for e in errs)


def test_unreadable_media(ws):
    def probe(path):
        raise ProbeError("moov atom not found")
    errs = errors_for(plan(), ws, probe=probe)
    assert any("unreadable media" in e for e in errs)


def test_media_not_probed_when_structure_is_broken(ws):
    calls = []
    p = plan()
    p["meta"]["fps"] = 0
    errors_for(p, ws, probe=lambda path: calls.append(path) or fake_probe(path))
    assert calls == []
