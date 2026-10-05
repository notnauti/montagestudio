"""`montage publish-pack`: publish/youtube.md from brief, script, plan, approval, manifest and render.

No ffmpeg, no model, no network: media are placeholder bytes and the probe is faked.
"""

import json
import os
import time

import pytest

from montage import approval as approval_mod, cli, publish
from montage.plan import load_plan
from montage.probe import MediaInfo
from montage.script import Beat, Script


def fake_probe(path):
    return {".png": MediaInfo(True, False, None), ".jpg": MediaInfo(True, False, None),
            ".wav": MediaInfo(False, True, 60.0), ".mp4": MediaInfo(True, True, 48.0)}[path.suffix]


FPS = 30
BEAT = 12 * FPS
LINES = ["Christmas morning, 2021. A rocket stood ready.",
         "Its mirror spanned six and a half meters, eighteen hexagons of beryllium coated in gold.",
         "So engineers taught a telescope to fold.",
         "Every one of them worked."]
ASSETS = ["./assets/launch.jpg", "./assets/mirror.jpg", "./assets/fold.png", "./assets/cliffs.jpg"]

ENTRIES = {
    "./assets/launch.jpg": {"source": "commons", "title": "Ariane 5 prelaunch", "pageUrl": "https://commons.example/launch",
                            "license": "Public domain", "licenseUrl": None, "licenseBasis": "item-field",
                            "attribution": "Bill Ingalls, via Wikimedia Commons"},
    "./assets/mirror.jpg": {"source": "commons", "title": "Golden mirror", "pageUrl": "https://commons.example/mirror",
                            "license": "CC BY-SA 4.0", "licenseUrl": "https://creativecommons.org/licenses/by-sa/4.0",
                            "licenseBasis": "item-field", "attribution": "Jane Doe"},
    "./assets/fold.png": {"source": "nasa", "title": "Partially stowed", "pageUrl": "https://images.nasa.gov/details/x",
                          "license": "NASA media (not copyrighted, per NASA media guidelines)",
                          "licenseUrl": "https://www.nasa.gov/nasa-brand-center/images-and-media/",
                          "licenseBasis": "source-policy", "attribution": "NASA, GSFC"},
    "./assets/cliffs.jpg": {"source": "commons", "title": "Cosmic Cliffs", "pageUrl": "https://commons.example/cliffs",
                            "license": "CC0", "licenseUrl": "https://creativecommons.org/publicdomain/zero/1.0/",
                            "licenseBasis": "item-field", "attribution": "NASA, ESA, CSA, STScI"},
}


def _plan(beats=4, beat_frames=BEAT):
    video, voice = [], []
    for i in range(beats):
        video.append({"id": f"shot_{i + 1:02d}", "src": ASSETS[i % len(ASSETS)], "startFrame": i * beat_frames,
                      "durationFrames": beat_frames})
        voice.append({"id": f"vo_{i + 1:02d}", "src": f"./assets/narration/beat_{i + 1:02d}_{'%010x' % i}.wav",
                      "startFrame": i * beat_frames + 12, "durationFrames": beat_frames - 30})
    return {"meta": {"title": "How Webb Unfolded", "resolution": [1920, 1080], "fps": FPS,
                     "durationFrames": beats * beat_frames},
            "tracks": [{"id": "visuals", "type": "video", "clips": video},
                       {"id": "narration", "type": "audio", "clips": voice}]}


def _write(path, data):
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _approve(ws):
    plan_path = ws / "scene_plan.json"
    plan = load_plan(plan_path, probe=fake_probe)
    _write(ws / "approval.json", {"version": 1, "approvedAt": "2026-10-04T05:46:13+00:00",
                                  **approval_mod.fingerprint(plan, plan_path)})


def _render(ws, name="how-webb-unfolded.mp4", age=0.0):
    (ws / "renders").mkdir(exist_ok=True)
    p = ws / "renders" / name
    p.write_bytes(b"video")
    t = time.time() + 5 - age
    os.utime(p, (t, t))
    return p


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "default_probe", fake_probe)
    (tmp_path / "assets" / "narration").mkdir(parents=True)
    for src in ASSETS:
        (tmp_path / src).write_bytes(src.encode())
    plan = _plan()
    for c in plan["tracks"][1]["clips"]:
        (tmp_path / c["src"]).write_bytes(b"RIFF")
    _write(tmp_path / "brief.json", {
        "title": "How Webb Unfolded",
        "brief": "A short documentary for an educational audience about the James Webb Space Telescope: "
                 "the Christmas 2021 launch, the origami fold and the first images.",
        "targetSeconds": 48, "recipe": "documentary",
        "assets": [{"src": a, "description": f"James Webb Space Telescope {d}"}
                   for a, d in zip(ASSETS, ["launch", "gold mirror", "folded", "cosmic cliffs"])]})
    _write(tmp_path / "script.json", {"title": "How Webb Unfolded", "writer": "hand", "beats": [
        {"narration": t, "asset": a, "motion": "none"} for t, a in zip(LINES, ASSETS)]})
    _write(tmp_path / "scene_plan.json", plan)
    manifest = {src: dict(e, sha256=approval_mod.sha256(tmp_path / src)) for src, e in ENTRIES.items()}
    _write(tmp_path / "manifest.json", {"version": 1, "assets": manifest})
    _approve(tmp_path)
    _render(tmp_path)
    return tmp_path


def _run(ws, *extra):
    return cli.main(["publish-pack", str(ws), *extra])


def test_pack_has_title_chapters_credits_tags_and_disclosure(ws, capsys):
    (ws / "youtube.md").write_text("my own notes", encoding="utf-8")
    assert _run(ws) == 0
    out = capsys.readouterr().out
    assert "4 credited asset(s); 4 chapter(s)" in out
    text = (ws / "publish" / "youtube.md").read_text(encoding="utf-8")
    assert (ws / "youtube.md").read_text(encoding="utf-8") == "my own notes"  # never touched
    assert text.startswith("# YouTube upload — How Webb Unfolded")
    assert "## Title\nHow Webb Unfolded\n" in text
    assert "`renders/how-webb-unfolded.mp4` (48.0 s)" in text
    assert "Storyboard approved: 2026-10-04T05:46:13+00:00" in text
    assert "about the James Webb Space Telescope: the Christmas 2021 launch" in text  # the premise
    assert ("Chapters:\n0:00 Christmas morning, 2021\n0:12 Its mirror spanned six and a half meters\n"
            "0:24 So engineers taught a telescope to fold\n0:36 Every one of them worked\n") in text
    # every asset, exactly as the manifest records it
    for src, e in ENTRIES.items():
        assert f"- {e['title']}\n  Author: {e['attribution']}\n  Source: {e['pageUrl']}\n  License: {e['license']}" in text
        if e["licenseUrl"]:
            assert f"{e['license']} — {e['licenseUrl']}" in text
    assert "License: Public domain (no license URL recorded)" in text
    assert "NASA does not endorse this video." in text
    assert "Disclosure: the narration is synthetic speech generated with Piper TTS (voice en_US-lessac-medium)" in text
    assert "drafted by an AI model" not in text  # a hand-written script makes no such claim
    assert "- Altered or synthetic content: yes" in text
    tags = text.split("## Tags\n")[1].split("\n")[0].split(", ")
    assert tags[:3] == ["How Webb Unfolded", "James Webb Space Telescope", "documentary"]
    assert "Christmas" in tags and "origami" in tags
    assert not {"short", "educational", "audience"} & {t.lower() for t in tags}


def test_never_overwrites_without_force(ws, capsys):
    assert _run(ws) == 0
    pack = ws / "publish" / "youtube.md"
    pack.write_text("edited by hand", encoding="utf-8")
    assert _run(ws) == 1
    assert "--force" in capsys.readouterr().err
    assert pack.read_text(encoding="utf-8") == "edited by hand"
    assert _run(ws, "--force") == 0
    assert pack.read_text(encoding="utf-8").startswith("# YouTube upload")


def test_refuses_an_unapproved_or_changed_plan(ws, capsys):
    (ws / "approval.json").unlink()
    assert _run(ws) == 1
    assert "not approved: no approval.json" in capsys.readouterr().err
    _approve(ws)
    plan = json.loads((ws / "scene_plan.json").read_text())
    plan["tracks"][0]["clips"][0]["fit"] = "contain"
    _write(ws / "scene_plan.json", plan)
    _render(ws)  # a newer render: only the approval is wrong
    assert _run(ws) == 1
    assert "scene_plan.json changed since it was approved" in capsys.readouterr().err
    assert not (ws / "publish").exists()


def _edit_manifest(ws, src, **changes):
    doc = json.loads((ws / "manifest.json").read_text())
    if changes.pop("drop", False):
        del doc["assets"][src]
    else:
        doc["assets"][src].update(changes)
    _write(ws / "manifest.json", doc)


@pytest.mark.parametrize("src, changes, reason", [
    ("./assets/mirror.jpg", {"drop": True}, "no manifest.json entry"),
    ("./assets/mirror.jpg", {"attribution": ""}, "lacks author (attribution)"),
    ("./assets/launch.jpg", {"pageUrl": None}, "lacks source URL (pageUrl)"),
    ("./assets/cliffs.jpg", {"license": None, "licenseUrl": None}, "lacks license"),
    ("./assets/mirror.jpg", {"license": "CC BY-NC 4.0", "licenseUrl": "https://creativecommons.org/licenses/by-nc/4.0"},
     "is NonCommercial"),
    ("./assets/mirror.jpg", {"license": "CC BY-ND 4.0", "licenseUrl": None}, "NoDerivatives"),
    ("./assets/mirror.jpg", {"license": "All rights reserved", "licenseUrl": None}, "is not allowed"),
    ("./assets/mirror.jpg", {"licenseUrl": None}, "requires a link to the license"),
    ("./assets/launch.jpg", {"licenseBasis": "source-policy", "license": "Getty policy"}, "only accepted for NASA"),
    ("./assets/cliffs.jpg", {"sha256": "0" * 64}, "sha256 differs"),
])
def test_refuses_any_asset_without_usable_attribution(ws, capsys, src, changes, reason):
    _edit_manifest(ws, src, **changes)
    assert _run(ws) == 1
    err = capsys.readouterr().err
    assert "refusing to write the publish pack" in err and reason in err and src[2:] in err
    assert not (ws / "publish" / "youtube.md").exists()


def test_refuses_without_manifest_listing_every_asset(ws, capsys):
    (ws / "manifest.json").unlink()
    assert _run(ws) == 1
    err = capsys.readouterr().err
    assert err.count("no manifest.json entry") == 4 and "narration" not in err  # montage's own wavs need none


def test_refuses_without_a_render_or_with_a_stale_one(ws, capsys):
    for f in (ws / "renders").iterdir():
        f.unlink()
    assert _run(ws) == 1
    assert "no render in" in capsys.readouterr().err
    _render(ws, age=3600)  # older than scene_plan.json
    assert _run(ws) == 1
    assert "older than scene_plan.json" in capsys.readouterr().err


def test_latest_render_is_the_newest_file(ws):
    _render(ws, "old.mp4", age=10)
    newest = _render(ws, "new-720p.mp4", age=-10)
    assert publish.latest_render(ws) == newest


def test_disclosure_names_a_model_writer_from_the_script_gate(ws):
    _write(ws / "script_approval.json", {"writer": "claude-opus-5", "draftSha256": "a", "approvedSha256": "a"})
    pack = publish.build(ws, probe=fake_probe)
    assert "drafted by an AI model (claude-opus-5) and reviewed by a person" in pack.text


def test_missing_workspace_files(tmp_path, capsys):
    assert _run(tmp_path) == 1
    assert "no brief.json" in capsys.readouterr().err


# ── chapters ─────────────────────────────────────────────────────────────────

def _script(n):
    return Script("T", tuple(Beat(f"Line {i}, with more.", ASSETS[0], "none") for i in range(n)), "hand")


def test_short_beats_merge_into_chapters_of_ten_seconds():
    raw = _plan(beats=6, beat_frames=6 * FPS)  # 6 s beats, 36 s video
    chs, why = publish.chapters(raw, _script(6), FPS, 6 * 6 * FPS)
    assert why is None
    assert [(c.seconds, c.title) for c in chs] == [(0, "Line 0, with more"), (12, "Line 2, with more"),
                                                   (24, "Line 4, with more")]


def test_too_few_chapters_are_left_out_with_a_note():
    raw = _plan(beats=2, beat_frames=BEAT)
    chs, why = publish.chapters(raw, _script(2), FPS, 2 * BEAT)
    assert chs == [] and "at least 3 chapters" in why


def test_chapters_need_one_narration_clip_per_beat():
    raw = _plan(beats=4)
    raw["tracks"][1]["clips"].pop()
    raw["tracks"][0]["clips"].pop()
    chs, why = publish.chapters(raw, _script(4), FPS, 4 * BEAT)
    assert chs == [] and "one narration clip per beat" in why


def test_chapter_titles():
    assert publish.chapter_title("Christmas morning, 2021. On the coast...") == "Christmas morning, 2021"
    assert publish.chapter_title("Its mirror spanned six and a half meters: eighteen hexagons of beryllium.") == \
        "Its mirror spanned six and a half meters"
    long = "one two three four five six seven eight nine ten eleven"
    assert publish.chapter_title(long) == "one two three four five six seven eight …"
    assert publish.stamp(75) == "1:15" and publish.stamp(3725) == "1:02:05"
