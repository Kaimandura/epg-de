# epg-de

Automatisch erzeugter XMLTV-EPG für **deutschsprachige Sender** und **Sender/Feeds, die in Deutschland verfügbar sind**, auf Basis der aktuellen Daten aus dem iptv-org-Ökosystem.

## TiviMate

Für TiviMate stehen kurze, eindeutig benannte Adressen bereit. Dadurch bleibt auch in der gekürzten Quellenanzeige erkennbar, welcher Guide hinterlegt ist.

### Deutschland

```text
Master:  https://kaimandura.github.io/epg-de/DE-MASTER.xml.gz
Samsung: https://kaimandura.github.io/epg-de/DE-SAMSUNG.xml.gz
Pluto:   https://kaimandura.github.io/epg-de/DE-PLUTO.xml.gz
Amazon:  https://kaimandura.github.io/epg-de/DE-AMAZON.xml.gz
```

### USA

```text
Master: https://kaimandura.github.io/epg-de/USA-MASTER.xml.gz
FAST:   https://kaimandura.github.io/epg-de/USA-FAST.xml.gz
Local:  https://kaimandura.github.io/epg-de/USA-LOCAL.xml.gz
Sports: https://kaimandura.github.io/epg-de/USA-SPORTS.xml.gz
```

Die bisherigen `raw.githubusercontent.com`-Adressen bleiben kompatibel. Die Deutschland-Plattform-Dateien sind Teilmengen der Hauptquelle und starten **keine zusätzlichen Grabber-Läufe**.

Der USA-Build gleicht aktive Programme zusätzlich mit der aktuellen USA-Playlist von iptv-org ab. Eindeutige Treffer werden auch unter der exakten, feed-qualifizierten `tvg-id` veröffentlicht; unsichere Zuordnungen bleiben im Unmapped-Report statt automatisch falsch verknüpft zu werden.

## Auswahl

Der Collector durchsucht das komplette aktuelle `iptv-org/epg`-Repository und nimmt einen Kanal auf, wenn mindestens eines zutrifft:

- der EPG-Eintrag ist deutschsprachig (`lang="de"` bzw. entsprechender deutscher Sprachcode),
- der zugehörige Feed ist in `iptv-org/database` für Deutschland (`c/DE` oder deutsche Unterregion) markiert,
- die XMLTV-ID ist in der aktuellen Deutschland-Playlist von `iptv-org/iptv` enthalten.

Zusätzlich werden unvollständige Upstream-Einträge mit leerer `xmltv_id` ausgewertet. Eindeutig zuordenbare Sender werden automatisch gemappt; geprüfte Sonderfälle können über `config/channel-overrides.json` ergänzt werden.

Damit werden nicht nur frei empfangbare deutsche Sender erfasst. Auch internationale, FAST-, Pay-TV- und Plattform-Sender können aufgenommen werden, sofern eine nutzbare EPG-Quelle vorhanden ist.

## Plattform-Zuordnung

Die Plattform-Dateien werden anhand der vollständigen Kandidaten-Metadaten erzeugt, nicht anhand der am Ende gewählten EPG-Quelle. Dadurch bleibt ein Sender beispielsweise in `samsung.xml.gz`, auch wenn seine Programmdaten wegen eines besseren Fallbacks von einer anderen Site stammen.

Aktuelle automatische Plattform-Erkennung:

- **Samsung TV Plus:** deutsche DACH-Feeds aus `SamsungTVPlus/de`, `SamsungTVPlus/at` und `SamsungTVPlus/ch` über `i.mjh.nz`.
- **Pluto TV:** deutsche Pluto-TV-Kanäle aus `pluto.tv_de.channels.xml`.
- **Amazon:** vorhandene Kandidaten mit Amazon-, Freevee- oder Prime-Video-Kennung. Da upstream derzeit keine vollständige eigenständige deutsche Amazon-Channel-Liste bereitstellt, kann diese Plattform zusätzlich über die Konfiguration erweitert werden.

Die Regeln stehen in `config/platforms.json`.

## Quellen und Dubletten

Alle passenden EPG-Sites werden berücksichtigt. Bei mehreren Quellen für dieselbe `xmltv_id` wird eine priorisierte Quelle gewählt; wenn diese keine ausreichenden Programmdaten liefert, versucht der Builder weitere Kandidaten.

Aktuelle priorisierte Quellen beginnen mit:

1. `web.magentatv.de`
2. `www.magenta.tv`
3. `sky.com`
4. `epgshare01.online`
5. `plex.tv`
6. `tv.blue.ch`
7. `tvheute.at`
8. `tv.magenta.at`

Der finale XMLTV-Guide enthält pro `xmltv_id` nur einen Programmdatensatz.

## Dateien

- `epg/de.xml.gz` – vollständiger komprimierter Guide für TiviMate
- `epg/samsung.xml.gz` – Samsung-TV-Plus-Teilmenge
- `epg/pluto.xml.gz` – Pluto-TV-DE-Teilmenge
- `epg/amazon.xml.gz` – Amazon-/Freevee-/Prime-Video-Teilmenge
- `data/de-channels.xml` – bevorzugte Channel-Zuordnungen
- `reports/coverage.csv` – Abdeckung, gewählte Quelle und Programmanzahl je XMLTV-ID
- `reports/platform-coverage.csv` – Sender- und Programmabdeckung je Plattform-Datei
- `reports/unmapped-de-channels.csv` – noch nicht eindeutig zuordenbare deutsche Upstream-Einträge
- `epg/usa.xml.gz` – nationale USA-Sender
- `epg/usa-fast.xml.gz` – FAST-/Plattform-Sender der USA
- `epg/usa-local.xml.gz` – lokale und regionale USA-Sender
- `epg/usa-sports.xml.gz` – USA-Sport- und Event-Sender
- `reports/usa-channel-mapping.csv` – aktive, exakte iptv-org-Zuordnungen mit Match-Methode
- `reports/usa-unmapped-channels.csv` – aktuelle USA-Playlist-IDs ohne sichere Programmzuordnung
- `reports/usa-quality-audit.json` – harter, dateiübergreifender USA-Qualitätsstatus

Die unkomprimierte `de.xml` wird nur während des Builds erzeugt und validiert. Sie wird wegen der GitHub-Dateigrößenbegrenzung nicht im Repository veröffentlicht.

## Aktualisierung

GitHub Actions aktualisiert den Guide täglich im Fast-Profil. Ein wöchentlicher Deep Scan prüft zusätzliche Fallbacks. Änderungen an Workflow, Scripts oder Konfiguration lösen ebenfalls einen Build aus.

## Hinweis

Dieses Repository erzeugt EPG-Daten aus externen Quellen. Verfügbarkeit, Vollständigkeit und Nutzungsbedingungen werden durch die jeweiligen Daten- und Programmanbieter bestimmt. Dieses Repository gewährt keine zusätzlichen Rechte an fremden Programmdaten.
