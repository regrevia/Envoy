"""A prompt laid over readable material is not a sign-in wall.

Clicking the close control is the first answer; a prompt with no working
control is stripped so the page underneath can be read. Only a page whose own
text still asks for credentials counts as a gate.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-core" / "src"))
sys.path.insert(0, str(ROOT_DIR / "packages" / "envoy-cli" / "src"))

from envoy_cli import browser  # noqa: E402

ARTICLE = "本文正文的这一段说明实际内容，与登录无关。" * 40


class _Locator:
    def __init__(self, page: "FakePage") -> None:
        self.page = page

    def inner_text(self, **_kwargs) -> str:
        return self.page.body_text


class FakePage:
    """Enough of a Playwright page for the dismiss-and-read path."""

    def __init__(self, body_text: str, title: str = "", main: str | None = None):
        self.body_text = body_text
        self.title_text = title
        self.main_text = body_text if main is None else main
        self.after_strip: str | None = None
        self.frames = []
        self.url = "https://example.org/article"

    def title(self) -> str:
        return self.title_text

    def locator(self, _selector: str) -> _Locator:
        return _Locator(self)

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def content(self) -> str:
        return "<html><body></body></html>"

    def evaluate(self, script: str):
        if "main-content" in script:
            return self.main_text
        if "strip-wall-dialog" in script:
            if self.after_strip is None:
                return 0
            self.body_text = self.after_strip
            self.after_strip = None
            return 1
        return 0


def test_dialog_text_does_not_turn_readable_page_into_a_challenge():
    page = FakePage(
        body_text="验证码登录\n扫码登录\n" + ARTICLE,
        title="如何看待这个问题",
        main=ARTICLE,
    )

    _title, text, challenge = browser._page_state(page)

    assert challenge is False
    assert text == ARTICLE


def test_a_page_that_itself_asks_for_login_stays_a_challenge():
    page = FakePage(
        body_text="安全验证\n请完成验证后继续",
        title="访问验证",
        main="安全验证\n请完成验证后继续",
    )

    _title, _text, challenge = browser._page_state(page)

    assert challenge is True


def test_undismissable_prompt_is_stripped_so_the_page_can_be_read():
    page = FakePage(body_text="扫码登录\n登录后查看完整内容", title="某平台", main="扫码登录")
    page.after_strip = ARTICLE

    title, text = browser._load_visible_content(page)

    assert title == "某平台"
    assert text == ARTICLE


def test_strip_never_touches_a_page_with_real_material():
    page = FakePage(body_text=ARTICLE, title="文章", main=ARTICLE)

    title, text = browser._load_visible_content(page)

    assert text == ARTICLE
    assert page.after_strip is None
