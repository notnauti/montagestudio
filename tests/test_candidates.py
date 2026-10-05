"""Scrape upgrades found on the live JWST run: query building, stems kept local, tie-breaks, the
scene-type penalty; the candidates sheet, --pick, and --vision (Claude always mocked).

HTTP is faked with test_sourcing.FakeHttp; ffmpeg is real where a preview or sheet is drawn.
"""

import json
import re
import shutil
import subprocess
import types

import pytest

from montage import candidates as cand_mod, net, scrape as scrape_mod, sources, vision
from montage.cli import main
from montage.net import NetError
from montage.scrape import scrape, search, wanted_assets
from montage.sources import Candidate, License, SearchResult, score, stem, terms, words
from montage.vision import ClaudeVision, VisionError

from test_sourcing import COMMONS, NASA, FakeHttp, _brief, _commons_page

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    d = tmp_path_factory.mktemp("cand")
    run = lambda *a: subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *a], check=True)
    run("-f", "lavfi", "-i", "testsrc2=size=320x180", "-frames:v", "1", str(d / "photo.jpg"))
    run("-f", "lavfi", "-i", "color=c=red:s=160x90", "-frames:v", "1", str(d / "thumb.png"))
    (d / "junk.jpg").write_bytes(b"<html>not an image</html>")
    return d


def _cand(title, desc="", source="commons", ident=None, pixels=None, size=None, preview=None):
    return Candidate(source, ident or title, title, desc, "image", "", License("CC0", None, True), "",
                     size=size, pixels=pixels, preview=preview)


# ── (b) stems stay local; search APIs get the words as written ─────────────────

def test_stemmer_short_es_and_s_words():
    assert stem("launches") == "launch" and stem("boxes") == "box" and stem("glasses") == "glass"
    assert stem("galaxies") == "galaxy" and stem("rockets") == "rocket" and stem("stars") == "star"
    assert [stem(w) for w in ("glass", "gas", "nucleus", "analysis", "bus")] == \
        ["glass", "gas", "nucleus", "analysis", "bus"]
    assert words("James Webb's galaxies, the GALAXY") == ["james", "webb", "galaxies"]  # one per stem
    assert terms("James Webb's galaxies") == ["jame", "webb", "galaxy"]  # local keys only


def test_search_sends_original_words_not_stems(tmp_path):
    _brief(tmp_path, [{"src": "./assets/x.jpg", "description": "James Webb telescopes"}])
    http = FakeHttp()
    scrape(tmp_path, http=http, source_names=("commons", "nasa"), dry_run=True)
    assert "gsrsearch=james+webb+telescopes" in http.json_calls[0]
    nasa = [u for u in http.json_calls if "images-api.nasa.gov" in u]
    assert "q=james+webb+telescopes" in nasa[0] and "q=james+webb&" in nasa[1]  # first try, then back-off
    assert not any(re.search(r"[=+]jame[+&]", u) for u in http.json_calls)


def test_manifest_matched_terms_use_the_brief_spelling(tmp_path):
    _brief(tmp_path, [{"src": "./assets/x.jpg", "description": "James Webb mirror"}])
    (w,), _ = wanted_assets(tmp_path)
    assert w.surface("jame") == "james" and w.surface("unknown") == "unknown"


# ── (a) queries keep the distinctive words ────────────────────────────────────

JWST = [
    {"src": "./assets/mirror.jpg", "description": "James Webb Space Telescope primary mirror fully unfolded, "
                                                  "gold hexagonal segments"},
    {"src": "./assets/sunshield.jpg", "description": "James Webb Space Telescope sunshield deployment test"},
    {"src": "./assets/launch.jpg", "description": "Ariane 5 launching the James Webb Space Telescope",
     "query": "webb ariane launch from kourou in december twenty twenty one"},
]


def test_description_query_drops_brief_wide_words_then_caps(tmp_path):
    _brief(tmp_path, JWST)
    mirror, shield, launch = wanted_assets(tmp_path)[0]
    assert mirror.shared == frozenset({"jame", "webb", "space", "telescope"})
    assert mirror.search_words() == ["primary", "mirror", "fully", "unfolded", "gold"]
    assert shield.search_words() == ["sunshield", "deployment", "test"]
    # an explicit query is sent whole: no cap, original spellings, only stopwords dropped
    assert launch.search_words() == ["webb", "ariane", "launch", "kourou", "december", "twenty", "one"]
    assert "sunshield" in shield.unique and "mirror" in mirror.unique and "webb" not in mirror.unique


def test_topic_words_kept_when_too_few_remain(tmp_path):
    _brief(tmp_path, [{"src": "./assets/a.jpg", "description": "Saturn V rocket launch"},
                      {"src": "./assets/b.jpg", "description": "Saturn V rocket launch night"}])
    a, b = wanted_assets(tmp_path)[0]
    assert a.search_words() == ["saturn", "rocket", "launch"]  # nothing distinctive left: keep the topic
    assert b.search_words() == ["saturn", "rocket", "launch", "night"]  # one own word is not a search either
    _brief(tmp_path, [{"src": "./assets/a.jpg", "description": "saturn rocket"}])
    (a,), _ = wanted_assets(tmp_path)
    assert a.shared == frozenset()  # a single asset has nothing to be told apart from


# ── (c) ties: distinctive matches, then resolution, then source order ─────────

class _Src:
    def __init__(self, name, cands):
        self.name, self._cands = name, cands

    def search(self, http, words_, kind):
        return SearchResult(list(self._cands), [], " ".join(words_))


def test_ties_break_on_distinctive_terms_then_resolution(tmp_path):
    _brief(tmp_path, JWST[:2])
    mirror = wanted_assets(tmp_path)[0][0]
    # same score: "webb telescope" (topic, 2 title terms) vs "mirror segment" (distinctive, 2 title terms)
    topic = _cand("Webb telescope", ident="t")
    distinct = _cand("Mirror segments", source="nasa", ident="d")
    out = search(mirror, None, [_Src("commons", [topic]), _Src("nasa", [distinct])], min_score=0)
    assert out.ranked[0].score == out.ranked[1].score
    assert out.ranked[0].candidate.id == "d" and out.ranked[0].distinctive == 2  # beat source order
    small = _cand("Mirror segments", ident="small", pixels=640 * 480)
    big = _cand("Mirror segments", source="nasa", ident="big", pixels=4000 * 3000)
    out = search(mirror, None, [_Src("commons", [small]), _Src("nasa", [big])], min_score=0)
    assert [s.candidate.id for s in out.ranked] == ["big", "small"]


def test_commons_reports_pixels_and_a_small_preview():
    page = _commons_page("Mirror", 1, "CC0", "https://upload.wikimedia.org/commons/m.jpg")
    info = page["imageinfo"][0]
    info.update(width=4000, height=3000,
                thumburl="https://upload.wikimedia.org/wikipedia/commons/thumb/m.jpg/1920px-m.jpg")
    (c,) = sources.Commons().search(FakeHttp([("commons", {"query": {"pages": [page]}})]), ["mirror"],
                                    "image").candidates
    assert c.pixels == 12_000_000 and c.preview.endswith("/330px-m.jpg")
    assert c.extra["urls"][0].endswith("/1920px-m.jpg")  # the download itself is unchanged
    # an original narrower than 1920 px comes back unscaled: the preview goes to the thumb host
    unscaled = "https://upload.wikimedia.org/wikipedia/commons/6/69/Wings_%281%29.jpg?utm_content=thumbnail_unscaled"
    assert sources.commons_preview(unscaled) == \
        "https://thumb.wikimedia.org/wikipedia/commons/thumb/6/69/Wings_%281%29.jpg/330px-Wings_%281%29.jpg"
    assert sources.commons_preview(None) is None


def test_nasa_and_archive_previews():
    (c,) = sources.Nasa().search(FakeHttp([("images-api.nasa.gov/search", NASA)]), ["saturn"], "image").candidates
    assert c.preview.endswith("S69-1~thumb.jpg")
    arch = {"response": {"docs": [{"identifier": "a b", "title": "t",
                                   "licenseurl": "https://creativecommons.org/publicdomain/zero/1.0/"}]}}
    (c,) = sources.Archive().search(FakeHttp([("advancedsearch", arch)]), ["t"], "image").candidates
    assert c.preview == "https://archive.org/services/img/a%20b"


# ── (d) scene-type penalty ────────────────────────────────────────────────────

def test_scene_type_penalty_marks_down_people_rooms_illustrations():
    w = {"mirror": 1.0, "wing": 1.0}
    ops = _cand("Webb Mirror Deployment", "Engineers in the Mission Operations Center watch the wing unfold")
    hardware = _cand("Webb Mirror Wing", "The mirror wing locked into place")
    plain = score(ops, w)
    s_ops, s_hw = score(ops, w, "primary mirror wing folding"), score(hardware, w, "primary mirror wing folding")
    assert plain.score == 0.75 and not plain.penalties  # no context: the old score
    assert s_ops.penalties == ("people (description)", "room (description)")
    assert s_ops.score == round(0.75 * 0.9 * 0.9, 3) and s_hw.score == 1.0
    # the asset asking for people / an illustration is not penalised for getting one
    assert not score(ops, w, "engineers in the operations center with the mirror wing").penalties
    art = _cand("Artist's illustration of the mirror wing")
    assert score(art, w, "mirror wing").penalties == ("illustration (title)",)
    assert score(art, w, "mirror wing").score == 0.75
    assert not score(art, w, "mirror wing illustration").penalties


# ── candidates sheet ──────────────────────────────────────────────────────────

def _http_with_previews(media, thumb=None):
    return FakeHttp(
        routes=[("commons.wikimedia.org", COMMONS), ("images-api.nasa.gov/search", NASA),
                ("advancedsearch", {"response": {"docs": []}})],
        files=[("S69-1~thumb", thumb or media / "thumb.png"), ("S69-1~large", media / "photo.jpg"),
               ("S69-1~orig", media / "photo.jpg"), ("/commons/thumb/a.jpg", media / "photo.jpg"),
               ("/commons/thumb/c.jpg", media / "photo.jpg")])


@needs_ffmpeg
def test_dry_run_keeps_top_candidates_as_previews_and_draws_the_sheet(tmp_path, media):
    _brief(tmp_path, [{"src": "./assets/saturn.jpg", "description": "Saturn V launch"}])
    http = _http_with_previews(media)
    (o,), notes, manifest = scrape(tmp_path, http=http, dry_run=True, candidates=True)
    assert manifest is None and not (tmp_path / "assets" / "saturn.jpg").exists()
    assert all("1920px" in u or "~thumb" in u for u in http.download_calls)  # previews only
    doc = json.loads((tmp_path / "cache" / "candidates.json").read_text())
    entry = doc["assets"]["./assets/saturn.jpg"]
    assert entry["chosen"] is None and 1 <= len(entry["candidates"]) <= 3
    first = entry["candidates"][0]
    assert first["rank"] == 1 and first["license"]["name"] and first["query"]
    assert (tmp_path / first["previewFile"]).is_file()
    nasa = next(c for c in entry["candidates"] if c["source"] == "nasa")
    assert nasa["previewFile"].endswith(".png")  # sniffed, not trusted from the URL
    assert nasa["license"]["basis"] == "source-policy"
    sheet = tmp_path / "cache" / "candidates.png"
    assert sheet.is_file() and any("candidates.png" in n for n in notes)
    dims = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                           str(sheet)], capture_output=True, text=True).stdout.strip()
    w, h = map(int, dims.split(","))
    assert w == 3 * cand_mod.TILE_W + 4 * cand_mod.GAP  # three columns
    assert h == cand_mod.TILE_H + cand_mod.LABEL_H + 2 * cand_mod.GAP + cand_mod.HEADER_H  # one asset row


@needs_ffmpeg
def test_unreadable_preview_still_draws_and_rescrape_replaces_previews(tmp_path, media):
    _brief(tmp_path, [{"src": "./assets/saturn.jpg", "description": "Saturn V launch"}])
    scrape(tmp_path, http=_http_with_previews(media, thumb=media / "junk.jpg"), dry_run=True, candidates=True)
    entry = json.loads((tmp_path / "cache" / "candidates.json").read_text())["assets"]["./assets/saturn.jpg"]
    assert next(c for c in entry["candidates"] if c["source"] == "nasa")["previewFile"] is None
    assert (tmp_path / "cache" / "candidates.png").is_file()
    stale = tmp_path / "cache" / "candidates" / "saturn.jpg--3.gif"
    stale.write_bytes(b"GIF8")
    scrape(tmp_path, http=_http_with_previews(media), dry_run=True, candidates=True)
    assert not stale.exists()


@needs_ffmpeg
def test_real_scrape_marks_the_chosen_candidate(tmp_path, media):
    _brief(tmp_path, [{"src": "./assets/saturn.jpg", "description": "Saturn V launch"}])
    (o,), _, _ = scrape(tmp_path, http=_http_with_previews(media), candidates=True)
    assert o.ok
    entry = json.loads((tmp_path / "cache" / "candidates.json").read_text())["assets"]["./assets/saturn.jpg"]
    assert entry["chosen"] == 1


# ── --pick ────────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_pick_downloads_candidate_n_and_updates_the_manifest(tmp_path, media):
    _brief(tmp_path, [{"src": "./assets/saturn.jpg", "description": "Saturn V launch"}])
    scrape(tmp_path, http=_http_with_previews(media), candidates=True)
    first = json.loads((tmp_path / "manifest.json").read_text())["assets"]["./assets/saturn.jpg"]
    doc = json.loads((tmp_path / "cache" / "candidates.json").read_text())
    second = doc["assets"]["./assets/saturn.jpg"]["candidates"][1]
    out, notes = cand_mod.pick(tmp_path, "saturn", 2, http=_http_with_previews(media))
    assert out.ok and out.wanted.path == (tmp_path / "assets" / "saturn.jpg").resolve()
    e = json.loads((tmp_path / "manifest.json").read_text())["assets"]["./assets/saturn.jpg"]
    assert (e["source"], e["sourceId"]) == (second["source"], second["id"]) != (first["source"], first["sourceId"])
    assert e["pickedRank"] == 2 and e["license"] == second["license"]["name"]
    assert e["sha256"] == scrape_mod._sha256(tmp_path / "assets" / "saturn.jpg")
    assert e["ranking"].startswith("lexical") and e["attribution"] == second["attribution"]
    assert json.loads((tmp_path / "cache" / "candidates.json").read_text())["assets"]["./assets/saturn.jpg"][
        "chosen"] == 2
    assert not list((tmp_path / "cache" / "pick").glob("*"))  # staging moved into place


@needs_ffmpeg
def test_failed_pick_leaves_the_asset_alone(tmp_path, media):
    _brief(tmp_path, [{"src": "./assets/saturn.jpg", "description": "Saturn V launch"}])
    scrape(tmp_path, http=_http_with_previews(media), dry_run=True, candidates=True)
    asset = tmp_path / "assets" / "saturn.jpg"
    asset.write_bytes(b"the user's file")
    broken = FakeHttp(routes=[], files=[(lambda u: True, NetError("could not reach host"))])
    out, _ = cand_mod.pick(tmp_path, "./assets/saturn.jpg", 1, http=broken)
    assert not out.ok and out.errors and asset.read_bytes() == b"the user's file"
    assert not (tmp_path / "manifest.json").exists()


def _seed(tmp_path, cands, src="./assets/saturn.jpg"):
    (tmp_path / "assets").mkdir(exist_ok=True)
    (tmp_path / "cache").mkdir(exist_ok=True)
    (tmp_path / "cache" / "candidates.json").write_text(json.dumps(
        {"version": 1, "assets": {src: {"kind": "image", "description": "d", "candidates": cands}}}))


_GOOD = {"rank": 1, "source": "commons", "id": "File:x.jpg", "title": "x", "kind": "image",
         "license": {"name": "CC BY-SA 4.0", "url": None, "basis": "item-field"}, "attribution": "a",
         "extra": {"urls": ["https://upload.wikimedia.org/x.jpg"]}, "score": 0.5}


@pytest.mark.parametrize("key,n,cands,expected", [
    ("nope", 1, [_GOOD], "no candidates for 'nope'"),
    ("saturn", 4, [_GOOD], "has candidates 1..1"),
    ("saturn", 1, [{**_GOOD, "license": {"name": "CC BY-NC 4.0", "url": None, "basis": "item-field"}}],
     "not usable"),
    ("saturn", 1, [{**_GOOD, "license": {"name": "anything", "url": None, "basis": "source-policy"}}],
     "not usable"),  # only NASA's guideline counts as a source policy
    ("saturn", 1, [{**_GOOD, "kind": "video"}], "candidate is a video"),
])
def test_pick_refuses(tmp_path, key, n, cands, expected):
    _seed(tmp_path, cands)
    http = FakeHttp()
    with pytest.raises(cand_mod.PickError, match=expected):
        cand_mod.pick(tmp_path, key, n, http=http)
    assert not http.download_calls


def test_pick_specs_and_lookup():
    assert cand_mod.parse_pick("jwst-mirror-wing=2") == ("jwst-mirror-wing", 2)
    assert cand_mod.parse_pick("./assets/a=b.jpg=3") == ("./assets/a=b.jpg", 3)
    for bad in ("x", "x=", "=2", "x=0", "x=two"):
        with pytest.raises(cand_mod.PickError, match="expected <asset>=<n>"):
            cand_mod.parse_pick(bad)
    doc = {"assets": {"./assets/a.jpg": {}, "./assets/sub/a.png": {}}}
    assert cand_mod.find_asset(doc, "assets/a.jpg") == "./assets/a.jpg"
    assert cand_mod.find_asset(doc, "a.png") == "./assets/sub/a.png"
    with pytest.raises(cand_mod.PickError, match="matches 2 assets"):
        cand_mod.find_asset(doc, "a")


@needs_ffmpeg
def test_cli_pick(tmp_path, media, monkeypatch, capsys):
    _brief(tmp_path, [{"src": "./assets/saturn.jpg", "description": "Saturn V launch"}])
    monkeypatch.setattr(net, "Http", lambda: _http_with_previews(media))
    assert main(["scrape", str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "candidate previews only" in out and "candidates.png" in out
    assert main(["scrape", str(tmp_path), "--pick", "saturn=2"]) == 0
    assert "pick saturn=2: saved ./assets/saturn.jpg" in capsys.readouterr().out
    assert main(["scrape", str(tmp_path), "--pick", "saturn=9"]) == 1
    assert main(["scrape", str(tmp_path), "--pick", "saturn"]) == 1
    assert main(["scrape", str(tmp_path), "--pick", "saturn=1", "--dry-run"]) == 1
    monkeypatch.setattr(net, "Http", lambda: FakeHttp(files=[(lambda u: True, NetError("down"))]))
    assert main(["scrape", str(tmp_path), "--pick", "saturn=1"]) == 2
    assert "NOT replaced" in capsys.readouterr().err


def test_cli_no_candidates_skips_the_sheet(tmp_path, monkeypatch):
    _brief(tmp_path, [{"src": "./assets/x.jpg", "description": "Saturn V launch"}])
    http = FakeHttp([("commons", COMMONS)])
    monkeypatch.setattr(net, "Http", lambda: http)
    assert main(["scrape", str(tmp_path), "--dry-run", "--no-candidates", "--sources", "commons"]) == 0
    assert not http.download_calls and not (tmp_path / "cache").exists()


# ── --vision (Claude mocked) ──────────────────────────────────────────────────

class FakeScorer:
    def __init__(self, scores=None, error=None):
        self.scores, self.error, self.calls = scores or {}, error, []

    def __call__(self, wanted, items):
        self.calls.append((wanted.src, [(r, c.id) for r, c, _ in items]))
        if self.error:
            raise VisionError(self.error)
        return {r: self.scores[r] for r, _, _ in items if r in self.scores}


def _outcome(tmp_path, n=3, previews=True):
    from montage.scrape import Outcome, Wanted
    out = Outcome(Wanted("./assets/a.jpg", tmp_path / "a.jpg", "image", "mirror wing", None, ""))
    out.ranked = [sources.Scored(_cand(f"c{i}"), 1 - i / 10, ()) for i in range(1, n + 2)]
    for i, s in enumerate(out.ranked[:3], 1):
        p = tmp_path / f"p{i}.jpg"
        p.write_bytes(b"\xff\xd8jpeg")
        out.previews[(s.candidate.source, s.candidate.id)] = p if previews or i != 1 else None
    return out


def test_apply_vision_reranks_the_top_candidates(tmp_path):
    out = _outcome(tmp_path, previews=False)  # #1 has no preview
    scorer = FakeScorer({2: (3, "people"), 3: (9, "the wing")})
    assert cand_mod.apply_vision(out, scorer)
    assert scorer.calls == [("./assets/a.jpg", [(2, "c2"), (3, "c3")])]  # only previewed ones are sent
    assert [s.candidate.id for s in out.ranked] == ["c3", "c2", "c1", "c4"]  # unscored after scored; tail kept
    assert out.vision[("commons", "c3")] == (9, "the wing")


def test_apply_vision_failure_keeps_lexical_order(tmp_path):
    out = _outcome(tmp_path)
    assert not cand_mod.apply_vision(out, FakeScorer(error="Claude API error 400: credit balance too low"))
    assert [s.candidate.id for s in out.ranked] == ["c1", "c2", "c3", "c4"]
    assert "credit balance" in out.vision_error and not out.vision


@needs_ffmpeg
def test_scrape_with_vision_downloads_the_vision_pick(tmp_path, media):
    _brief(tmp_path, [{"src": "./assets/saturn.jpg", "description": "Saturn V launch"},
                      {"src": "./assets/other.jpg", "description": "Saturn V launch kennedy"}])
    scorer = FakeScorer({1: (2, "a crowd"), 2: (8, "the rocket"), 3: (5, "")})
    outcomes, _, _ = scrape(tmp_path, http=_http_with_previews(media), candidates=True, vision=scorer)
    o = outcomes[0]
    assert o.ok and o.chosen.candidate.id == o.ranked[0].candidate.id
    assert o.vision[(o.chosen.candidate.source, o.chosen.candidate.id)][0] == 8
    entry = json.loads((tmp_path / "cache" / "candidates.json").read_text())["assets"]["./assets/saturn.jpg"]
    assert entry["chosen"] == 1 and entry["candidates"][0]["vision"]["score"] == 8
    # one failure switches vision off for the rest of the run
    failing = FakeScorer(error="no credit")
    for f in (tmp_path / "assets").glob("*.jpg"):
        f.unlink()
    outcomes, _, _ = scrape(tmp_path, http=_http_with_previews(media), candidates=True, vision=failing)
    assert len(failing.calls) == 1 and outcomes[0].vision_error == "no credit" and outcomes[1].vision_error is None


def test_cli_vision_without_credentials_is_exit_2(tmp_path, monkeypatch, capsys):
    _brief(tmp_path, [{"src": "./assets/x.jpg", "description": "saturn"}])
    monkeypatch.setattr(vision, "unavailable_reason", lambda: "no Claude credentials: set ANTHROPIC_API_KEY")
    http = FakeHttp()
    monkeypatch.setattr(net, "Http", lambda: http)
    assert main(["scrape", str(tmp_path), "--vision"]) == 2
    assert "--vision needs Claude: no Claude credentials" in capsys.readouterr().err
    assert not http.json_calls  # refused before any search


@needs_ffmpeg
def test_cli_vision_failure_is_exit_2_but_assets_still_sourced(tmp_path, media, monkeypatch, capsys):
    _brief(tmp_path, [{"src": "./assets/saturn.jpg", "description": "Saturn V launch"}])
    monkeypatch.setattr(vision, "unavailable_reason", lambda: None)
    monkeypatch.setattr(vision, "ClaudeVision", lambda: FakeScorer(error="Claude API error 400: credit"))
    monkeypatch.setattr(net, "Http", lambda: _http_with_previews(media))
    assert main(["scrape", str(tmp_path), "--vision"]) == 2
    io = capsys.readouterr()
    assert "vision failed: Claude API error 400" in io.out and "-> saved ./assets/saturn.jpg" in io.out
    assert "assets were chosen lexically" in io.err


def test_unavailable_reason(monkeypatch):
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic",
                        lambda: types.SimpleNamespace(api_key=None, auth_token=None, credentials=None))
    assert "no Claude credentials" in vision.unavailable_reason()
    monkeypatch.setattr(anthropic, "Anthropic",
                        lambda: types.SimpleNamespace(api_key="k", auth_token=None, credentials=None))
    assert vision.unavailable_reason() is None


class _FakeMessages:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.kwargs = response, exc, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.exc:
            raise self.exc
        return self.response


def _client(messages):
    return types.SimpleNamespace(beta=types.SimpleNamespace(messages=messages))


def _response(text=None, stop="end_turn"):
    content = [types.SimpleNamespace(type="text", text=text)] if text is not None else []
    return types.SimpleNamespace(stop_reason=stop, content=content)


def test_claude_vision_request_and_parse(tmp_path):
    from montage.scrape import Wanted
    p1, p2 = tmp_path / "a.png", tmp_path / "b.jpg"
    p1.write_bytes(b"\x89PNG....")
    p2.write_bytes(b"\xff\xd8....")
    w = Wanted("./assets/a.jpg", tmp_path / "a.jpg", "image", "mirror wing unfolding", "webb wing", "")
    msgs = _FakeMessages(_response(json.dumps({"scores": [
        {"candidate": 1, "score": 14, "reason": "the wing"}, {"candidate": 2, "score": 2, "reason": "a room"},
        {"candidate": 7, "score": 5, "reason": "not asked"}]})))
    scores = ClaudeVision(client=_client(msgs))(w, [(1, _cand("A"), p1), (2, _cand("B"), p2)])
    assert scores == {1: (10, "the wing"), 2: (2, "a room")}  # clamped; unknown ranks dropped
    k = msgs.kwargs
    assert k["model"] == vision.VISION_MODEL and k["fallbacks"] == "default"
    assert k["output_config"]["format"]["type"] == "json_schema"
    blocks = k["messages"][0]["content"]
    assert "mirror wing unfolding" in blocks[0]["text"] and "webb wing" in blocks[0]["text"]
    images = [b for b in blocks if b["type"] == "image"]
    assert [i["source"]["media_type"] for i in images] == ["image/png", "image/jpeg"]


@pytest.mark.parametrize("response,match", [
    (_response(stop="refusal"), "declined"),
    (_response("not json"), "unparseable"),
    (_response(json.dumps({"scores": []})), "scored none"),
    (_response(stop="max_tokens"), "max_tokens"),
])
def test_claude_vision_failures(tmp_path, response, match):
    from montage.scrape import Wanted
    p = tmp_path / "a.jpg"
    p.write_bytes(b"\xff\xd8")
    w = Wanted("./assets/a.jpg", p, "image", "d", None, "")
    with pytest.raises(VisionError, match=match):
        ClaudeVision(client=_client(_FakeMessages(response)))(w, [(1, _cand("A"), p)])


def test_claude_vision_api_errors_become_vision_errors(tmp_path):
    import anthropic
    import httpx2
    from montage.scrape import Wanted
    p = tmp_path / "a.jpg"
    p.write_bytes(b"\xff\xd8")
    w = Wanted("./assets/a.jpg", p, "image", "d", None, "")
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    status = anthropic.BadRequestError("credit balance is too low", response=httpx2.Response(400, request=req),
                                       body=None)
    with pytest.raises(VisionError, match="Claude API error 400"):
        ClaudeVision(client=_client(_FakeMessages(exc=status)))(w, [(1, _cand("A"), p)])
    with pytest.raises(VisionError, match="could not reach"):
        ClaudeVision(client=_client(_FakeMessages(exc=anthropic.APIConnectionError(request=req))))(
            w, [(1, _cand("A"), p)])
