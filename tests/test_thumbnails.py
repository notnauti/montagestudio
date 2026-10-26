"""publish-pack thumbnail candidates: frames clear of every cut and crossfade, grabbed as YouTube-sized JPEGs."""

import json
import shutil
import subprocess

import pytest

from montage import cli, publish
from montage.plan import load_plan
from montage.probe import probe as real_probe
from test_publish import fake_probe, ws  # noqa: F401 -- ws is the publishable-workspace fixture

FPS = 30
XF = 12

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _plan_doc(srcs, beat=90, xf=XF):
    clips, start = [], 0
    for i, src in enumerate(srcs):
        c = {"id": f"shot_{i + 1:02d}", "src": src, "startFrame": start, "durationFrames": beat}
        if i < len(srcs) - 1 and xf:
            c["transition"] = {"type": "crossfade", "durationFrames": xf}
        clips.append(c)
        start += beat - (xf if i < len(srcs) - 1 else 0)
    bed = {"id": "bed", "src": "./assets/bed.wav", "startFrame": 0, "durationFrames": start}
    return {"meta": {"title": "T", "resolution": [1920, 1080], "fps": FPS, "durationFrames": start},
            "tracks": [{"id": "visuals", "type": "video", "clips": clips},
                       {"id": "music", "type": "audio", "clips": [bed]}]}


def _load(tmp_path, srcs, probe=fake_probe, **kw):
    (tmp_path / "assets").mkdir(exist_ok=True)
    for s in set(srcs) | {"./assets/bed.wav"}:
        p = tmp_path / s
        if not p.exists():
            p.write_bytes(b"x")
    path = tmp_path / "scene_plan.json"
    path.write_text(json.dumps(_plan_doc(srcs, **kw)), encoding="utf-8")
    return load_plan(path, probe=probe)


def _alone(plan, frame, margin):
    """Exactly one clip covers [frame - margin, frame + margin]."""
    covering = lambda f: [c for c in plan.video.clips if c.start_frame <= f < c.end_frame]  # noqa: E731
    return all(len(covering(f)) == 1 for f in range(frame - margin, frame + margin + 1)) and \
        covering(frame - margin)[0] is covering(frame + margin)[0]


def test_frames_stay_clear_of_cuts_and_crossfades(tmp_path):
    plan = _load(tmp_path, [f"./assets/{c}.jpg" for c in "abcdef"])
    thumbs = publish.thumbnail_frames(plan)
    assert len(thumbs) == 3
    for t in thumbs:
        assert _alone(plan, t.frame, publish.THUMB_MARGIN), t


def test_hard_cuts_get_the_margin_too(tmp_path):
    plan = _load(tmp_path, [f"./assets/{c}.jpg" for c in "abc"], xf=0)
    for t in publish.thumbnail_frames(plan):
        assert _alone(plan, t.frame, publish.THUMB_MARGIN), t


def test_one_frame_per_source_file_spread_over_the_video(tmp_path):
    srcs = ["./assets/a.jpg", "./assets/a.jpg", "./assets/b.jpg", "./assets/c.jpg", "./assets/d.jpg",
            "./assets/e.jpg", "./assets/f.jpg"]
    thumbs = publish.thumbnail_frames(_load(tmp_path, srcs))
    assert len({t.src for t in thumbs}) == 3
    # shot_02 repeats shot_01's file; of the 6 distinct sources, the middle one of each third
    assert [t.clip_id for t in thumbs] == ["shot_03", "shot_05", "shot_07"]
    assert [t.frame for t in thumbs] == sorted(t.frame for t in thumbs)


def test_fewer_candidates_when_clips_are_too_short(tmp_path):
    # 30-frame clips: alone for 30 - 12 - 12 = 6 frames in the middle, less than 2 x 6 + 1
    plan = _load(tmp_path, [f"./assets/{c}.jpg" for c in "abcd"], beat=30)
    thumbs = publish.thumbnail_frames(plan)
    assert [t.clip_id for t in thumbs] == ["shot_01", "shot_04"]   # first and last have one fade only
    pack = publish.Pack(tmp_path / "publish" / "youtube.md", "## Upload settings\n", tmp_path, (), (), (), (),
                        plan, ())
    assert "- none: no clip is alone on screen long enough" in publish.with_thumbnails(pack, {}, {}).text


def test_a_failed_grab_is_listed_not_fatal(ws, capsys):   # noqa: F811 -- the fixture's media are fake bytes
    assert cli.main(["publish-pack", str(ws)]) == 0
    text = (ws / "publish" / "youtube.md").read_text(encoding="utf-8")
    assert "## Thumbnail" in text and "thumb_1: not rendered (" in text
    assert text.index("## Thumbnail") < text.index("## Upload settings")
    assert "warning: thumbnail 1 not rendered" in capsys.readouterr().err
    assert not list((ws / "publish" / publish.THUMB_DIR).glob("*.jpg"))


def test_a_refused_overwrite_keeps_the_old_candidates(ws):   # noqa: F811
    assert cli.main(["publish-pack", str(ws)]) == 0
    keep = ws / "publish" / publish.THUMB_DIR / "thumb_1.jpg"
    keep.write_bytes(b"chosen")
    assert cli.main(["publish-pack", str(ws)]) == 1                       # exists, no --force
    assert keep.read_bytes() == b"chosen"


@needs_ffmpeg
def test_real_grabs_are_youtube_sized_jpegs(tmp_path):
    (tmp_path / "assets").mkdir()
    colors = ["red", "green", "blue", "yellow"]
    for c in colors:
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"testsrc2=size=1600x1000,drawbox=c={c}:t=fill:w=200:h=200", "-frames:v", "1",
                        str(tmp_path / "assets" / f"{c}.png")], check=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=stereo", "-t", "15", str(tmp_path / "assets" / "bed.wav")], check=True)
    plan = _load(tmp_path, [f"./assets/{c}.png" for c in colors], probe=real_probe)
    thumbs = tuple(publish.thumbnail_frames(plan))
    pack = publish.Pack(tmp_path / "publish" / "youtube.md", "## Upload settings\n", tmp_path, (), (), (), (),
                        plan, thumbs)
    made, failed = publish.render_thumbnails(pack)
    assert not failed and sorted(made) == [1, 2, 3]
    for path in made.values():
        info = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, check=True).stdout)["streams"][0]
        assert (info["codec_name"], info["width"], info["height"]) == ("mjpeg", 1280, 720)
        assert path.stat().st_size <= publish.THUMB_MAX_BYTES
    text = publish.with_thumbnails(pack, made, failed).text
    # shot_01 is alone from 0 to its fade at 78: clear span 6..71, middle 38
    assert "`publish/thumb_candidates/thumb_1.jpg` — 0:01 (frame 38), clip shot_01, assets/red.png" in text
