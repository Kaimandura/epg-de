# Sky Cinema source repair — 2026-11-02

Official naming evidence: https://www.sky.de/hilfe/abonnement/programm/deine-neuen-sky-cinema-sender confirms that on 2026-05-19 Highlights became Blockbuster and Family became Feelgood. Best Of is not the current name.

Upstream https://github.com/iptv-org/epg/blob/master/sites/sky.com/sky.com.channels.xml contains DE#330/331/332/333 with the exact German HD and HD +24 names but empty XMLTV IDs. The collector cannot resolve these by a unique existing name. Four explicit overrides now include those sources without copying or inventing schedules. Expected source names guard against reassigned service IDs.

Live source verification on 2026-11-02: https://awk.epgsky.com/hawk/linear/schedule/20261102/330,331,332,333 with X-SkyOTT-Territory: DE returned respectively 14,15,14,15 events. Raw response SHA256: d8affc7bdc9b6aff50cb525d645729af0995c425936bcddd9dd4ee041d2cc8f4. These are diagnostic observations, not committed programme fixtures.

Logo repair integrates the changes from PR #24 (including both existing Premiere +24 IDs) and adds four exact official Sky logo assets discovered at https://www.sky.de/pakete-produkte/sky-cinema. All nine distinct override URLs returned HTTP 200 with image/png and valid PNG bytes on 2026-11-02. Exact +24 overrides prevent ordinary-feed logos replacing time-shift logos. Unrelated logos and all schedule metadata remain intact.

Validation: 51 local unittest tests pass. Full producer, snapshot, logo reachability and artifact roundtrip gates must pass on this commit before merge/release. Local targeted checks do not establish global logo completeness or public deployment success.
