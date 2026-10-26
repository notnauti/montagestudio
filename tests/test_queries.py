"""scrape --suggest-queries: Claude's archive queries, saved, reused, and always behind the brief's own."""

import json
import types

import pytest

from montage import cli, queries, vision
from montage.queries import QueryError, parse
from montage.scrape import scrape, wanted_assets
from montage.script import CLAUDE_MODEL
from test_sourcing import FakeHttp, _brief

ASSETS = [{"src": "./assets/mirror.jpg", "description": "primary mirror fully unfolded, gold hexagonal segments"},
          {"src": "./assets/orbit.jpg", "description": "orbit around the second Lagrange point L2"},
          {"src": "./assets/launch.jpg", "description": "Ariane 5 launch", "query": "webb ariane launch"}]
SRCS = ["./assets/mirror.jpg", "./assets/orbit.jpg"]
GOOD = {"queries": [{"asset": "./assets/mirror.jpg", "query": "webb gold mirror revealed"},
                    {"asset": "./assets/orbit.jpg", "query": "webb orbit illustration"}]}


# ── parsing ───────────────────────────────────────────────────────────────────

def test_parse_good_queries_and_normalise_spacing_and_quotes():
    got = parse(json.dumps({"queries": [{"asset": SRCS[0], "query": ' "webb   gold" mirror '},
                                        {"asset": SRCS[1], "query": "webb orbit"}]}), SRCS)
    assert got == {SRCS[0]: "webb gold mirror", SRCS[1]: "webb orbit"}


@pytest.mark.parametrize("items, reason", [
    ([{"asset": SRCS[0], "query": "mirror"}, {"asset": SRCS[1], "query": "webb orbit"}], "1 words"),
    ([{"asset": SRCS[0], "query": "a b c d e f g"}, {"asset": SRCS[1], "query": "webb orbit"}], "7 words"),
    ([{"asset": SRCS[0], "query": "webb mirror"}], "no query for ./assets/orbit.jpg"),
    ([{"asset": SRCS[0], "query": "webb mirror"}, {"asset": SRCS[0], "query": "webb gold"},
      {"asset": SRCS[1], "query": "webb orbit"}], "more than one query"),
    ([{"asset": "./assets/other.jpg", "query": "webb x"}], "unusable item"),
])
def test_queries_that_break_the_rules_are_rejected_whole(items, reason):
    with pytest.raises(QueryError, match=reason):
        parse(json.dumps({"queries": items}), SRCS)


def test_non_json_is_rejected():
    with pytest.raises(QueryError, match="not the expected JSON"):
        parse("webb mirror", SRCS)


# ── precedence and storage ────────────────────────────────────────────────────

def test_the_brief_query_wins_then_the_suggestion_then_the_description(tmp_path):
    _brief(tmp_path, ASSETS)
    queries.save(tmp_path, {SRCS[0]: "webb gold mirror revealed", "./assets/launch.jpg": "ignored"}, "m")
    by_src = {w.src: w for w in queries.apply(wanted_assets(tmp_path)[0], queries.load(tmp_path))}
    assert (by_src[SRCS[0]].query, by_src[SRCS[0]].query_from) == ("webb gold mirror revealed", "suggested")
    assert (by_src[SRCS[1]].query, by_src[SRCS[1]].query_from) == (None, "description")
    assert (by_src["./assets/launch.jpg"].query, by_src["./assets/launch.jpg"].query_from) == \
        ("webb ariane launch", "brief")


def test_save_merges_and_load_survives_a_bad_file(tmp_path):
    queries.save(tmp_path, {SRCS[0]: "webb mirror"}, "m1")
    queries.save(tmp_path, {SRCS[1]: "webb orbit"}, "m2")
    assert queries.load(tmp_path) == {SRCS[0]: "webb mirror", SRCS[1]: "webb orbit"}
    queries.path(tmp_path).write_text("{not json", encoding="utf-8")
    assert queries.load(tmp_path) == {}


def test_the_search_sends_the_suggestion_and_the_manifest_says_so(tmp_path):
    _brief(tmp_path, ASSETS[:1])
    queries.save(tmp_path, {SRCS[0]: "webb gold mirror revealed"}, "m")
    http = FakeHttp()
    (o,), _, _ = scrape(tmp_path, http=http, source_names=("commons",), dry_run=True)
    sent = http.json_calls[0].split("gsrsearch=")[1].split("&")[0]
    assert sent.startswith("webb+gold+mirror+revealed")
    assert o.wanted.query_from == "suggested"


# ── asking Claude ─────────────────────────────────────────────────────────────

class FakeClient:
    def __init__(self, text=json.dumps(GOOD), stop_reason="end_turn"):
        self.calls = []
        self.response = types.SimpleNamespace(stop_reason=stop_reason, model=CLAUDE_MODEL,
                                              content=[types.SimpleNamespace(type="text", text=text)])
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        return self.response


def test_only_assets_without_a_brief_query_are_asked_for_and_the_answers_saved(tmp_path):
    _brief(tmp_path, ASSETS)
    client = FakeClient()
    got, err = queries.suggest(tmp_path, queries.ClaudeQueries(client=client))
    assert err is None and got == {SRCS[0]: "webb gold mirror revealed", SRCS[1]: "webb orbit illustration"}
    k = client.calls[0]
    assert k["model"] == CLAUDE_MODEL
    enum = k["output_config"]["format"]["schema"]["properties"]["queries"]["items"]["properties"]["asset"]["enum"]
    assert enum == SRCS                                         # the brief-queried launch is not asked for
    assert "The video: b" in k["messages"][0]["content"] and "Ariane" not in k["messages"][0]["content"]
    assert queries.load(tmp_path) == got


def test_a_failed_request_saves_nothing(tmp_path):
    _brief(tmp_path, ASSETS)
    got, err = queries.suggest(tmp_path, queries.ClaudeQueries(client=FakeClient(stop_reason="refusal")))
    assert got == {} and "declined" in err
    assert not queries.path(tmp_path).exists()


def test_nothing_to_ask_makes_no_request(tmp_path):
    _brief(tmp_path, ASSETS[2:])
    client = FakeClient()
    assert queries.suggest(tmp_path, queries.ClaudeQueries(client=client)) == ({}, None)
    assert client.calls == []


# ── the CLI ───────────────────────────────────────────────────────────────────

def test_cli_without_credentials_exits_2_before_any_search(tmp_path, monkeypatch, capsys):
    _brief(tmp_path, ASSETS)
    monkeypatch.setattr(vision, "unavailable_reason", lambda: "no Claude credentials")
    assert cli.main(["scrape", str(tmp_path), "--suggest-queries", "--dry-run"]) == 2
    assert "--suggest-queries needs Claude" in capsys.readouterr().err


def test_cli_failed_suggestion_still_searches_and_exits_2(tmp_path, monkeypatch, capsys):
    _brief(tmp_path, ASSETS[:1])
    monkeypatch.setattr(vision, "unavailable_reason", lambda: None)
    real = queries.ClaudeQueries
    monkeypatch.setattr(queries, "ClaudeQueries", lambda: real(client=FakeClient(stop_reason="refusal")))
    monkeypatch.setattr("montage.net.Http", FakeHttp)
    assert cli.main(["scrape", str(tmp_path), "--suggest-queries", "--dry-run", "--sources", "commons",
                     "--no-candidates"]) == 2
    out = capsys.readouterr()
    assert "suggest-queries failed (Claude declined" in out.err and "(from the description)" in out.out


def test_cli_prints_and_uses_the_suggestions(tmp_path, monkeypatch, capsys):
    _brief(tmp_path, ASSETS[:2])
    monkeypatch.setattr(vision, "unavailable_reason", lambda: None)
    real = queries.ClaudeQueries
    monkeypatch.setattr(queries, "ClaudeQueries", lambda: real(client=FakeClient()))
    monkeypatch.setattr("montage.net.Http", FakeHttp)
    assert cli.main(["scrape", str(tmp_path), "--suggest-queries", "--dry-run", "--sources", "commons",
                     "--no-candidates"]) == 0
    out = capsys.readouterr().out
    assert "suggested queries (2), saved to cache/queries.json" in out
    assert "search words: webb gold mirror revealed  (suggested query, cache/queries.json)" in out


def test_pick_refuses_suggest_queries(tmp_path, capsys):
    _brief(tmp_path, ASSETS)
    assert cli.main(["scrape", str(tmp_path), "--pick", "mirror=1", "--suggest-queries"]) == 1
    assert "no --dry-run, --vision or --suggest-queries" in capsys.readouterr().err
