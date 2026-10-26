"""`montage view` page JS in a real headless browser: frames load, clicks select, edits are followed.

Skips without Chrome/Edge (set MONTAGE_TEST_CHROME to point at one) or ffmpeg. Frames go through the
real renderer, so this module takes ~30 s.
"""

import json
import threading

import pytest

from cdp import Chrome, find_chrome
from montage import view as view_mod
from test_view import ws  # noqa: F401 -- three-colour workspace fixture, module-scoped

CHROME = find_chrome()
pytestmark = pytest.mark.skipif(CHROME is None, reason="needs Chrome or Edge")

LOADED = "[...document.querySelectorAll('#frames img')]"
ALL_LOADED = f"{LOADED}.length > 0 && {LOADED}.every(i => i.complete && i.naturalWidth > 0)"


@pytest.fixture(scope="module")
def browser():
    b = Chrome(CHROME)
    yield b
    b.close()


@pytest.fixture
def page(ws, browser):  # noqa: F811
    srv = view_mod.make_server(ws)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    browser.exceptions.clear()
    browser.goto(f"http://127.0.0.1:{srv.server_address[1]}/")
    browser.wait("document.querySelectorAll('.clip').length > 0")
    yield browser, srv
    browser.goto("about:blank")                 # stop the page polling before the server goes
    srv.shutdown()
    srv.server_close()
    assert browser.exceptions == []


def _text(b, sel):
    return b.js(f"document.querySelector({json.dumps(sel)}).textContent")


def test_overview_frames_actually_load(page):
    # the 2026-10-26 bug: the frame queue ran before the image was attached and no frame ever loaded
    b, _ = page
    assert _text(b, "#title") == "T"
    assert b.js("[...document.querySelectorAll('.clip.video')].map(c => c.textContent)") == \
        ["shot_01", "shot_02", "shot_03"]
    assert b.js("document.querySelectorAll('.clip.audio').length") == 1
    assert b.js("document.querySelectorAll('.fade').length") == 2           # two crossfades
    gates = b.js("[...document.querySelectorAll('#gates .badge')].map(g => g.textContent)")
    assert "storyboard not approved" in gates and "no preview yet" in gates
    assert b.js(f"{LOADED}.length") == 3
    b.wait(ALL_LOADED, timeout=90)
    assert b.js(f"{LOADED}.every(i => i.src.startsWith('blob:'))")
    assert "rendering" not in _text(b, "#frames")


def test_clicking_a_clip_shows_its_detail_and_frames(page):
    b, _ = page
    b.js("document.querySelector('.clip[data-id=shot_02]').click()")
    assert b.js("document.querySelector('.clip.selected').dataset.id") == "shot_02"
    assert "shot_02 (visuals, video)" in _text(b, "#detail")
    assert "crossfade 12 frames" in _text(b, "#detail")
    caps = b.js("[...document.querySelectorAll('#frames figcaption')].map(c => c.textContent)")
    assert [c.split(" ·")[0] for c in caps] == ["first", "middle", "last"]
    b.wait(ALL_LOADED, timeout=90)


def test_a_real_click_on_a_lane_shows_that_frame(page):
    b, _ = page
    # clips cover the whole video lane; let the click fall through them to the lane itself
    b.js("document.querySelectorAll('.clip').forEach(c => c.style.pointerEvents = 'none')")
    b.js("document.querySelector('.lane').scrollIntoView({block: 'center'})")
    x, y, w, h = b.js("(() => { const r = document.querySelector('.lane').getBoundingClientRect();"
                      " return [r.left, r.top, r.width, r.height]; })()")
    b.click_at(x + w / 2, y + h / 2)
    b.wait("document.querySelector('.playhead') !== null")
    duration = b.js("snap.durationFrames")
    shown = int(_text(b, "#detail").split()[1])
    assert abs(shown - duration / 2) <= 2
    assert b.js(f"{LOADED}.length") == 1
    b.wait(ALL_LOADED, timeout=60)


def test_edits_on_disk_are_followed_and_plan_text_is_never_markup(page, ws):  # noqa: F811
    b, _ = page
    plan_path = ws / "scene_plan.json"
    original = plan_path.read_text(encoding="utf-8")
    try:
        doc = json.loads(original)
        doc["meta"]["title"] = '<img src=x onerror="window.pwned=1">Edited'
        plan_path.write_text(json.dumps(doc), encoding="utf-8")
        b.wait("document.querySelector('#title').textContent.endsWith('Edited')", timeout=10)
        assert _text(b, "#title") == doc["meta"]["title"]
        assert b.js("document.querySelectorAll('header img').length") == 0
        assert b.js("window.pwned === undefined")

        plan_path.write_text("{broken", encoding="utf-8")
        b.wait("!document.querySelector('#errors').hidden", timeout=10)
        assert "does not validate" in _text(b, "#errors")
    finally:
        plan_path.write_text(original, encoding="utf-8")
    b.wait("document.querySelector('#errors').hidden", timeout=10)
    assert _text(b, "#title") == "T"


def test_rerender_button_posts_same_origin(page, monkeypatch):
    # the page's own POST must pass the server's Origin check that refuses cross-site ones
    b, srv = page
    started = []
    monkeypatch.setattr(srv.view, "start_preview", lambda: started.append(1) or True)
    b.js("document.querySelector('#rerender').click()")
    for _ in range(50):
        if started:
            break
        b.js("new Promise(r => setTimeout(r, 100))")
    assert started == [1]
