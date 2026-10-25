# Scope Deviation: 20 packages instead of 25

The original design asked for 25 packages, five per stratum, including a
Xiaohongshu layer, at least 50 unique public URLs, and at least two sources per
package. The confirmatory set that was actually frozen and run is **20 packages,
five each over bilibili, douyin, wechat and cross_platform, 40 unique public
URLs**. This file records why, what was measured rather than assumed, and what
would be required to close the gap. No source, fact or number in this benchmark
is invented to make the original shape fit.

## 1. Xiaohongshu cannot supply verbatim-checkable evidence (measured)

Three access paths were tried on real note URLs; see `XHS-ACCESS-FINDINGS.md`
for the full probe records:

| Path | Result |
|---|---|
| Share short link, anonymous | `blocked`, 0 characters |
| Note URL with `?xsec_token`, guest render | `partial`, 1368 characters — title and page footer only |
| Note URL with a warm browser profile | `partial`, 9668 characters, 59.4 s, landed on the home feed |

None of the three returned note body text. The body is provable locally with a
direct Playwright read of `#detail-desc` (author `W学长聊求职`), but that is a
different retriever than the one under test, so using it as benchmark evidence
would measure the fallback rather than Envoy. A package needs at least two
sources whose reference facts are literal substrings of a frozen snapshot; with
zero usable note bodies, the Xiaohongshu stratum cannot meet the freeze gate at
all. It is excluded rather than filled with reconstructed content.

## 2. What was substituted

The five strata became four, and the removed Xiaohongshu slots were not
silently redistributed: the frozen set keeps five packages per remaining
stratum, so per-stratum evidence stays balanced (5 × 4 = 20) instead of biasing
one platform. `source-intake.csv` holds exactly 40 rows, one per source slot,
all `accepted`, all with a frozen content-addressed snapshot.

## 3. Why 40 URLs, not 50

The two-source rule is hard: every package needs at least two distinct public
URLs with independently reviewed facts. 20 packages × 2 = 40. Reaching 50 would
require ten more accepted sources, and the intake ledger has no tenth pair for
any stratum that also passes the freeze gate:

- Douyin exposes page-level captions and chapter markers but no transcript for
  the remaining candidate URLs, so a second distinct URL per package could not
  be captured with a checkable body.
- Bilibili capture failed at the transport layer for two additional science
  videos (`SSL: UNEXPECTED_EOF_WHILE_READING`, handshake timeout); that is a
  transport failure, not a content verdict, and it leaves no snapshot to freeze.
- WeChat candidate expansion articles were used up by the existing five
  packages per stratum.

The honest alternative to 50 thin URLs is 40 URLs that each pass the same
freeze gate. Padding the set with single-source packages was explicitly
rejected.

## 4. Consequence for the reported results

- Every claim in the A/B report is scoped to 20 packages / 40 URLs / 4 strata /
  60 turns; nothing is extrapolated to Xiaohongshu.
- The 5-package-per-stratum balance is preserved, so per-stratum comparisons
  remain internally valid.
- To reach the original 25 / 50 / 5-stratum shape, the missing work is
  concrete: obtain at least ten more public URLs that survive capture with a
  snapshottable body (most plausibly by adding a Xiaohongshu-capable retrieval
  path or by finding ten more Douyin/Bilibili sources), then re-freeze the
  manifest and run a fresh batch. That is a v1.1 scope, not something that can
  be patched into an already-frozen set without invalidating the run.
