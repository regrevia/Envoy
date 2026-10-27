"""Tests for ``scripts/acceptance/link_coverage.py``.

The script is the measurement instrument for link compatibility, so the things
tested here are the ones that would silently poison a reported number:

- a status string must never be counted as delivered text (``result`` is not
  content length);
- a dead link must leave the denominator, while a 403 / risk-control answer
  must not;
- the "with risk-control" and "without risk-control" denominators must be
  computed from the same rows and stay apart;
- the precheck must try Envoy's egress (direct, then the policy proxy) before
  calling a link dead or unreachable.

No test touches the network or launches ``envoy``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "acceptance" / "link_coverage.py"


@pytest.fixture(scope="module")
def lc():
    spec = importlib.util.spec_from_file_location("link_coverage_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_entry(lc, family="web", label="one", verdict="readable", seconds=1.0,
               risk_host=False, dead=False, url=None, status="ready"):
    entry = lc.Entries(family=family, label=label, url=url or f"https://example.com/{label}", risk_host=risk_host)
    entry.dead = dead
    if verdict:
        entry.runs = [{"verdict": verdict, "status": status, "seconds": seconds, "chars": 300}]
    return entry


def text_response(result="ready", body="x" * 300, total_chars=None, state="", errors=None):
    response = {
        "result": result,
        "execution_state": state,
        "blocks": [{"content": {"text": body}}],
        "errors": errors or [],
        "coverage": {"modalities": {"text": {"details": {"total_chars": total_chars if total_chars is not None else len(body)}}}},
    }
    return response


# -- classify ---------------------------------------------------------------


def test_classify_counts_delivered_text_not_the_status_word(lc):
    outcome = lc.classify(text_response("ready", "y" * 250))
    assert outcome["verdict"] == "readable"
    assert outcome["chars"] == 250


def test_classify_ready_but_short_is_empty(lc):
    assert lc.classify(text_response("ready", ""))["verdict"] == "empty"


def test_classify_partial_needs_text_to_be_useful(lc):
    assert lc.classify(text_response("partial", "z" * 400))["verdict"] == "partial"
    assert lc.classify(text_response("partial", "z"))["verdict"] == "empty"


def test_classify_truncated_when_page_extracted_but_body_withheld(lc):
    outcome = lc.classify(text_response("ready", "tiny", total_chars=12000))
    assert outcome["verdict"] == "truncated"


def test_classify_blocked_for_host_and_action_states(lc):
    assert lc.classify(text_response("blocked", ""))["verdict"] == "blocked"
    assert lc.classify(text_response("needs_host", ""))["verdict"] == "blocked"
    assert lc.classify(text_response("", state="waiting_for_host"))["verdict"] == "blocked"


def test_classify_unknown_result_is_failed_not_readable(lc):
    assert lc.classify(text_response("", ""))["verdict"] == "failed"


def test_classify_egress_failure_is_unreachable_not_failed(lc):
    response = text_response("", "", errors=[{"code": "read_failed", "message": "net::ERR_PROXY_CONNECTION_FAILED"}])
    assert lc.classify(response)["verdict"] == "unreachable"


def test_classify_challenge_page_stays_blocked(lc):
    response = text_response("blocked", "", errors=[{"code": "blocked", "message": "captcha required"}])
    assert lc.classify(response)["verdict"] == "blocked"


# -- precheck ---------------------------------------------------------------


def probe(code, dead=False, final="https://example.com/", note=""):
    return {"code": code, "final_url": final, "note": note, "dead": dead}


def test_precheck_falls_back_to_policy_proxy(lc, monkeypatch):
    monkeypatch.setattr(lc, "detected_proxy", lambda: "http://127.0.0.1:7897")
    calls = []

    def fake(url, timeout, proxy):
        calls.append(proxy)
        return probe("000", dead=False) if proxy is None else probe("200")

    monkeypatch.setattr(lc, "_http_probe_once", fake)
    result = lc.precheck_http("https://blocked.example.com/a")
    assert result["status"] == "ok"
    assert result["via"] == "proxy"
    assert calls == [None, "http://127.0.0.1:7897"]


def test_precheck_dead_link_stops_after_first_answer(lc, monkeypatch):
    monkeypatch.setattr(lc, "detected_proxy", lambda: "http://127.0.0.1:7897")
    calls = []

    def fake(url, timeout, proxy):
        calls.append(proxy)
        return probe("404", dead=True)

    monkeypatch.setattr(lc, "_http_probe_once", fake)
    result = lc.precheck_http("https://example.com/gone")
    assert result["status"] == "dead"
    assert result["code"] == "404"
    assert calls == [None]


def test_precheck_unreachable_when_no_attempt_answers(lc, monkeypatch):
    monkeypatch.setattr(lc, "detected_proxy", lambda: "http://127.0.0.1:7897")
    monkeypatch.setattr(lc, "_http_probe_once", lambda url, timeout, proxy: probe("000"))
    result = lc.precheck_http("https://void.example/")
    assert result["status"] == "unreachable"
    assert len(result["attempts"]) == 2


def test_precheck_403_is_an_answer_not_a_dead_link(lc, monkeypatch):
    monkeypatch.setattr(lc, "detected_proxy", lambda: None)
    monkeypatch.setattr(lc, "_http_probe_once", lambda url, timeout, proxy: probe("403"))
    result = lc.precheck_http("https://www.zhihu.com/question/1")
    assert result["status"] == "ok"
    assert result["code"] == "403"


def test_precheck_dns_failure_is_retried_then_handed_to_the_proxy(lc, monkeypatch):
    """A resolver hiccup must not delete a live host from the denominator."""
    monkeypatch.setattr(lc, "detected_proxy", lambda: "http://127.0.0.1:7897")
    calls = []

    def fake(url, timeout, proxy):
        calls.append(proxy)
        if proxy is None:
            return probe("000", dead=True, note="nodename nor servname provided")
        return probe("200")

    monkeypatch.setattr(lc, "_http_probe_once", fake)
    result = lc.precheck_http("https://36kr.com/")
    assert result["status"] == "ok"
    assert result["via"] == "proxy"
    assert calls == [None, None, "http://127.0.0.1:7897"]


def test_precheck_dns_failure_on_every_hop_is_unreachable_not_dead(lc, monkeypatch):
    monkeypatch.setattr(lc, "detected_proxy", lambda: "http://127.0.0.1:7897")

    def fake(url, timeout, proxy):
        return probe("000", dead=True, note="nodename nor servname provided")

    monkeypatch.setattr(lc, "_http_probe_once", fake)
    result = lc.precheck_http("https://archive.org/details/nasa")
    assert result["status"] == "unreachable"
    assert [a["via"] for a in result["attempts"]] == ["direct", "proxy"]


# -- denominators -----------------------------------------------------------


def test_scope_summary_excludes_dead_and_splits_risk(lc):
    entries = [
        make_entry(lc, label="readable", verdict="readable", seconds=1.0),
        make_entry(lc, label="partial", verdict="partial", seconds=2.0),
        make_entry(lc, label="blocked", verdict="blocked", seconds=3.0),
        make_entry(lc, label="unreachable", verdict="unreachable", seconds=4.0),
        make_entry(lc, label="risky", verdict="readable", seconds=5.0, risk_host=True),
        make_entry(lc, label="dead", verdict=None, dead=True),
    ]
    all_scope = lc.scope_summary(entries, "all")
    no_risk = lc.scope_summary(entries, "no_risk")
    assert all_scope["denominator"] == 5
    assert no_risk["denominator"] == 4
    assert all_scope["usable"] == 3
    assert no_risk["usable"] == 2
    assert no_risk["usable_rate"] == 0.5
    # unreachable is not a readability denominator: 1 readable of 3 that answered
    assert no_risk["reachable_denominator"] == 3
    assert no_risk["reachable_readable_rate"] == 0.333
    assert all_scope["blocked"] == 1 and no_risk["blocked"] == 1


def test_scope_summary_latency_uses_mean_p50_p90(lc):
    latencies = [1.0, 2.0, 3.0, 10.0]
    entries = [
        make_entry(lc, label=f"l{index}", seconds=value)
        for index, value in enumerate(latencies)
    ]
    latency = lc.scope_summary(entries, "all")["latency"]
    assert latency["n"] == 4
    assert latency["mean"] == 4.0
    assert latency["p50"] == pytest.approx(2.5)
    assert latency["p90"] > 7.0


def test_entries_without_runs_count_as_failed_not_readable(lc):
    entry = make_entry(lc, verdict=None)
    summary = lc.scope_summary([entry], "all")
    assert summary["failed"] == 1
    assert summary["usable"] == 0


# -- jitter -----------------------------------------------------------------


def test_jitter_reports_verdict_flips(lc):
    steady = make_entry(lc, label="steady", verdict="readable", seconds=1.0)
    steady.runs.append({"verdict": "readable", "status": "ready", "seconds": 1.2})
    flaky = make_entry(lc, label="flaky", verdict="readable", seconds=2.0)
    flaky.runs.append({"verdict": "blocked", "status": "blocked", "seconds": 2.5})
    report = lc.jitter_report([steady, flaky], 2)
    assert report["verdict_flip_count"] == 1
    assert report["verdict_flips"][0]["label"] == "flaky"
    assert report["latency_delta"]["mean"] == pytest.approx(0.35)


# -- corpus -----------------------------------------------------------------


def test_corpus_rows_are_well_formed_and_unique(lc):
    labels = [(family, label) for family, label, _ in lc.CORPUS]
    assert len(labels) == len(set(labels)), "duplicate (family, label)"
    # a URL may appear under two families on purpose (the platform family
    # re-lists the canonical content pages); the runner dedupes by URL.
    per_family = [(family, url) for family, _, url in lc.CORPUS]
    assert len(per_family) == len(set(per_family)), "duplicate URL inside one family"
    assert len({url for _, _, url in lc.CORPUS}) >= 70
    for family, label, url in lc.CORPUS:
        assert url.startswith("http"), (family, label)
        assert family in lc.FAMILY_ORDER, (family, label)


def test_cn_platform_family_holds_the_adaptation_targets(lc):
    by_label = {label: url for family, label, url in lc.CORPUS if family == "cn-platform"}
    assert "douyin-video" in by_label
    assert "douyin-short" in by_label
    assert "kuaishou-share" in by_label
    assert "xiaoyuzhou-episode" in by_label
    assert "douban-movie" in by_label
    assert {"podcast-rss-ifanr", "podcast-rss-sspai", "podcast-rss-36kr"} <= set(by_label)


def test_corpus_has_no_xiaohongshu_link(lc):
    assert not any("xiaohongshu" in url for _, _, url in lc.CORPUS)


def test_every_platform_required_by_the_brief_is_covered(lc):
    families = {family for family, _, _ in lc.CORPUS}
    assert {"platforms", "cn-platform"} <= families
    labels = {label for _, label, _ in lc.CORPUS}
    assert {"youtube-watch", "bilibili-video", "b23-bv", "weibo-profile", "zhihu-question"} <= labels


# -- request / selection ----------------------------------------------------


def test_build_request_carries_the_fixed_budget(lc):
    request = lc.build_request("youtube-watch", "https://www.youtube.com/watch?v=1", 0)
    assert request["protocol_version"] == "envoy.read/1"
    assert request["budget"]["max_work_seconds"] == 120
    assert request["budget"]["max_response_chars"] == 8000
    assert request["request_id"].startswith("coverage-youtube-watch")


def test_risk_hosts_are_skipped_unless_asked_for(lc):
    class Args:
        only = ""
        include_risk_hosts = False
        skip_hosts = ",".join(lc.RISK_HOSTS)

    entries, skipped = lc.select_entries(Args())
    assert any(item.risk_host for item in skipped)
    assert not any(item.risk_host for item in entries)

    class Include(Args):
        include_risk_hosts = True

    entries, skipped = lc.select_entries(Include())
    assert not skipped
    assert any(item.risk_host for item in entries)


def test_report_keeps_both_denominators_and_dead_links(lc):
    entries = [
        make_entry(lc, label="ok", verdict="readable"),
        make_entry(lc, label="gone", verdict=None, dead=True),
        make_entry(lc, label="risky", verdict="blocked", risk_host=True),
    ]
    entries[1].precheck = {"status": "dead", "code": "410"}

    class Args:
        envoy = "envoy"
        jobs = 2
        timeout = 180.0
        repeats = 1
        include_risk_hosts = True

    report = lc.build_report(entries, Args(), "envoy 0.1.14", "http://127.0.0.1:7897")
    assert report["corpus_total"] == 3
    assert report["summary"]["dead"] == 1
    assert report["summary"]["all"]["denominator"] == 2
    assert report["summary"]["no_risk"]["denominator"] == 1
    rendered = lc.render(entries, report)
    assert "no_risk" in rendered
    assert "410" in rendered
    assert json.dumps(report)
