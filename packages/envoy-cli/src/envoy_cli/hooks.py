"""Deterministic link-routing hooks shared by coding-agent hosts.

The hooks intentionally do not fetch anything. They only recognise a public
URL in the user's prompt and inject a stable, host-neutral routing policy that
tells the model to try Envoy's MCP reader before a built-in web fetch. Host
adapters (Claude Code, Codex, DeepSeek Harness) are thin wrappers around
``build_prompt_context``; keeping the policy here makes it testable without any
host installed.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional
from urllib.parse import urlsplit

HookHost = Literal["claude-code", "codex", "dsh"]

HOOK_MARKER = "[envoy-link-routing]"
SUPPORTED_HOSTS: tuple[str, ...] = ("claude-code", "codex", "dsh")

_URL_RE = re.compile(
    r"https?://[^\s<>\"'`，。；：！？、（）【】「」《》]+",
    re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_TRAILING_PUNCTUATION = ".,;:!?，。；：！？、"
_TRAILING_BRACKETS = ")]}）】」》"
_OPT_OUT_MARKERS = (
    "不要用 envoy",
    "不要使用 envoy",
    "不用 envoy",
    "别用 envoy",
    "don't use envoy",
    "do not use envoy",
    "no envoy",
)
_HOST_ALIASES = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "claudecode": "claude-code",
    "codex": "codex",
    "dsh": "dsh",
    "deepseek-harness": "dsh",
    "deepseek_harness": "dsh",
}


@dataclass(frozen=True)
class PromptRouting:
    """The result of inspecting one prompt for public links."""

    urls: tuple[str, ...]
    opted_out: bool

    @property
    def should_route(self) -> bool:
        return bool(self.urls) and not self.opted_out


def normalize_host(value: Optional[str]) -> Optional[str]:
    """Return a canonical host id, or ``None`` when it is unknown."""

    if not value:
        return None
    return _HOST_ALIASES.get(value.strip().lower())


def _strip_url(url: str) -> str:
    url = url.rstrip(_TRAILING_PUNCTUATION)
    while url and url[-1] in _TRAILING_BRACKETS:
        opener = {")": "(", "]": "[", "}": "{", "）": "（", "】": "【", "」": "「", "》": "《"}[url[-1]]
        if url.count(opener) >= url.count(url[-1]):
            break
        url = url[:-1].rstrip(_TRAILING_PUNCTUATION)
    return url


def _is_public_url(url: str) -> bool:
    """Reject local, private, and non-routable destinations.

    The hook only routes links that a remote reader could plausibly fetch. A
    localhost URL is almost always a development preview and should stay with
    the host's own browser tools.
    """

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False

    host = parts.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain", "0.0.0.0"} or host.endswith(".localhost"):
        return False
    if host.endswith((".local", ".internal", ".home.arpa")):
        return False

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A DNS name. Single-label names are usually intranet hosts.
        if "." not in host:
            return False
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


_CODE_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")
_CODE_PATH_RE = re.compile(
    r"^/[^/]+/[^/]+/(?:-/)?(?:commit|commits|blob|tree|raw|pull|pulls|pull-requests|merge_requests|issues|compare)(?:/|$)",
    re.IGNORECASE,
)


def is_code_host_diff_url(url: str) -> bool:
    """Whether ``url`` is a source-code page whose diff Envoy cannot return.

    Envoy reads the rendered page, so a commit, file, tree, pull-request, or
    issue page yields the title and at most the commit message, never the diff.
    Those links belong to `git`/`gh`, and routing them to Envoy costs a fetch
    that cannot answer the question.
    """

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in {"gist.github.com"}:
        return False
    if host not in _CODE_HOSTS and not any(host.endswith("." + item) for item in _CODE_HOSTS):
        return False
    return bool(_CODE_PATH_RE.match(parts.path or ""))


def _routable(urls: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(url for url in urls if not is_code_host_diff_url(url))


def extract_public_urls(text: str) -> tuple[str, ...]:
    """Extract unique public ``http(s)`` URLs, preserving first-seen order."""

    seen: set[str] = set()
    result: list[str] = []
    for match in _URL_RE.finditer(text or ""):
        url = _strip_url(match.group(0))
        if not url or not _is_public_url(url):
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(url)
    return tuple(result)


def _opted_out(text: str) -> bool:
    lowered = (text or "").lower()
    compact = re.sub(r"\s+", "", lowered)
    return any(
        marker in lowered or re.sub(r"\s+", "", marker) in compact
        for marker in _OPT_OUT_MARKERS
    )


def inspect_prompt(text: str) -> PromptRouting:
    return PromptRouting(urls=_routable(extract_public_urls(text)), opted_out=_opted_out(text))


def detect_prompt_language(text: str) -> str:
    """Pick the routing-notice language from the prompt itself."""

    return "zh" if _CJK_RE.search(text or "") else "en"


def resolve_prompt_language(text: str, language: Optional[str] = "auto") -> str:
    """Resolve ``auto`` (default), ``zh``, or ``en`` for one prompt."""

    value = (language or "auto").strip().lower()
    if value.startswith("en"):
        return "en"
    if value.startswith("zh"):
        return "zh"
    return detect_prompt_language(text)


def _tool_phrase(host: HookHost, language: str = "zh") -> str:
    if language.lower().startswith("en"):
        if host == "dsh":
            return "the Envoy MCP tool `mcp__envoy__envoy_read`"
        if host == "claude-code":
            return (
                "the Envoy MCP tool `envoy_read` (shown as "
                "`mcp__envoy__envoy_read` in Claude Code)"
            )
        return "the Envoy MCP tool `envoy_read` (check the tool catalog for the exact name)"
    if host == "dsh":
        return "Envoy MCP 工具 `mcp__envoy__envoy_read`"
    if host == "claude-code":
        return "Envoy MCP 工具 `envoy_read`（Claude Code 中显示为 `mcp__envoy__envoy_read`）"
    return "Envoy MCP 工具 `envoy_read`（Codex 中的实际名称以工具目录为准）"


def build_prompt_context(
    urls: Iterable[str],
    host: HookHost = "claude-code",
    *,
    language: str = "zh",
) -> str:
    """Build the injected routing policy for a prompt with public links."""

    url_list = list(dict.fromkeys(urls))
    link_text = "\n".join(f"- {url}" for url in url_list)
    if language.lower().startswith("en"):
        return (
            f"{HOOK_MARKER}\n"
            "This turn contains public link(s):\n"
            f"{link_text}\n\n"
            f"Use {_tool_phrase(host, 'en')} first. For a local video or audio file, call "
            "`envoy_read` with the file path or its `file://` URL (the `full` toolset also "
            "exposes `envoy_read_local_video`, and the `envoy read-local-video` CLI takes "
            "the same request). Do not hand these links to a built-in "
            "web fetch, curl, or a browser before Envoy has been tried.\n"
            "Keep the returned `material_id`, locators, and coverage. Continue with "
            "`envoy_expand` or `envoy_search`; use `envoy_replay` only when that tool is "
            "exposed. Do not re-fetch. "
            "Request bounded ranges and use search/expand so the page text does not "
            "flood the context; that is the token saving this hook is for.\n"
            "Envoy is not a replacement for the host's web search. A query without a "
            "user-provided link should still use native search; use Envoy to read the "
            "user's links, videos, audio, and documents, or search results that need "
            "full-source evidence. Envoy also handles video/audio that text-only web "
            "tools cannot.\n"
            "Fall back to built-in fetching only when Envoy returns `blocked` / "
            "`challenge_required`, explicitly needs sign-in or a CAPTCHA, or the Envoy "
            "tools are unavailable; state the fallback reason to the user.\n"
            "Images and OCR text are source evidence. When page text and a screenshot "
            "disagree, trust the image and do not invent missing text. Cite locators."
        )
    return (
        f"{HOOK_MARKER}\n"
        "本轮用户消息包含公网链接：\n"
        f"{link_text}\n\n"
        f"1. 先调用 {_tool_phrase(host)}。本地视频或音频用 `envoy_read` 传文件路径或 "
        "`file://` URL（`full` 工具集另有 `envoy_read_local_video`，CLI "
        "`envoy read-local-video` 接收同一份请求）。"
        "不要把链接直接交给内置网页抓取、curl 或浏览器。\n"
        "2. 保留返回的 `material_id`、locator 和 coverage；继续读取用 "
        "`envoy_expand` 或 `envoy_search`，回放用 `envoy_replay`（仅在工具集暴露时），"
        "不要重复抓取。"
        "按范围读取并用 search/expand 定位，避免把整页原文灌进上下文——这正是 "
        "Envoy 节省 token 的方式。\n"
        "3. Envoy 不替代宿主的联网搜索：用户没有给链接的检索仍用宿主搜索工具；"
        "Envoy 负责用户给出的链接、视频、音频和文档，以及需要完整来源证据的"
        "搜索结果。视频/音频是纯文本联网工具处理不了的场景。\n"
        "4. 只有 Envoy 返回 `blocked` / `challenge_required`、明确需要登录或验证码、"
        "或 Envoy 工具不可用时，才回退到内置抓取，并向用户说明回退原因。\n"
        "5. 图片和 OCR 文本是源证据。页面文字与截图冲突时以图片为准，不要臆造缺失文字；"
        "引用结论时带上 locator。\n"
        "6. 如果用户明确要求不用 Envoy，或链接是 localhost/内网地址，忽略本条。"
    )


def prompt_hook_payload(
    host: HookHost,
    raw_stdin: str,
    *,
    language: str = "auto",
    marker: str = HOOK_MARKER,
) -> Optional[dict]:
    """Return the host hook JSON for one prompt, or ``None`` when not routing.

    Both Claude Code and Codex accept the Claude-compatible
    ``hookSpecificOutput.additionalContext`` envelope for ``UserPromptSubmit``.
    """

    text = _prompt_text(raw_stdin)
    routing = inspect_prompt(text)
    if not routing.should_route or marker in text:
        return None
    context = build_prompt_context(
        routing.urls,
        host=host,
        language=resolve_prompt_language(text, language),
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }


def _prompt_text(raw_stdin: str) -> str:
    """Read the prompt from a host hook payload, with a plain-text fallback."""

    raw_stdin = raw_stdin or ""
    try:
        payload = json.loads(raw_stdin)
    except (TypeError, ValueError):
        return raw_stdin
    if not isinstance(payload, dict):
        return raw_stdin
    for key in ("prompt", "user_prompt", "message", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = [
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and isinstance(part.get("text"), str)
                    ]
                    if parts:
                        return "\n".join(parts)
    return raw_stdin


def run_prompt_hook(
    host: str, raw_stdin: str, *, language: Optional[str] = None
) -> int:
    """CLI entry used by plugin hook scripts. Always fails open."""

    import os

    canonical = normalize_host(host)
    if canonical is None:
        # An unknown host must never break the caller's prompt.
        print(json.dumps({"error": "unknown_host", "host": host}, ensure_ascii=False))
        return 1
    resolved_language = (
        language or os.getenv("ENVOY_HOOK_LANGUAGE", "auto").strip() or "auto"
    )
    payload = prompt_hook_payload(canonical, raw_stdin, language=resolved_language)
    if payload is not None:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


# --- Codex user-level hook installation -----------------------------------
#
# Codex 0.156 no longer loads plugin-supplied lifecycle hooks (the
# ``plugin_hooks`` feature is removed), but it does load ``$CODEX_HOME/hooks.json``.
# The Codex plugin still ships the canonical hook files and MCP server; this
# helper copies the wrapper next to the user config and merges the hook entry
# idempotently, preserving any hooks the user already has.

_CODEX_HOOK_SCRIPT_DIR = "envoy/hooks"


def _codex_hook_entry(script_dir) -> dict:
    import shlex

    sh = shlex.quote(str(script_dir / "envoy-hook.sh"))
    ps1 = str(script_dir / "envoy-hook.ps1")
    return {
        "hooks": [
            {
                "type": "command",
                "command": f"sh {sh} prompt --host codex",
                "commandWindows": (
                    'powershell -NoProfile -ExecutionPolicy Bypass '
                    f'-File "{ps1}" prompt --host codex'
                ),
                "timeout": 10,
                "statusMessage": "Checking links with Envoy",
            }
        ]
    }


def _is_envoy_codex_hook(hook: object) -> bool:
    if not isinstance(hook, dict):
        return False
    command = f"{hook.get('command', '')} {hook.get('commandWindows', '')}"
    return "envoy-hook" in command and "--host codex" in command


def _split_envoy_codex_entries(entries: list) -> tuple[list, bool]:
    """Drop only Envoy hook objects, preserving other hooks in the same entry."""

    kept: list = []
    removed = False
    for entry in entries:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        hook_list = entry.get("hooks")
        if not isinstance(hook_list, list):
            kept.append(entry)
            continue
        remaining = [hook for hook in hook_list if not _is_envoy_codex_hook(hook)]
        if len(remaining) == len(hook_list):
            kept.append(entry)
            continue
        removed = True
        if remaining:
            kept.append({**entry, "hooks": remaining})
    return kept, removed


def install_codex_hooks(*, home=None, uninstall: bool = False) -> dict:
    """Install or remove the Envoy ``UserPromptSubmit`` hook for Codex CLI.

    Writes ``$CODEX_HOME/hooks.json`` (default ``~/.codex/hooks.json``) and
    copies the POSIX/PowerShell wrappers to ``$CODEX_HOME/envoy/hooks``.
    Returns a machine-readable summary; never raises for missing files.
    """

    import os
    import shutil
    from pathlib import Path

    codex_home = Path(home).expanduser() if home else Path(
        os.getenv("CODEX_HOME") or (Path.home() / ".codex")
    ).expanduser()
    hooks_path = codex_home / "hooks.json"
    script_dir = codex_home / _CODEX_HOOK_SCRIPT_DIR
    result = {
        "host": "codex",
        "hooks_path": str(hooks_path),
        "script_dir": str(script_dir),
        "changed": False,
        "installed": False,
    }

    data: dict = {}
    if hooks_path.exists():
        raw = hooks_path.read_text(encoding="utf-8").strip()
        if raw:
            try:
                loaded = json.loads(raw)
            except ValueError as exc:
                result["error"] = f"invalid JSON in {hooks_path}: {exc}"
                return result
            if not isinstance(loaded, dict):
                result["error"] = f"{hooks_path} must contain a JSON object"
                return result
            data = loaded
    if "hooks" in data and not isinstance(data["hooks"], dict):
        result["error"] = f"{hooks_path} has a non-object 'hooks' value; refusing to modify it"
        return result
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    data["hooks"] = hooks
    if "UserPromptSubmit" in hooks and not isinstance(hooks["UserPromptSubmit"], list):
        result["error"] = (
            f"{hooks_path} has a non-list hooks.UserPromptSubmit value; refusing to modify it"
        )
        return result
    entries = hooks.get("UserPromptSubmit")
    if not isinstance(entries, list):
        entries = []
    kept, _ = _split_envoy_codex_entries(entries)

    if uninstall:
        hooks["UserPromptSubmit"] = kept
        if not kept:
            hooks.pop("UserPromptSubmit", None)
    else:
        source_dir = Path(__file__).with_name("hook_scripts")
        sh_source = source_dir / "envoy-hook.sh"
        if not sh_source.is_file():
            result["error"] = f"hook wrapper missing from package: {sh_source}"
            return result
        script_dir.mkdir(parents=True, exist_ok=True)
        for name in ("envoy-hook.sh", "envoy-hook.ps1"):
            src = source_dir / name
            if src.is_file():
                shutil.copyfile(src, script_dir / name)
        (script_dir / "envoy-hook.sh").chmod(0o755)
        kept.append(_codex_hook_entry(script_dir))
        hooks["UserPromptSubmit"] = kept
        result["installed"] = True

    if not hooks:
        data.pop("hooks", None)
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if uninstall and not data:
        if hooks_path.exists():
            hooks_path.unlink()
        result["changed"] = True
        return result
    previous = hooks_path.read_text(encoding="utf-8") if hooks_path.exists() else None
    if previous != rendered:
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text(rendered, encoding="utf-8")
        result["changed"] = True
    return result


# --- Hook installation status ---------------------------------------------

_DELIVERY = {
    "claude-code": "plugin",
    "codex": "user-config",
    "dsh": "plugin-bundle",
}

_VERIFY_COMMAND = {
    "claude-code": "claude plugin details envoy   # expect 'Hooks (1) UserPromptSubmit'",
    "codex": "codex   # then run /hooks and approve the Envoy entry once",
    "dsh": "dsh plugin list   # the envoy-dsh bundle provides agent/pre-step routing",
}

_TRUST_STATE = {
    "claude-code": "not-required",
    "codex": "unknown",
    "dsh": "not-required",
}


def _codex_home(home=None) -> "Path":
    if home:
        return Path(home).expanduser()
    return Path(
        os.getenv("CODEX_HOME") or (Path.home() / ".codex")
    ).expanduser()


def hook_status(host: str, *, home=None) -> dict:
    """Report whether the host hook is wired up. Read-only; never writes.

    Codex needs both a user-level ``hooks.json`` and a one-time ``/hooks``
    approval inside the CLI. The approval leaves no trace on disk, so ``trusted``
    stays ``unknown`` instead of being inferred from the file's presence.
    """

    import os

    canonical = normalize_host(host)
    if canonical is None:
        return {"host": host, "error": "unknown_host", "ok": False}
    report: dict = {
        "host": canonical,
        "delivery": _DELIVERY[canonical],
        "trusted": _TRUST_STATE[canonical],
        "verify": _VERIFY_COMMAND[canonical],
    }
    if canonical != "codex":
        # Plugin-supplied hooks are owned by the host bundle; claiming a state
        # the CLI cannot observe would be worse than reporting it as managed.
        report.update({"installed": None, "ok": None})
        return report

    codex_home = _codex_home(home)
    hooks_path = codex_home / "hooks.json"
    script_dir = codex_home / _CODEX_HOOK_SCRIPT_DIR
    report["config_path"] = str(hooks_path)
    if not hooks_path.is_file():
        report.update({
            "installed": False,
            "ok": False,
            "reason": "hooks.json is missing; run: envoy hook install --host codex",
        })
        return report
    try:
        data = json.loads(hooks_path.read_text(encoding="utf-8") or "{}")
    except ValueError as exc:
        report.update({"installed": False, "ok": False, "error": f"invalid JSON: {exc}"})
        return report

    entries: list = []
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if isinstance(hooks, dict) and isinstance(hooks.get("UserPromptSubmit"), list):
        entries = [entry for entry in hooks["UserPromptSubmit"] if isinstance(entry, dict)]
    envoy_entries = [
        entry
        for entry in entries
        if any(_is_envoy_codex_hook(hook) for hook in entry.get("hooks") or [])
    ]
    scripts = {}
    for name in ("envoy-hook.sh", "envoy-hook.ps1"):
        path = script_dir / name
        scripts[name] = {
            "path": str(path),
            "exists": path.is_file(),
            "executable": bool(name.endswith(".sh") and path.is_file() and os.access(path, os.X_OK)),
        }
    report["scripts"] = scripts
    report["installed"] = bool(envoy_entries)
    report["entries"] = len(envoy_entries)
    missing = [name for name, info in scripts.items() if not info["exists"]]
    if missing:
        report["ok"] = False
        report["reason"] = "hook wrapper missing: " + ", ".join(missing)
    elif not envoy_entries:
        report["ok"] = False
        report["reason"] = "no Envoy UserPromptSubmit entry; run: envoy hook install --host codex"
    else:
        report["ok"] = True
    return report


def render_hook_status(report: dict) -> str:
    """Render :func:`hook_status` for a human, one fact per line."""

    installed = report.get("installed")
    label = {True: "yes", False: "no", None: "host-managed"}[installed]
    lines = [
        f"host: {report.get('host', 'unknown')}",
        f"delivery: {report.get('delivery', 'unknown')}",
    ]
    if report.get("config_path"):
        lines.append(f"config: {report['config_path']}")
    lines.append(f"installed: {label}")
    if report.get("entries") is not None:
        lines.append(f"entries: {report['entries']}")
    lines.append(f"trusted: {report.get('trusted', 'unknown')}")
    if report.get("reason"):
        lines.append(f"reason: {report['reason']}")
    if report.get("error"):
        lines.append(f"error: {report['error']}")
    lines.append(f"verify: {report.get('verify', '')}")
    return "\n".join(lines)
