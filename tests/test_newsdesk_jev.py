import pytest
import requests

from ffl.newsdesk import jev


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


def test_ask_posts_bearer_and_parses(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k123")
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, body=json, timeout=timeout)
        return Resp(200, {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.9}},
                          "usage": {"input_tokens": 10, "output_tokens": 2}})

    monkeypatch.setattr(requests, "post", post)
    got = jev.ask({"title": "t"}, {"q": {"type": "noul", "instructions": "?"}})
    assert got == {"q": {"type": "noul", "noul": 0.9}}
    assert got.usage == {"input_tokens": 10, "output_tokens": 2}  # the bill, beside the answers
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["headers"]["Authorization"] == "Bearer k123"
    assert seen["body"]["model"] == "jev-latest" and seen["body"]["state"] == {"title": "t"}
    assert seen["timeout"] == 20


def test_ask_raises_jeverror_on_http_error(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k123")
    monkeypatch.setattr(requests, "post", lambda *a, **kw: Resp(422, {"detail": "bad question"}))
    with pytest.raises(jev.JevError) as e:
        jev.ask("s", {"q": {"type": "noul", "instructions": "?"}})
    assert e.value.status == 422

    def down(*a, **kw):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(requests, "post", down)
    with pytest.raises(jev.JevError) as e:
        jev.ask("s", {"q": {"type": "noul", "instructions": "?"}})
    assert e.value.status is None


def test_ask_without_key_raises_jeverror(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(requests, "post", lambda *a, **kw: pytest.fail("no key, no call"))
    with pytest.raises(jev.JevError):
        jev.ask("s", {"q": {"type": "noul", "instructions": "?"}})


def test_ask_missing_answer_raises(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "k123")
    monkeypatch.setattr(requests, "post", lambda *a, **kw: Resp(200, {"answers": {}}))
    with pytest.raises(jev.JevError):
        jev.ask("s", {"q": {"type": "noul", "instructions": "?"}})
