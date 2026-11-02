# Sky Cinema source repair — 2026-11-02

Official naming evidence: https://www.sky.de/hilfe/abonnement/programm/deine-neuen-sky-cinema-sender confirms that on 2026-05-19 Highlights became Blockbuster and Family became Feelgood. Best Of is not the current name.

Upstream https://github.com/iptv-org/epg/blob/master/sites/sky.com/sky.com.channels.xml contains DE#330/331/332/333 with the exact German HD and HD +24 names but empty XMLTV IDs. The collector cannot resolve these by a unique existing name. Four explicit overrides now include those sources without copying or inventing schedules. Expected source names guard against reassigned service IDs.

Live source verification on 2026-11-02: https://awk.epgsky.com/hawk/linear/schedule/20261102/330,331,332,333 with X-SkyOTT-Territory: DE returned respectively 14,15,14,15 events. Raw response SHA256: d8affc7bdc9b6aff50cb525d645729af0995c425936bcddd9dd4ee041d2cc8f4. These are diagnostic observations, not committed programme fixtures.

Logo repair integrates the changes from PR #24 (including both existing Premiere +24 IDs) and adds four exact official Sky logo assets discovered at https://www.sky.de/pakete-produkte/sky-cinema. All nine distinct override URLs returned HTTP 200 with image/png and valid PNG bytes on 2026-11-02. Exact +24 overrides prevent ordinary-feed logos replacing time-shift logos. Unrelated logos and all schedule metadata remain intact.

Validation: 51 local unittest tests pass. Full producer, snapshot, logo reachability and artifact roundtrip gates must pass on this commit before merge/release. Local targeted checks do not establish global logo completeness or public deployment success.

Additional German logo repair: 452 unique missing IDs were joined to original native Samsung/Pluto/Magenta metadata with country-qualified IDs; every replacement passed the HTTPS image check. Further exact channel joins and publisher assets resolve the remaining German missing-logo entries. Evidence is in native-logo-evidence-2026-11-02.json and additional-logo-evidence-2026-11-02.json. The officially downloadable Stimmungsgarten SVG is copied byte-for-byte and referenced by immutable commit URL. These checks do not certify unrelated US logo gaps or the old committed EPG snapshot. The DE producer now requires active programmes and exact display names for all four new Sky Cinema feeds.

Final local checks: 52 tests passed. All four German products have a logo entry for every channel in the pinned diagnostic snapshot: master 897/897, Magenta 267/267, Samsung 465/465, Pluto 178/178. Logo presence is not a reachability certificate. The global CI on 1bdf797d reported 832 missing entries and 180 invalid URLs in the unchanged committed snapshot. DE producer run 41414021999, USA producer run 41414022215 and regression job 125173403094 all succeeded for 1bdf797d. Release run 41414021998 failed; artifact roundtrip was skipped. These are earlier-commit results, not validation of this expanded override set.

Full replacement-asset audit: 684 configured ID mappings reference 640 distinct URLs; checked=640 missing=0 invalid=0 status=passed. The same strict HTTPS/content-signature audit now runs as an additional CI step; the existing global product gate is preserved. Evidence: logo-override-audit-2026-11-02.json.
