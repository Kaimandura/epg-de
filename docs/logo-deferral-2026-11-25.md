# Temporary logo deferral — 2026-11-25

The repository owner explicitly deferred missing/unavailable logos so that cosmetic image faults no longer prevent fresh EPG publication. This supersedes the earlier requirement that every logo availability fault block this repair.

Both the PR snapshot and production Pages workflow opt into `--allow-logo-gaps`. Audits continue to run and preserve all missing/failed image records. Their report status is `passed_with_logo_warnings` when image availability is the only problem; they do not falsely report all logos healthy. Without the option, the existing strict behavior and historical USA ceilings remain unchanged.

Missing feeds, XMLTV corruption, orphan references, invalid dates/durations, critical channels, unsafe logo URLs/redirects, provider identity, artifact provenance/hashes and final public-byte verification remain enforced. No broad `continue-on-error` is used. No programmes, channel IDs, logo URLs or existing quality limits are changed by this policy update.

Validation: all 79 tests passed, including new tests proving that missing images and network failures are warnings while missing feeds, orphan programmes and unsafe URLs still fail. Real CI artifact roundtrip and public deployment are still required. Existing Samsung/Silverline coverage gates may expose separate source-data problems; this change does not claim those repaired.
