"""Regression tests for the honesty fixes in the fixed-set A/B harness.

These pin the rules the README now states, so the claim "a citation means the
article, not the host" cannot silently decay back to substring matching.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ab_grade  # noqa: E402
import ab_report  # noqa: E402
import outcome_classes  # noqa: E402
import run_ab  # noqa: E402

WX_A = "https://mp.weixin.qq.com/s/AAAaaaBBBbbbCCCCccc"
WX_B = "https://mp.weixin.qq.com/s/DDDdddEEEeeeFFFFfff"
WX_ID_A = "https://mp.weixin.qq.com/s?__biz=MzI5&mid=2247483&idx=1&sn=abc123&scene=21"
WX_ID_A_SHARE = (
    "https://mp.weixin.qq.com/s?sn=abc123&idx=1&mid=2247483&__biz=MzI5&from=timeline"
)
WX_ID_B = "https://mp.weixin.qq.com/s?__biz=MzI5&mid=2247483&idx=1&sn=def456"


def source(source_id: str, url: str) -> dict:
    return {"source_id": source_id, "url": url}


class CanonicalArticleTests(unittest.TestCase):
    def test_scheme_www_and_trailing_slash_are_normalized(self):
        self.assertEqual(
            ab_grade.canonical_article("http://www.mp.weixin.qq.com/s/AAAaaaBBBbbbCCCCccc/"),
            ab_grade.canonical_article(WX_A),
        )

    def test_tracking_parameters_do_not_change_identity(self):
        self.assertEqual(
            ab_grade.canonical_article(WX_A + "?utm_source=share&from=timeline&scene=21"),
            ab_grade.canonical_article(WX_A),
        )

    def test_wechat_query_identity_keeps_article_and_drops_share_noise(self):
        self.assertEqual(
            ab_grade.canonical_article(WX_ID_A_SHARE), ab_grade.canonical_article(WX_ID_A)
        )

    def test_wechat_different_sn_is_a_different_article(self):
        self.assertNotEqual(
            ab_grade.canonical_article(WX_ID_A), ab_grade.canonical_article(WX_ID_B)
        )

    def test_bare_host_is_not_an_article(self):
        self.assertNotEqual(ab_grade.canonical_article("mp.weixin.qq.com"), ab_grade.canonical_article(WX_A))
        self.assertNotEqual(ab_grade.canonical_article("https://mp.weixin.qq.com/"), ab_grade.canonical_article(WX_ID_A))

    def test_bilibili_video_id_is_part_of_identity(self):
        a = ab_grade.canonical_article("https://www.bilibili.com/video/BV1xx411c7mD")
        b = ab_grade.canonical_article("https://bilibili.com/video/BV1yy411c7mE")
        self.assertNotEqual(a, b)
        self.assertEqual(
            ab_grade.canonical_article("https://bilibili.com/video/BV1xx411c7mD/"),
            ab_grade.canonical_article("https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333"),
        )

    def test_urls_are_found_inside_chinese_sentences(self):
        answer = f"来源（公众号）：{WX_A}，另见 mp.weixin.qq.com/s/DDDdddEEEeeeFFFFfff 一文"
        keys = ab_grade.cited_article_keys(answer)
        self.assertIn(ab_grade.canonical_article(WX_A), keys)
        self.assertIn(ab_grade.canonical_article(WX_B), keys)


class CitationTests(unittest.TestCase):
    def test_host_only_mention_does_not_score_any_article(self):
        sources = [source("s1", WX_A), source("s2", WX_B)]
        answer = "我在 mp.weixin.qq.com 上看到过相关讨论，但没记具体文章。"
        self.assertEqual(ab_grade.citation_hits(sources, answer), [])
        # The old host rule is kept as a diagnostic so the gap is measurable.
        self.assertEqual(ab_grade.legacy_host_mentions(sources, answer), ["s1", "s2"])

    def test_exact_article_scores_only_that_article(self):
        sources = [source("s1", WX_A), source("s2", WX_B)]
        answer = f"参考：{WX_A}?utm_medium=social"
        self.assertEqual(ab_grade.citation_hits(sources, answer), ["s1"])

    def test_share_link_matches_its_own_article_only(self):
        sources = [source("s1", WX_ID_A), source("s2", WX_ID_B)]
        answer = f"文章链接：{WX_ID_A_SHARE}"
        self.assertEqual(ab_grade.citation_hits(sources, answer), ["s1"])

    def test_shared_host_articles_do_not_credit_each_other(self):
        sources = [source("s1", WX_A), source("s2", WX_B)]
        answer = f"来源：{WX_A}"
        hits = ab_grade.citation_hits(sources, answer)
        self.assertEqual(hits, ["s1"])
        self.assertNotIn("s2", hits)

    def test_attribution_needs_title_author_or_exact_article(self):
        hints = [{"title": "某篇文章标题的前十二个字足够长", "author": "作者甲", "url": WX_A}]
        self.assertEqual(ab_grade.attribution_hits(hints, "mp.weixin.qq.com"), [])
        self.assertEqual(ab_grade.attribution_hits(hints, f"见 {WX_A}"), [1])
        self.assertEqual(ab_grade.attribution_hits(hints, "作者甲 的文章"), [1])


class ArmOrderTests(unittest.TestCase):
    def test_first_arm_decides_execution_order(self):
        item = {"package_id": "p", "arms": ["baseline", "envoy"], "first_arm": "envoy"}
        self.assertEqual(run_ab.ordered_arms(item), ["envoy", "baseline"])

    def test_single_arm_filtered_plan_is_run_as_is(self):
        item = {"package_id": "p", "arms": ["envoy"], "first_arm": "baseline"}
        self.assertEqual(run_ab.ordered_arms(item), ["envoy"])

    def test_inconsistent_plan_is_detected(self):
        packages = [
            {"package_id": "p1", "arms": ["baseline", "envoy"], "first_arm": "envoy"},
            {"package_id": "p2", "arms": ["envoy", "baseline"], "first_arm": "envoy"},
            {"package_id": "p3", "arms": ["envoy"], "first_arm": "baseline"},
            {"package_id": "p4", "arms": ["baseline", "envoy"], "first_arm": "envoy"},
        ]
        problems = run_ab.plan_order_problems(packages)
        self.assertEqual(len(problems), 2)
        self.assertIn("p1", problems[0])
        self.assertIn("p4", problems[1])

    def test_committed_plan_declares_a_consistent_order(self):
        plan = json.loads((HERE / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(run_ab.plan_order_problems(plan["packages"]), [])
        firsts = [item["first_arm"] for item in plan["packages"]]
        self.assertEqual(firsts.count("baseline"), 10)
        self.assertEqual(firsts.count("envoy"), 10)


class OutcomeClassTests(unittest.TestCase):
    long_answer = "这里是一段足够长的答案文本，用来排除短输出规则。" * 20

    def test_completed_looking_answer_is_only_suspect_not_verified(self):
        label, _hit = outcome_classes.classify({"answer": self.long_answer, "exit_code": 0})
        self.assertEqual(label, "suspect_complete")
        self.assertNotIn("delivered", outcome_classes.CLASSES)

    def test_timeout_and_no_output_are_separate(self):
        self.assertEqual(outcome_classes.classify({"answer": "", "exit_code": 124})[0], "timed_out")
        self.assertEqual(outcome_classes.classify({"answer": "", "exit_code": 0})[0], "no_output")

    def test_login_request_is_needs_action(self):
        answer = "请先登录后再继续。" + self.long_answer
        self.assertEqual(outcome_classes.classify({"answer": answer, "exit_code": 0})[0], "needs_action")

    def test_verified_requires_reviewer_verdict(self):
        row = {"package_id": "wechat-01", "arm": "envoy", "role": "initial"}
        self.assertFalse(outcome_classes._verified({}, row))
        review = {f"{row['package_id']}-{row['role']}": {"answer_correct": True}}
        self.assertTrue(outcome_classes._verified(review, row))
        self.assertFalse(
            outcome_classes._verified(
                {f"{row['package_id']}-{row['role']}": {"answer_correct": None}}, row
            )
        )

    def test_baseline_verdict_uses_its_own_field(self):
        row = {"package_id": "wechat-01", "arm": "baseline", "role": "initial"}
        review = {f"{row['package_id']}-{row['role']}": {"answer_correct": True,
                                                          "baseline_answer_correct": False}}
        self.assertFalse(outcome_classes._verified(review, row))


class ReadmeOrderClaimTests(unittest.TestCase):
    """The docs must describe the executed shards, not the intended interleaving."""

    ROOT = Path(__file__).resolve().parents[2]

    def read(self, rel: str) -> str:
        return (self.ROOT / rel).read_text(encoding="utf-8").lower()

    def test_readmes_do_not_claim_randomized_order(self):
        for rel in ("benchmarks/README.md", "benchmarks/fixed-set-v1/README.md"):
            text = self.read(rel)
            self.assertNotIn("randomized", text, rel)
            self.assertNotIn("randomised", text, rel)

    def test_fixed_set_readme_states_single_arm_shards_and_no_backfill(self):
        text = self.read("benchmarks/fixed-set-v1/README.md")
        self.assertIn("single-arm shard", text)
        self.assertIn("not retroactive", text)
        self.assertIn("first_arm", text)


class ChildEnvTests(unittest.TestCase):
    """Benchmark runs must never open a visible browser window."""

    def test_child_env_forces_headless_browser(self):
        env = run_ab.child_env(Path("/tmp/ab-profile-fixture"), "token", "http://gateway", with_envoy=False)
        self.assertEqual(env["ENVOY_VISIBLE_BROWSER"], "0")

    def test_inherited_visible_browser_does_not_leak_into_child(self):
        with mock.patch.dict(os.environ, {"ENVOY_VISIBLE_BROWSER": "1"}):
            env = run_ab.child_env(Path("/tmp/ab-profile-fixture"), "token", "http://gateway", with_envoy=False)
        self.assertEqual(env["ENVOY_VISIBLE_BROWSER"], "0")


class GradeAnswerSourceTests(unittest.TestCase):
    """Recovered transcript text is diagnosis, not a graded answer, by default."""

    def run_grade(self, *args: str) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "grade.jsonl"
            subprocess.run(
                [sys.executable, str(HERE / "ab_grade.py"), *args, "--out", str(out)],
                check=True,
                capture_output=True,
                text=True,
            )
            return [
                json.loads(line)
                for line in out.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

    def fixture(self, tmp: Path) -> tuple[str, str]:
        terminal = tmp / "answers.jsonl"
        recovered = tmp / "answers-recovered.jsonl"
        base = {"run_id": "fixture", "package_id": "bili-01", "role": "initial", "is_error": False}
        terminal.write_text(
            "\n".join(
                [
                    json.dumps({**base, "arm": "baseline", "turn_correlation_id": "t1", "answer": "短答。"}),
                    json.dumps({**base, "arm": "envoy", "turn_correlation_id": "t2", "answer": "另一短答。"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        recovered.write_text(
            json.dumps(
                {
                    **base,
                    "arm": "baseline",
                    "turn_correlation_id": "t1",
                    "answer": "从 transcript 回收的长答案 " * 20,
                    "answer_source": "transcript_last_assistant_text",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return str(terminal), str(recovered)

    def test_every_grade_row_carries_answer_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            terminal, _ = self.fixture(Path(tmp))
            rows = self.run_grade("--answers", terminal)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["answer_source"] for row in rows}, {"terminal"})
        for row in rows:
            self.assertEqual(row["answer_chars_source"], row["answer_chars"])

    def test_recovered_text_is_not_graded_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            terminal, recovered = self.fixture(Path(tmp))
            default_rows = self.run_grade("--answers", terminal, "--answers", recovered)
            recovered_only = self.run_grade("--answers", recovered)
            included = self.run_grade("--answers", terminal, "--answers", recovered, "--include-recovered")
        self.assertEqual({row["answer_source"] for row in default_rows}, {"terminal"})
        self.assertEqual(
            next(row for row in default_rows if row["arm"] == "baseline")["answer_chars"], len("短答。")
        )
        self.assertEqual(recovered_only, [])
        baseline = next(row for row in included if row["arm"] == "baseline")
        self.assertEqual(baseline["answer_source"], "transcript_last_assistant_text")
        self.assertGreater(baseline["answer_chars"], 100)


class ReportTemplateTests(unittest.TestCase):
    """Reports may show host attribution only as a diagnosis, never as citation."""

    ROOT_FILES = ("ab_report.py", "four_questions.py")

    def sources(self) -> dict[str, str]:
        return {
            name: (HERE / name).read_text(encoding="utf-8") for name in self.ROOT_FILES
        }

    def test_host_attribution_is_labelled_as_diagnosis(self):
        for name, text in self.sources().items():
            self.assertNotIn("归属来源命中", text, name)
            self.assertIn("host 级归属（诊断）", text, name)

    def test_citation_metric_is_citation_hits(self):
        for name, text in self.sources().items():
            self.assertIn("文章级引用命中", text, name)
            self.assertIn("citation_hits", text, name)

    def test_report_marks_fact_threshold_and_repeated_total(self):
        text = self.sources()["ab_report.py"]
        self.assertIn("≥0.6", text)
        self.assertIn("516", text)

    def test_token_comparison_is_a_multiplier_not_a_negative_percentage(self):
        line = ab_report.paired_input_sentence(
            {
                "paired_completed": 19,
                "paired_input_tokens": {"baseline": 2904111, "envoy": 17422152},
                "paired_input_ratio": 6.0,
            }
        )
        self.assertIn("6.0 倍", line)
        self.assertIn("envoy 更贵", line)
        self.assertNotRegex(line, r"变化 -\d|-\d+(\.\d+)?%")
        insufficient = ab_report.paired_input_sentence({"paired_completed": 0})
        self.assertIn("样本不足，不计倍数", insufficient)
        self.assertNotIn("%", insufficient)
        for name, text in self.sources().items():
            self.assertNotRegex(text, r"变化 -\d", name)


if __name__ == "__main__":
    unittest.main()
