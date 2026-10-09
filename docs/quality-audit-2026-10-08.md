# EPG-DE / USA quality audit — 2026-10-08

Status: **100% qualification blocked. This branch is not a production release.**

The public release was downloaded and checked against all nine producer SHA-256
hashes. The manifest remained identical before and after the downloads. Baseline
main is 806b5a023587526cd1346519dd43e1b158b4fbc1. Measurement time is
2026-10-08T16:30:30Z. Counts include optional MASTER copies; they are not unique
physical sender counts.

| Public product | Sender entries | Programmes | Logo entries | Logo presence |
| --- | ---: | ---: | ---: | ---: |
| DE-MASTER | 854 | 142228 | 820 | 96.02% |
| DE-MAGENTA | 267 | 54213 | 242 | 90.64% |
| DE-SAMSUNG | 468 | 3369 | 133 | 28.42% |
| DE-PLUTO | 178 | 8213 | 74 | 41.57% |
| DE-AMAZON | 1 | 36 | 1 | 100.00% |
| USA-MASTER | 166 | 16960 | 165 | 99.40% |
| USA-LOCAL | 8947 | 423981 | 8683 | 97.05% |
| USA-FAST | 521 | 19547 | 444 | 85.22% |
| USA-SPORTS | 3346 | 156462 | 3345 | 99.97% |

The existing central audit passes with zero integrity errors and zero proven
cross-file identity violations. This result does not prove factual correctness
of every third-party programme, identity of every old logo, or future coverage.
The expanded quality audit measures 841 missing logo entries, 1608 overlaps,
763 gaps longer than six hours, 588 expired sender entries, 3827 warnings and
770 review findings. All 468 public Samsung schedules are expired at the
measurement time. Reviews include possible event channels and source-supplied
placeholder titles; they are not automatic deletion orders.

## Changes on this branch

- Extend the existing logo enrichment with exact native Samsung/Pluto country
  IDs and exact Magenta contentId/site_id joins. Use channel-logo image type 15,
  not programme art. Legacy USA .pluto joins additionally require both a matching
  ID stem and one unique US provider name.
- GET every proposed new asset. Reject HTML, invalid URLs, insecure redirects,
  unsupported images, empty SVGs, one-pixel PNGs and failed downloads. Preserve final URL and content hash.
  Leave unknown/ambiguous logos missing rather than using a placeholder.
- Correct 23 exact Magenta source identities using current contentId/name pairs
  and a unique active German catalog identity: DF1, Motorvision+ and renamed
  or renumbered Sky Sport/Bundesliga feeds. Other candidates, including the real
  Austrian ServusTV source, remain in their original source groups.
- Verify and, where necessary, refresh existing native-provider logos only with
  both the exact native ID and the matching current provider name. Preserve old
  icon URLs in the correction evidence. This corrects stale branding such as
  Sony Channel artwork for the source-confirmed AXN White identity.
- Preserve the country component of catalog IDs during identity reconciliation.
  Generic HD/SD suffixes cannot bridge German, Austrian and Swiss native feeds.
  Country-equivalent copies can still merge with conservation evidence.
- Stream large XMLTV inputs. Check unchanged sender IDs and the complete ordered
  programme XML digest around logo-only enrichment and gzip output.
- Refresh native provider schedules in the existing platform exporter. Validate
  all incoming intervals, titles and future coverage before using them. Preserve
  complementary history and full superseded originals as JSONL alternatives.
  Skip stale or overlapping source schedules rather than fabricating a repair.
- Update DE every three hours because the Samsung native source supplies roughly
  six hours. Measure its documented minimum future horizon separately (3 hours).
- Measure quality on final reconciled outputs and retain per-channel CSV reports,
  logo provenance and complete schedule alternatives in Actions artifacts.
  The central release manifest now includes final logo-presence counts.
- Distinguish integrity success, logo presence, asset verification and unresolved
  source/identity review. Strict qualification does not pass unresolved evidence.

The first candidate experiment adds 484 DE logo entries and 32 USA FAST entries,
with unchanged sender IDs and programme digests for logo-only changes. Samsung
logo presence becomes 468/468; native schedule refresh succeeds for all 468
existing Samsung IDs. These numbers describe a local candidate, not live Pages.
325 missing logo entries still remain across the nine candidate files.

## Release gates

The PR must remain draft until regression tests, the pinned production snapshot,
artifact roundtrip, and both live branch producers pass. The remaining quality
findings must be resolved using concrete source evidence before a 100% claim.
After merge, both matching main producer artifacts and public byte verification
are still required. Existing Pages stays LKG during incompatible or failed builds.
No main EPG bytes are directly rewritten by this branch.

Regression coverage comprises 53 tests, including native rebranding, unsuccessful
asset replacement, source-specific Sky renumbering, preservation of Austrian
ServusTV and repeated reconciliation of DE/AT/CH variants. On the first branch
commit, both regression runs, the pinned snapshot and the Actions artifact
roundtrip pass; the USA live producer passes with 12984 sender entries and
632724 programmes. Its four final files still have 279 missing logo entries.
These earlier-head results do not validate subsequent changes: the final branch
head requires new producer and regression runs before integration.

## Reproduce

Run python -m unittest discover -s tests -v. Run the existing
scripts/validate_release_snapshot.py with a new --output directory. For the
actual public files, run scripts/audit_published_epgs.py with the USA mapping,
then scripts/audit_epg_quality.py --xml followed by all nine downloaded XMLTV
paths, --now 2026-10-08T16:30:30+00:00, --report, --summary, --channel-report
and --strict. The expected baseline qualification result is blocked.

No new Python package dependencies, placeholder assets or synthetic programmes
are introduced. Existing source-data rights remain with the original providers.
