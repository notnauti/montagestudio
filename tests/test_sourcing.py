"""`montage scrape`: licenses, lexical ranking, the three sources, download + manifest.

Every HTTP call is faked (FakeHttp, or a fake urlopen for net.Http itself). The response
fixtures copy the shapes the live APIs returned on 2026-10-02. One live test runs only with
MONTAGE_LIVE_NET=1.
"""

import io
import json
import os
import shutil
import subprocess
import urllib.error

import pytest

from montage import net, scrape as scrape_mod, sources
from montage.brief import BriefError, parse_brief
from montage.cli import main
from montage.net import Http, NetError, TooLarge
from montage.probe import MediaInfo
from montage.scrape import scrape, wanted_assets
from montage.sources import Candidate, License, parse_license, score, terms

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")


# ── fakes ─────────────────────────────────────────────────────────────────────

class FakeHttp:
    """routes: [(match, payload)] where match is a substring of the URL or a predicate; payload is
    JSON, or an exception to raise. files: same for downloads, payload a local path or exception."""

    def __init__(self, routes=(), files=()):
        self.routes, self.files = list(routes), list(files)
        self.json_calls, self.download_calls = [], []

    @staticmethod
    def _hit(match, url):
        return match(url) if callable(match) else match in url

    def json(self, url):
        self.json_calls.append(url)
        for match, payload in self.routes:
            if self._hit(match, url):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        return {}  # an empty response: no results

    def download(self, url, dest, max_bytes):
        self.download_calls.append(url)
        for match, payload in self.files:
            if self._hit(match, url):
                if isinstance(payload, Exception):
                    raise payload
                data = payload.read_bytes()
                if len(data) > max_bytes:
                    raise TooLarge("over the cap")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                return len(data)
        raise NetError(f"no file for {url}")


def _commons_page(title, index, license_name, url, artist="NASA", license_url=None, mime="image/jpeg",
                  size=900_000, thumb=True, desc=""):
    meta = {"LicenseShortName": {"value": license_name}, "Artist": {"value": artist},
            "ObjectName": {"value": title}, "ImageDescription": {"value": desc}}
    if license_url:
        meta["LicenseUrl"] = {"value": license_url}
    if license_name is None:
        del meta["LicenseShortName"]
    info = {"size": size, "url": url, "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{index}",
            "mime": mime, "extmetadata": meta}
    if thumb:
        info["thumburl"] = url.replace("/commons/", "/commons/thumb/") + "/1920px.jpg"
    return {"title": f"File:{title}.jpg", "index": index, "imageinfo": [info]}


COMMONS = {"query": {"pages": [
    _commons_page("Saturn V launch Kennedy", 2, "Public domain", "https://upload.wikimedia.org/commons/a.jpg"),
    _commons_page("Saturn V launch photo", 1, "CC BY-NC 2.0", "https://upload.wikimedia.org/commons/b.jpg"),
    _commons_page("Moon rock", 3, "CC BY-SA 4.0", "https://upload.wikimedia.org/commons/c.jpg",
                  artist='<a href="//commons.wikimedia.org/wiki/User:X">Jane &amp; Co</a>',
                  license_url="https://creativecommons.org/licenses/by-sa/4.0"),
    _commons_page("Untagged", 4, None, "https://upload.wikimedia.org/commons/d.jpg"),
]}}

NASA = {"collection": {"items": [
    {"href": "https://images-assets.nasa.gov/image/S69-1/collection.json",
     "data": [{"nasa_id": "S69-1", "title": "Apollo 11 Saturn V launch", "media_type": "image", "center": "KSC",
               "description": "Liftoff of Apollo 11", "keywords": ["Saturn V", "Launch"]}],
     "links": [{"href": "https://images-assets.nasa.gov/image/S69-1/S69-1~thumb.jpg", "rel": "preview",
                "render": "image", "size": 9000},
               {"href": "https://images-assets.nasa.gov/image/S69-1/S69-1~orig.jpg", "rel": "canonical",
                "render": "image"},
               {"href": "https://images-assets.nasa.gov/image/S69-1/S69-1~large.jpg", "rel": "alternate",
                "render": "image", "size": 500_000}]},
    {"href": "https://images-assets.nasa.gov/image/X/collection.json",
     "data": [{"nasa_id": "X", "title": "Saturn V launch (Getty Images)", "media_type": "image",
               "description": "Photo courtesy of Getty Images"}], "links": []},
]}}

ARCHIVE = {"response": {"docs": [
    {"identifier": "saturn-launch-1969", "title": "Saturn V launch", "creator": "U.S. Government",
     "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/", "description": ["Launch film", "1969"]},
    {"identifier": "no-license", "title": "Saturn V launch home movie"},
    {"identifier": "nc-item", "title": "Saturn V launch 4K", "licenseurl": "https://creativecommons.org/licenses/by-nc-nd/4.0/"},
]}}


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    d = tmp_path_factory.mktemp("dl")
    run = lambda *a: subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *a], check=True)
    run("-f", "lavfi", "-i", "testsrc2=size=320x180", "-frames:v", "1", str(d / "photo.jpg"))
    run("-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3", "-c:v", "libvpx-vp9", "-b:v", "200k",
        str(d / "clip.webm"))
    (d / "junk.jpg").write_bytes(b"not an image at all")
    return d


def _brief(ws, assets, **extra):
    (ws / "assets").mkdir(exist_ok=True)
    (ws / "brief.json").write_text(json.dumps({"title": "T", "brief": "b", "targetSeconds": 30,
                                               "assets": assets, **extra}), encoding="utf-8")


# ── licenses ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("short,url,name,usable", [
    ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0", "CC BY-SA 4.0", True),
    ("CC BY 2.0", None, "CC BY 2.0", True),
    ("Public domain", None, "Public domain", True),
    ("PD-USGov-NASA", None, "Public domain", True),
    ("CC0", None, "CC0", True),
    (None, "https://creativecommons.org/publicdomain/zero/1.0/", "CC0", True),
    (None, "http://creativecommons.org/publicdomain/mark/1.0/", "Public domain", True),
    (None, "http://creativecommons.org/licenses/publicdomain/", "Public domain", True),
    (None, "https://creativecommons.org/licenses/by/4.0/", "CC BY 4.0", True),
    ("CC BY-NC 2.0", None, "CC BY-NC 2.0", False),
    (None, "https://creativecommons.org/licenses/by-nc-nd/4.0/", "CC BY-NC-ND 4.0", False),
    ("CC BY-ND 4.0", None, "CC BY-ND 4.0", False),
    ("GFDL", None, "GFDL", False),
    (None, "http://www.nasaimages.org/Terms.html", None, False),
    (None, None, None, False),
    ("", "  ", None, False),
])
def test_parse_license(short, url, name, usable):
    lic = parse_license(short, url)
    assert (lic.name, lic.usable) == (name, usable)
    assert lic.usable or lic.reason


def test_refusal_reasons_say_why():
    assert "NonCommercial" in parse_license("CC BY-NC-SA 3.0", None).reason
    assert "NoDerivatives" in parse_license("CC BY-ND 2.0", None).reason
    assert parse_license(None, None).reason == "no license field"


# ── ranking ───────────────────────────────────────────────────────────────────

def test_terms_drop_stopwords_and_plurals():
    assert terms("The Saturn V rockets of the Apollo-11 launches, slow pan footage") == \
        ["saturn", "rocket", "apollo", "11", "launch"]  # -es after ch is dropped whole
    assert terms("glass gas") == ["glass", "gas"]  # no stem on 'ss' or short words


def _cand(title, desc="", keywords=()):
    return Candidate("commons", title, title, desc, "image", "", License("CC0", None, True), "", tuple(keywords))


def test_score_title_counts_double_and_is_bounded():
    w = {"saturn": 1.0, "launch": 1.0}
    assert score(_cand("Saturn launch"), w).score == 1.0
    assert score(_cand("Rocket", "saturn launch"), w).score == 0.5
    assert score(_cand("Saturn", keywords=["Launch"]), w).score == 0.75
    s = score(_cand("Cat"), w)
    assert s.score == 0.0 and s.matched == ()


def test_beat_words_weigh_half(tmp_path):
    _brief(tmp_path, [{"src": "./assets/a.jpg", "description": "saturn rocket"}])
    (tmp_path / "script.json").write_text(json.dumps({"beats": [
        {"narration": "It roared off the pad", "asset": "./assets/a.jpg"},
        {"narration": "unrelated", "asset": "./assets/other.jpg"}]}))
    (w,), _ = wanted_assets(tmp_path)
    assert w.beat_text == "It roared off the pad"
    assert w.weights() == {"saturn": 1.0, "rocket": 1.0, "roared": 0.5, "pad": 0.5}
    assert w.search_words() == ["saturn", "rocket"]


# ── wanted assets (the brief integration) ─────────────────────────────────────

def test_wanted_assets_from_the_brief(tmp_path):
    _brief(tmp_path, [{"src": "./assets/have.png", "description": "x"},
                      {"src": "./assets/launch.mp4", "description": "launch footage", "query": "apollo 11 launch"},
                      {"src": "./assets/replace-me.mp4", "description": "template"},
                      {"src": "./assets/stage.JPG", "description": "first stage"}],
           music={"src": "./assets/bed.wav"})
    (tmp_path / "assets" / "have.png").write_bytes(b"x")
    wanted, notes = wanted_assets(tmp_path)
    assert [(w.src, w.kind) for w in wanted] == [("./assets/launch.mp4", "video"), ("./assets/stage.JPG", "image")]
    assert wanted[0].search_words() == ["apollo", "11", "launch"]  # query beats description
    assert any("replace-me" in n for n in notes) and any("audio is not sourced" in n for n in notes)


@pytest.mark.parametrize("asset,expected", [
    ({"src": "../outside.jpg", "description": "x"}, "inside assets/"),
    ({"src": "./assets/a.gif", "description": "x"}, "cannot source .gif"),
    ({"src": "./assets/a.jpg"}, "description: needed"),
    ({"src": "./assets/a.jpg", "description": "x", "query": ""}, "query: must be"),
    ({"description": "x"}, "needs a \"src\""),
])
def test_wanted_assets_rejects(tmp_path, asset, expected):
    _brief(tmp_path, [asset])
    with pytest.raises(BriefError) as exc:
        wanted_assets(tmp_path)
    assert expected in str(exc.value)


def test_brief_accepts_query_and_hints_at_scrape(tmp_path):
    raw = {"title": "T", "brief": "b", "targetSeconds": 30,
           "assets": [{"src": "./assets/missing.jpg", "description": "d", "query": "q"}]}
    (tmp_path / "assets").mkdir()
    with pytest.raises(BriefError) as exc:
        parse_brief(raw, tmp_path, probe=lambda p: MediaInfo(True, False, None))
    (err,) = exc.value.errors
    assert "file not found" in err and "montage scrape" in err  # query itself is accepted
    raw["assets"][0]["query"] = 3
    with pytest.raises(BriefError, match="query"):
        parse_brief(raw, tmp_path, probe=lambda p: MediaInfo(True, False, None))


# ── sources: parsing the upstream shapes ──────────────────────────────────────

def test_commons_parses_licenses_and_prefers_thumbnails():
    http = FakeHttp([("commons.wikimedia.org", COMMONS)])
    r = sources.Commons().search(http, ["saturn", "launch"], "image")
    assert r.query == "saturn launch filetype:bitmap"
    assert [c.title for c in r.candidates] == ["Saturn V launch Kennedy", "Moon rock"]  # sorted by search index
    assert sorted(x.reason for x in r.refused) == ["CC BY-NC 2.0 is NonCommercial", "no license field"]
    moon = r.candidates[1]
    assert moon.attribution == "Jane & Co, via Wikimedia Commons" and moon.license.name == "CC BY-SA 4.0"
    assert sources.Commons().downloads(http, moon, 10**9)[0].endswith("/1920px.jpg")
    assert "gsrsearch=saturn+launch+filetype%3Abitmap" in http.json_calls[0]


def test_commons_video_uses_the_original_and_reports_size():
    page = _commons_page("Launch", 1, "CC0", "https://upload.wikimedia.org/commons/l.webm", mime="video/webm",
                         size=3_000_000_000)
    http = FakeHttp([("commons", {"query": {"pages": [page]}})])
    (c,) = sources.Commons().search(http, ["launch"], "video").candidates
    assert c.size == 3_000_000_000 and sources.Commons().downloads(http, c, 1)[0].endswith("l.webm")


def test_nasa_source_policy_and_third_party_refusal():
    http = FakeHttp([("images-api.nasa.gov/search", NASA)])
    r = sources.Nasa().search(http, ["saturn", "launch"], "image")
    (c,) = r.candidates
    assert c.license.basis == "source-policy" and c.license.usable
    assert c.attribution == "NASA, KSC" and c.keywords == ("Saturn V", "Launch")
    assert c.page_url == "https://images.nasa.gov/details/S69-1"
    assert len(r.refused) == 1 and "third party" in r.refused[0].reason
    urls = sources.Nasa().downloads(http, c, 10**9)
    assert [u.rsplit("~", 1)[1] for u in urls] == ["large.jpg", "orig.jpg"]  # never the thumb
    assert [u.rsplit("~", 1)[1] for u in sources.Nasa().downloads(http, c, 100_000)] == ["orig.jpg"]


def test_nasa_video_renditions_from_the_collection():
    item = {"href": "https://images-assets.nasa.gov/video/V 1/collection.json",
            "data": [{"nasa_id": "V 1", "title": "Launch", "media_type": "video"}], "links": []}
    files = [f"http://images-assets.nasa.gov/video/V 1/V 1~{n}" for n in
             ("orig.mp4", "mobile.mp4", "medium.mp4", "large.mp4", "thumb_1.jpg")] + ["x/metadata.json"]
    http = FakeHttp([("search", {"collection": {"items": [item]}}), ("collection.json", files)])
    (c,) = sources.Nasa().search(http, ["launch"], "video").candidates
    assert [u.rsplit("~", 1)[1] for u in sources.Nasa().downloads(http, c, 10**9)] == \
        ["large.mp4", "medium.mp4", "orig.mp4"]


def test_archive_needs_licenseurl_and_picks_files():
    http = FakeHttp([("advancedsearch", ARCHIVE), ("archive.org/metadata", {"files": [
        {"name": "__ia_thumb.jpg", "format": "Item Tile", "size": "3000"},
        {"name": "x.thumbs/Saturn launch_000001.jpg", "format": "Thumbnail", "size": "15000"},
        {"name": "CBS News 1969.mp4", "format": "MPEG4", "size": "23000000", "source": "original"},
        {"name": "Saturn V launch.mp4", "format": "MPEG4", "size": "8000000", "source": "original"},
        {"name": "Saturn V launch huge.mp4", "format": "MPEG4", "size": "900000000", "source": "original"},
    ]})])
    r = sources.Archive().search(http, ["saturn", "launch"], "video")
    assert "AND mediatype:(movies) AND licenseurl:*" in r.query
    (c,) = r.candidates
    assert c.license.name == "Public domain" and c.description == "Launch film; 1969"
    assert c.attribution == "U.S. Government, via Internet Archive"
    assert {x.id: x.reason for x in r.refused} == {"no-license": "no license field",
                                                  "nc-item": "CC BY-NC-ND 4.0 is NonCommercial"}
    urls = sources.Archive().downloads(http, c, 50_000_000)
    assert urls == ["https://archive.org/download/saturn-launch-1969/Saturn%20V%20launch.mp4",
                    "https://archive.org/download/saturn-launch-1969/CBS%20News%201969.mp4"]


# ── scrape end to end (fake HTTP, real ffmpeg for conversion and probing) ──────

def _three_sources(media, nasa_file=None):
    return FakeHttp(
        routes=[("commons.wikimedia.org", COMMONS), ("images-api.nasa.gov/search", NASA),
                ("advancedsearch", {"response": {"docs": []}})],
        files=[("S69-1~large", nasa_file or media / "photo.jpg"), ("S69-1~orig", media / "photo.jpg"),
               ("/commons/thumb/a.jpg", media / "photo.jpg"), ("/commons/thumb/c.jpg", media / "photo.jpg")])


@needs_ffmpeg
def test_scrape_downloads_converts_and_writes_manifest(tmp_path, media):
    _brief(tmp_path, [{"src": "./assets/saturn.png", "description": "Saturn V launch"}])
    (tmp_path / "manifest.json").write_text(json.dumps({"version": 1, "assets": {"./assets/old.jpg": {"x": 1}}}))
    http = _three_sources(media)
    outcomes, notes, manifest = scrape(tmp_path, http=http)
    (o,) = outcomes
    assert o.ok and not notes
    # Commons' public-domain 'Saturn V launch Kennedy' and NASA's 'Apollo 11 Saturn V launch' both match
    # every term in the title; the tie goes to the source listed first
    assert o.chosen.candidate.source == "commons" and o.chosen.score == 1.0
    assert [s.candidate.title for s in o.ranked[:2]] == ["Saturn V launch Kennedy", "Apollo 11 Saturn V launch"]
    assert not any(s.candidate.license.name == "CC BY-NC 2.0" for s in o.ranked)  # refused, never ranked
    out = tmp_path / "assets" / "saturn.png"
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                            str(out)], capture_output=True, text=True)
    assert probe.stdout.strip() == "png"  # .jpg download converted to the .png the brief asked for
    doc = json.loads(manifest.read_text())
    entry = doc["assets"]["./assets/saturn.png"]
    assert doc["assets"]["./assets/old.jpg"] == {"x": 1}  # merged, not overwritten
    assert entry["license"] == "Public domain" and entry["licenseBasis"] == "item-field"
    assert entry["convertedFrom"] == ".jpg" and entry["pageUrl"] and entry["downloadUrl"].endswith("1920px.jpg")
    assert entry["sha256"] == scrape_mod._sha256(out) and entry["matchedTerms"] == ["saturn", "launch"]
    assert entry["ranking"].startswith("lexical")
    assert not list((tmp_path / "cache" / "scrape").glob("*"))  # the partial download was moved / converted


@needs_ffmpeg
def test_scrape_falls_through_bad_downloads(tmp_path, media):
    _brief(tmp_path, [{"src": "./assets/saturn.jpg", "description": "Saturn V launch"}])
    http = _three_sources(media)
    http.files = [("/commons/thumb/a.jpg", TooLarge("passed the cap")),       # 1st: too big
                  ("S69-1~large", media / "junk.jpg"),                         # 2nd: not media
                  ("S69-1~orig", media / "photo.jpg")]                         # same item, next rendition
    (o,), _, _ = scrape(tmp_path, http=http)
    assert o.ok and o.entry["source"] == "nasa" and o.entry["downloadUrl"].endswith("~orig.jpg")
    assert o.entry["licenseBasis"] == "source-policy" and o.entry["convertedFrom"] is None
    assert any("cap" in e for e in o.errors) and any("unusable media" in e for e in o.errors)


@needs_ffmpeg
def test_scrape_video_conversion_and_size_precheck(tmp_path, media):
    _brief(tmp_path, [{"src": "./assets/launch.mp4", "description": "launch"}])
    pages = [_commons_page("Launch big", 1, "CC0", "https://upload.wikimedia.org/commons/big.webm",
                           mime="video/webm", size=10**10),
             _commons_page("Launch", 2, "CC0", "https://upload.wikimedia.org/commons/ok.webm",
                           mime="video/webm", size=50_000)]
    http = FakeHttp([("commons", {"query": {"pages": pages}})], [("ok.webm", media / "clip.webm")])
    (o,), _, _ = scrape(tmp_path, http=http, source_names=("commons",))
    assert o.ok and o.entry["convertedFrom"] == ".webm"
    assert http.download_calls == ["https://upload.wikimedia.org/commons/ok.webm"]  # big one never requested
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                           str(tmp_path / "assets" / "launch.mp4")], capture_output=True, text=True).stdout
    assert info.strip() == "h264"


def test_scrape_nothing_licensed_or_relevant(tmp_path):
    _brief(tmp_path, [{"src": "./assets/x.jpg", "description": "underwater basket weaving"}])
    http = FakeHttp([("commons", COMMONS), ("nasa.gov/search", NetError("could not reach images-api.nasa.gov"))])
    (o,), _, manifest = scrape(tmp_path, http=http)
    assert not o.ok and not o.ranked and o.below and manifest is None
    assert any(e.startswith("nasa:") for e in o.errors)
    assert not http.download_calls


def test_scrape_backs_off_search_terms(tmp_path):
    _brief(tmp_path, [{"src": "./assets/x.jpg", "description": "saturn launch kennedy pad night"}])
    hits = lambda url: "gsrsearch=saturn+launch+filetype" in url
    http = FakeHttp([(hits, COMMONS)])
    (o,), _, _ = scrape(tmp_path, http=http, source_names=("commons",), dry_run=True)
    searched = [u.split("gsrsearch=")[1].split("&")[0] for u in http.json_calls]
    assert searched == ["saturn+launch+kennedy+pad+night+filetype%3Abitmap",
                        "saturn+launch+kennedy+pad+filetype%3Abitmap",
                        "saturn+launch+kennedy+filetype%3Abitmap",
                        "saturn+launch+filetype%3Abitmap"]
    assert o.ranked and not http.download_calls  # dry run


def test_scrape_needs_a_brief(tmp_path):
    with pytest.raises(scrape_mod.ScrapeError, match="montage init"):
        scrape(tmp_path, http=FakeHttp())


# ── CLI ───────────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_cli_scrape(tmp_path, media, monkeypatch, capsys):
    _brief(tmp_path, [{"src": "./assets/saturn.jpg", "description": "Saturn V launch"},
                      {"src": "./assets/weaving.jpg", "description": "underwater basket weaving"}])
    monkeypatch.setattr(net, "Http", lambda: _three_sources(media))
    assert main(["scrape", str(tmp_path)]) == 1  # one of two found nothing relevant
    out = capsys.readouterr()
    # Commons: one NonCommercial + one untagged; NASA: one crediting Getty
    assert "not CLIP" in out.out and "refused 3 without a usable license" in out.out
    assert "-> saved ./assets/saturn.jpg" in out.out and "./assets/weaving.jpg NOT sourced" in out.out
    assert "1 visual(s) still missing" in out.err

    _brief(tmp_path, [{"src": "./assets/saturn.jpg", "description": "Saturn V launch"}])
    assert main(["scrape", str(tmp_path)]) == 0
    assert "nothing to fetch" in capsys.readouterr().out
    assert main(["scrape", str(tmp_path), "--sources", "commons,flickr"]) == 1
    assert main(["scrape", str(tmp_path / "nope")]) == 1


def test_cli_scrape_network_down_is_exit_2(tmp_path, monkeypatch, capsys):
    _brief(tmp_path, [{"src": "./assets/x.jpg", "description": "saturn"}])
    down = NetError("could not reach host")
    monkeypatch.setattr(net, "Http", lambda: FakeHttp([(lambda u: True, down)]))
    assert main(["scrape", str(tmp_path)]) == 2
    assert "search failed: commons: could not reach host" in capsys.readouterr().out


# ── net.Http against a fake urlopen ───────────────────────────────────────────

class _Resp(io.BytesIO):
    def __init__(self, body, length=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(length)} if length is not None else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_http_sends_user_agent_and_timeout(monkeypatch):
    seen = {}

    def opener(req, timeout):
        seen.update(url=req.full_url, ua=req.get_header("User-agent"), timeout=timeout)
        return _Resp(b'{"ok": 1}')
    monkeypatch.delenv("MONTAGE_CONTACT", raising=False)
    assert Http(timeout=7, opener=opener).json("http://images-assets.nasa.gov/video/A B/x.json") == {"ok": 1}
    assert seen["url"] == "https://images-assets.nasa.gov/video/A%20B/x.json"  # quoted + https
    assert seen["timeout"] == 7 and seen["ua"].startswith("MontageStudio/") and "@" not in seen["ua"]
    monkeypatch.setenv("MONTAGE_CONTACT", "ops@example.org")
    assert "ops@example.org" in net.user_agent()


def test_http_errors_become_neterror():
    def refuse(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
    with pytest.raises(NetError, match="HTTP 503"):
        Http(opener=refuse).json("https://commons.wikimedia.org/w/api.php")

    def down(req, timeout):
        raise urllib.error.URLError("timed out")
    with pytest.raises(NetError, match="could not reach archive.org"):
        Http(opener=down).json("https://archive.org/advancedsearch.php")
    with pytest.raises(NetError, match="non-JSON"):
        Http(opener=lambda r, timeout: _Resp(b"<html>")).json("https://x.org")


def test_http_download_cap(tmp_path):
    big = b"x" * 5000
    dest = tmp_path / "d" / "f.bin"
    assert Http(opener=lambda r, timeout: _Resp(big)).download("https://x.org/f", dest, 5000) == 5000
    with pytest.raises(TooLarge, match="declared"):
        Http(opener=lambda r, timeout: _Resp(big, length=5000)).download("https://x.org/f", dest, 4999)
    dest.unlink()
    with pytest.raises(TooLarge, match="while downloading"):  # no Content-Length: stopped mid-stream
        Http(opener=lambda r, timeout: _Resp(big)).download("https://x.org/f", dest, 4999)
    assert not dest.exists()  # the partial file is removed


def test_safe_url_keeps_existing_escapes():
    assert net.safe_url("https://a.org/x%20y z?q=a b&r=1") == "https://a.org/x%20y%20z?q=a%20b&r=1"


# ── live (opt-in) ─────────────────────────────────────────────────────────────

@pytest.mark.skipif(os.environ.get("MONTAGE_LIVE_NET") != "1", reason="live network test: set MONTAGE_LIVE_NET=1")
@needs_ffmpeg
def test_live_nasa_and_commons(tmp_path):
    _brief(tmp_path, [{"src": "./assets/saturn.jpg", "description": "Saturn V rocket launch"}])
    for name in ("nasa", "commons"):
        (o,), _, _ = scrape(tmp_path, source_names=(name,), dry_run=True)
        assert o.ranked, f"{name}: no licensed candidates ({o.errors})"
        assert all(s.candidate.license.usable for s in o.ranked)
    (o,), _, manifest = scrape(tmp_path, source_names=("nasa",), max_mb=15)
    assert o.ok, o.errors
    assert json.loads(manifest.read_text())["assets"]["./assets/saturn.jpg"]["source"] == "nasa"
