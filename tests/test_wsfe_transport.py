import arca_integration.core.client as client_module
from arca_integration.core.client import ArcaClient, _default_post


class Response:
    status_code = 200
    text = ""
    content = b"<ok/>"


def capture_post(monkeypatch, response=None):
    seen = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["timeout"] = timeout
        return response or Response()

    monkeypatch.setattr("requests.post", fake_post)
    return seen


def test_default_post_sends_the_given_soap_action(monkeypatch):
    seen = capture_post(monkeypatch)

    _default_post("https://example.test", b"body", soap_action="urn:FECAESolicitar")

    assert seen["headers"]["SOAPAction"] == '"urn:FECAESolicitar"'


def test_default_post_sends_a_blank_action_by_default(monkeypatch):
    seen = capture_post(monkeypatch)

    _default_post("https://example.test", b"body")

    assert seen["headers"]["SOAPAction"] == '""'


def test_arca_client_still_posts_without_an_action(monkeypatch):
    seen = capture_post(monkeypatch)
    client = ArcaClient("u", "p", "c", "k", "20000000001")

    client._call("https://example.test", b"body")

    assert seen["headers"]["SOAPAction"] == '""'
