from envoy_cli import reader


class _Response:
    def __init__(self, status, location=None):
        self.status_code = status
        self.headers = {"location": location} if location else {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Client:
    def __init__(self, hops):
        self._hops = list(hops)
        self.requested = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, _method, url, headers=None):
        self.requested.append(url)
        if not self._hops:
            return _Response(200)
        return _Response(*self._hops.pop(0))


def _patch(monkeypatch, hops):
    client = _Client(hops)
    monkeypatch.setattr(reader, "proxy_plan", lambda: [None])
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(reader.httpx, "Client", lambda **_kwargs: client)
    return client


def test_a_share_link_resolves_to_the_video_page(monkeypatch):
    client = _patch(
        monkeypatch, [(302, "https://www.douyin.com/video/7698358547440094502")]
    )

    resolved = reader._resolve_short_video_url("https://v.douyin.com/FMui7Rt9f4Y/")

    assert resolved == "https://www.douyin.com/video/7698358547440094502"
    assert client.requested == [
        "https://v.douyin.com/FMui7Rt9f4Y/",
        "https://www.douyin.com/video/7698358547440094502",
    ]


def test_a_share_link_that_lands_on_a_feed_is_left_alone(monkeypatch):
    _patch(monkeypatch, [(302, "https://www.douyin.com/discover")])

    assert (
        reader._resolve_short_video_url("https://v.douyin.com/token/")
        == "https://v.douyin.com/token/"
    )


def test_other_hosts_are_not_probed(monkeypatch):
    probed = []
    monkeypatch.setattr(reader.httpx, "Client", lambda **_kwargs: probed.append(1))

    assert (
        reader._resolve_short_video_url("https://www.douyin.com/video/1")
        == "https://www.douyin.com/video/1"
    )
    assert probed == []


def test_a_transport_failure_keeps_the_original_link(monkeypatch):
    class _Boom:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            raise reader.httpx.TransportError("no route")

    monkeypatch.setattr(reader, "proxy_plan", lambda: [None])
    monkeypatch.setattr(reader.httpx, "Client", lambda **_kwargs: _Boom())

    assert (
        reader._resolve_short_video_url("https://v.douyin.com/token/")
        == "https://v.douyin.com/token/"
    )
