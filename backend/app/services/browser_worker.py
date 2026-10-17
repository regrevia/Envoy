from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _write(payload: dict):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _executable_path() -> str | None:
    configured = os.getenv("ENVOY_BROWSER_EXECUTABLE", "").strip()
    candidates = [
        configured,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    return next((path for path in candidates if path and Path(path).is_file()), None)


def _extract_records(page):
    return page.eval_on_selector_all(
        "a[href]",
        """anchors => anchors.map(anchor => {
          const rect = anchor.getBoundingClientRect();
          const style = window.getComputedStyle(anchor);
          let text = (anchor.innerText || anchor.getAttribute('aria-label') || anchor.getAttribute('title') || '').trim();
          if (!text && anchor.parentElement) text = (anchor.parentElement.innerText || '').trim();
          return {
            href: anchor.href,
            text: text.slice(0, 240),
            aria_label: anchor.getAttribute('aria-label') || '',
            title: anchor.getAttribute('title') || '',
            visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none'
          };
        })""",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--profile-dir", required=True)
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        _write({"status": "failed", "error": f"Playwright is not installed: {exc}"})
        return 1

    with sync_playwright() as playwright:
        executable = _executable_path()
        launch_kwargs = {
            "headless": False,
            "user_data_dir": args.profile_dir,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if executable:
            launch_kwargs["executable_path"] = executable
        try:
            context = playwright.chromium.launch_persistent_context(**launch_kwargs)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            _write({"status": "ready", "title": page.title(), "url": page.url})
        except Exception as exc:
            _write({"status": "failed", "error": str(exc)[:1200]})
            return 1

        try:
            for line in sys.stdin:
                command = json.loads(line).get("command")
                if command == "status":
                    _write({
                        "status": "page",
                        "title": page.title(),
                        "url": page.url,
                        "body_text": page.locator("body").inner_text(timeout=5000)[:4000],
                    })
                elif command == "extract":
                    for _ in range(6):
                        page.mouse.wheel(0, 900)
                        page.wait_for_timeout(700)
                    _write({
                        "status": "extracted",
                        "title": page.title(),
                        "url": page.url,
                        "body_text": page.locator("body").inner_text(timeout=5000)[:4000],
                        "records": _extract_records(page),
                    })
                elif command == "close":
                    _write({"status": "closed"})
                    break
                else:
                    _write({"status": "failed", "error": f"Unknown browser command: {command}"})
        finally:
            context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
