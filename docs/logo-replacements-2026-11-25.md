# Verified logo replacements — 2026-11-25

46 exact channel overrides replace broken image URLs using 38 distinct assets. All 38 passed the existing HTTPS/content-type/image-signature audit on 2026-11-25. Sources and response evidence are recorded in logo-replacements-2026-11-25.json: official SiriusXM channel metadata, native Pluto/Samsung channel inventories, a Fubo station asset and pinned tv-logo NBA League Pass/BET Jams assets.

Validation: all 77 existing unit tests passed. A separate exercise applied all 46 overrides together and verified unchanged programme attributes/metadata, unchanged unrelated channel, and gzip roundtrip. No published programme files or policy limits were changed.

This is a partial repair, not a passed release. Remaining failures include source-specific event IDs whose precise identity is not established by similar brand names. Existing logo gates and debt limits remain unchanged. CI, exact artifact roundtrip and public-byte verification must pass before release.

Additional user sources checked: iptv-org/epg is already used by the producer; the supplied kodi-unlimited-support.de/iptv/epg/epg.xml.gz returned HTTP 404. Open-EPG country feeds were readable; no safe alternate image matches for the remaining affected IDs were found. Its Germany feed contains SilverlineTV.de and SilverlineTVCatchUp.de, 71 programmes combined, latest stop 20261126082000 +0000. This is a fallback candidate only, not verified continuous coverage of the regular Silverline service. No external feeds were added to publishing.
