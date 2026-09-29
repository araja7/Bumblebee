"""HTTP client: retries/backoff, 404 handling, robots.txt, ETag cache. Session is mocked."""
from unittest import mock

import pytest
import requests

from bumblebee.http import HttpClient, HttpError, NotFound, RobotsDisallowed


def resp(status, text="", headers=None, json_data=None):
    r = mock.Mock(status_code=status, text=text, headers=headers or {})
    r.json.return_value = json_data
    return r


def client(tmp_path=None, **cfg):
    session = mock.Mock()
    session.headers = {}
    base = {"max_retries": 3, "backoff_base": 0.01, "default_min_interval": 0, "respect_robots": True}
    base.update(cfg)
    return HttpClient(base, cache_dir=tmp_path, session=session), session


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("bumblebee.http.time.sleep", lambda s: None)


def route(session, mapping):
    def get(url, headers=None, timeout=None):
        assert timeout is not None, "every request must have a timeout"
        val = mapping[url]
        return val.pop(0) if isinstance(val, list) else val
    session.get.side_effect = get


def test_retries_then_succeeds():
    c, s = client()
    route(s, {"https://api.test/robots.txt": resp(404),
              "https://api.test/x": [resp(503), resp(429), resp(200, json_data={"ok": 1})]})
    assert c.get_json("https://api.test/x") == {"ok": 1}


def test_gives_up_after_max_retries():
    c, s = client(max_retries=2)
    route(s, {"https://api.test/robots.txt": resp(404), "https://api.test/x": [resp(500)] * 3})
    with pytest.raises(HttpError):
        c.get("https://api.test/x")


def test_connection_errors_retried():
    c, s = client()
    calls = {"n": 0}

    def get(url, headers=None, timeout=None):
        if url.endswith("robots.txt"):
            return resp(404)
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("reset")
        return resp(200, json_data=[1])
    s.get.side_effect = get
    assert c.get_json("https://api.test/y") == [1]


def test_404_not_retried():
    c, s = client()
    route(s, {"https://api.test/robots.txt": resp(404), "https://api.test/gone": resp(404)})
    with pytest.raises(NotFound):
        c.get("https://api.test/gone")
    assert s.get.call_count == 2  # robots + one attempt


def test_robots_disallow():
    c, s = client()
    route(s, {"https://site.test/robots.txt": resp(200, "User-agent: *\nDisallow: /embed/\nCrawl-delay: 2")})
    with pytest.raises(RobotsDisallowed):
        c.get("https://site.test/embed/job")
    assert c.limiter.per_host["site.test"] == 2.0   # Crawl-delay honored


def test_robots_4xx_means_allow():
    c, s = client()
    route(s, {"https://api.test/robots.txt": resp(401, "Unauthorized"), "https://api.test/ok": resp(200)})
    assert c.get("https://api.test/ok").status_code == 200


def test_etag_cache(tmp_path):
    c, s = client(tmp_path)
    route(s, {"https://raw.test/robots.txt": resp(404),
              "https://raw.test/big.json": [resp(200, "BODY", {"ETag": '"abc"'}), resp(304)]})
    assert c.get_cached_text("https://raw.test/big.json") == "BODY"
    assert c.get_cached_text("https://raw.test/big.json") == "BODY"
    second_headers = s.get.call_args_list[-1].kwargs["headers"]
    assert second_headers["If-None-Match"] == '"abc"'
