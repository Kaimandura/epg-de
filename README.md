# epg-de

Automatisch erzeugter XMLTV-EPG fÃ¼r **deutschsprachige Sender** und **Sender/Feeds, die in Deutschland verfÃ¼gbar sind**, auf Basis der aktuellen Daten aus dem iptv-org-Ã–kosystem.

## TiviMate

FÃ¼r TiviMate stehen kurze, eindeutig benannte Adressen bereit. Dadurch bleibt auch in der gekÃ¼rzten Quellenanzeige erkennbar, welcher Guide hinterlegt ist.

### Deutschland

```text
DE Master:  https://kaimandura.github.io/epg-de/DE-MASTER.xml.gz
DE Samsung: https://kaimandura.github.io/epg-de/DE-SAMSUNG.xml.gz
DE Pluto:   https://kaimandura.github.io/epg-de/DE-PLUTO.xml.gz
DE Magenta: https://kaimandura.github.io/epg-de/DE-MAGENTA.xml.gz
```

### USA â€“ validierte Kaimandura-Ausgaben

```text
USA Master: https://kaimandura.github.io/epg-de/USA-MASTER.xml.gz
USA FAST:   https://kaimandura.github.io/epg-de/USA-FAST.xml.gz
USA Local:  https://kaimandura.github.io/epg-de/USA-LOCAL.xml.gz
USA Sports: https://kaimandura.github.io/epg-de/USA-SPORTS.xml.gz
```

Die externen `USA-SOURCE-*`-Feeds sind keine Produkt-Ausgaben und werden nicht mehr verÃ¶ffentlicht. Upstream-Quellen bleiben ausschlieÃŸlich Eingaben des kontrollierten USA-Builds.

Die bisherigen `raw.githubusercontent.com`-Adressen bleiben kompatibel. Die Deutschland-Plattform-Dateien sind Teilmengen der Hauptquelle und starten **keine zusÃ¤tzlichen Grabber-LÃ¤ufe**.

Der USA-Build gleicht aktive Programme zusÃ¤tzlich mit der aktuellen USA-Playlist von iptv-org ab. Eindeutige Treffer werden auch unter der exakten, feed-qualifizierten `tvg-id` verÃ¶ffentlicht; unsichere Zuordnungen bleiben im Unmapped-Report statt automatisch falsch verknÃ¼pft zu werden.

## Auswahl

Der Collector durchsucht das komplette aktuelle `iptv-org/epg`-Repository und nimmt einen Kanal auf, wenn mindestens eines zutrifft:

- der EPG-Eintrag ist deutschsprachig (`lang="de"` bzw. entsprechender deutscher Sprachcode),
- der zugehÃ¶rige Feed ist in `iptv-org/database` fÃ¼r Deutschland (`c/DE` oder deutsche Unterregion) markiert,
- die XMLTV-ID ist in der aktuellen Deutschland-Playlist von `iptv-org/iptv` enthalten.

ZusÃ¤tzlich werden unvollstÃ¤ndige Upstream-EintrÃ¤ge mit leerer `xmltv_id` ausgewertet. Eindeutig zuordenbare Sender werden automatisch gemappt; geprÃ¼fte SonderfÃ¤lle kÃ¶nnen Ã¼ber `config/channel-overrides.json` ergÃ¤nzt werden.

Damit werden nicht nur frei empfangbare deutsche Sender erfasst. Auch internationale, FAST-, Pay-TV- und Plattform-Sender kÃ¶nnen aufgenommen werden, sofern eine nutzbare EPG-Quelle vorhanden ist.

## Plattform-Zuordnung

Die Plattform-Dateien werden anhand der vollstÃ¤ndigen Kandidaten-Metadaten erzeugt, nicht anhand der am Ende gewÃ¤hlten EPG-Quelle. Dadurch bleibt ein Sender beispielsweise in `samsung.xml.gz`, auch wenn seine Programmdaten wegen eines besseren Fallbacks von einer anderen Site stammen.

Aktuelle automatische Plattform-Erkennung:

- **Samsung TV Plus:** deutsche DACH-Feeds aus `SamsungTVPlus/de`, `SamsungTVPlus/at` und `SamsungTVPlus/ch` Ã¼ber `i.mjh.nz`.
- **Pluto TV:** deutsche Pluto-TV-KanÃ¤le aus `pluto.tv_de.channels.xml`.
- **Amazon:** vorhandene Kandidaten mit Amazon-, Freevee- oder Prime-Video-Kennung. Da upstream derzeit keine vollstÃ¤ndige eigenstÃ¤ndige deutsche Amazon-Channel-Liste bereitstellt, kann diese Plattform zusÃ¤tzlich Ã¼ber die Konfiguration erweitert werden.

Die Erkennung steht in `config/platforms.json`. Vor der Veröffentlichung gilt
zentral: Magenta für lineare Sender, Pluto für Pluto-Originalsender, ansonsten
der ursprüngliche Provider. Ein realer Sender steht nur beim zuständigen Provider
und optional im DE-MASTER. Amazon entfällt, solange keine eigenständigen Sender
übrig bleiben. Regionale, sprachliche und zeitversetzte Feeds bleiben getrennt.
Die verbindlichen Ausgaben stehen in `release.json` und `epg-urls.txt` auf Pages.

## Quellen und Dubletten

Alle passenden EPG-Sites werden berÃ¼cksichtigt. Bei mehreren Quellen fÃ¼r dieselbe `xmltv_id` wird eine priorisierte Quelle gewÃ¤hlt; wenn diese keine ausreichenden Programmdaten liefert, versucht der Builder weitere Kandidaten.

Aktuelle priorisierte Quellen beginnen mit:

1. `web.magentatv.de`
2. `www.magenta.tv`
3. `sky.com`
4. `epgshare01.online`
5. `plex.tv`
6. `tv.blue.ch`
7. `tvheute.at`
8. `tv.magenta.at`

Der finale XMLTV-Guide enthÃ¤lt pro `xmltv_id` nur einen Programmdatensatz.

## Dateien

- `epg/de.xml.gz` â€“ vollstÃ¤ndiger komprimierter Guide fÃ¼r TiviMate
- `epg/samsung.xml.gz` â€“ Samsung-TV-Plus-Teilmenge
- `epg/pluto.xml.gz` â€“ Pluto-TV-DE-Teilmenge
- `epg/amazon.xml.gz` â€“ Amazon-/Freevee-/Prime-Video-Teilmenge
- `data/de-channels.xml` â€“ bevorzugte Channel-Zuordnungen
- `reports/coverage.csv` â€“ Abdeckung, gewÃ¤hlte Quelle und Programmanzahl je XMLTV-ID
- `reports/platform-coverage.csv` â€“ Sender- und Programmabdeckung je Plattform-Datei
- `reports/unmapped-de-channels.csv` â€“ noch nicht eindeutig zuordenbare deutsche Upstream-EintrÃ¤ge
- `epg/usa.xml.gz` â€“ nationale USA-Sender
- `epg/usa-fast.xml.gz` â€“ FAST-/Plattform-Sender der USA
- `epg/usa-local.xml.gz` â€“ lokale und regionale USA-Sender
- `epg/usa-sports.xml.gz` â€“ USA-Sport- und Event-Sender
- `reports/usa-channel-mapping.csv` â€“ aktive, exakte iptv-org-Zuordnungen mit Match-Methode
- `reports/usa-unmapped-channels.csv` â€“ aktuelle USA-Playlist-IDs ohne sichere Programmzuordnung
- `reports/usa-quality-audit.json` â€“ harter, dateiÃ¼bergreifender USA-QualitÃ¤tsstatus

Die unkomprimierte `de.xml` wird nur wÃ¤hrend des Builds erzeugt und validiert. Sie wird wegen der GitHub-DateigrÃ¶ÃŸenbegrenzung nicht im Repository verÃ¶ffentlicht.

## Aktualisierung

GitHub Actions aktualisiert den Guide tÃ¤glich im Fast-Profil. Ein wÃ¶chentlicher Deep Scan prÃ¼ft zusÃ¤tzliche Fallbacks. Ã„nderungen an Workflow, Scripts oder Konfiguration lÃ¶sen ebenfalls einen Build aus.

## Hinweis

Dieses Repository erzeugt EPG-Daten aus externen Quellen. VerfÃ¼gbarkeit, VollstÃ¤ndigkeit und Nutzungsbedingungen werden durch die jeweiligen Daten- und Programmanbieter bestimmt. Dieses Repository gewÃ¤hrt keine zusÃ¤tzlichen Rechte an fremden Programmdaten.
