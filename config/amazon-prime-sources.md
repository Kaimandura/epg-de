# Prime Video Germany source evidence

The initial production seed is intentionally conservative.

Verified public availability used for the seed:

- Prime Video Germany continues to provide ARD, ZDF and the regional public broadcasters as live TV after the linear Prime channel ended in August 2026.
- The Prime Video live TV page exposes channels including Das Erste, ZDF HD, NDR Fernsehen NDS, BR Fernsehen Süd, WDR Köln, hr-fernsehen, rbb Berlin, MDR Sachsen, SWR Fernsehen BW, Radio Bremen, SR Fernsehen and Welt HD.
- Rakuten TV publicly announced All Romance, Planet Action and 21 Jump Street as FAST channels on Prime Video Germany in March 2026.

The exporter does not trust a broad `Amazon|Freevee|Prime Video` name regex. Each output channel must resolve to an active channel in the validated master guide and is cloned into the isolated `AmazonPrime.de.*` namespace.
