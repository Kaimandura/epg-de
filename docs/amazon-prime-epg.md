# Prime Video Germany EPG

`epg/amazon.xml.gz` is the dedicated XMLTV guide for Prime Video Germany live/FAST channels.

## Model

- Amazon channels use an isolated `AmazonPrime.de.*` namespace.
- Programme data is copied from the validated Germany master only when a channel mapping is explicit or unambiguous.
- The Amazon exporter never changes the master guide, Samsung TV Plus guide, or Pluto TV guide.
- The first production seed contains Prime Video Germany channels that are publicly verified as available, including public broadcasters and selected FAST channels.
- Legacy Freevee naming is preserved through aliases where relevant; the published platform name is Prime Video.

## Release gates

Publication is blocked unless the Amazon guide has at least:

- 10 channels,
- 10 active channels,
- 1,500 programmes,
- valid `AmazonPrime.de.*` channel IDs,
- valid XMLTV channel/programme references,
- deterministic gzip output.

Once a non-empty Amazon guide has been published, Last-Known-Good regression ratios also apply on subsequent runs.

## Reports

- `reports/platform-coverage.csv` contains the Amazon aggregate metrics.
- `reports/amazon-coverage.csv` records every Amazon channel mapping, selected master XMLTV source, programme count, and mapping status.
