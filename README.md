# epg-de

Automatisch erzeugter XMLTV-EPG für **deutschsprachige Sender** und **Sender/Feeds, die in Deutschland verfügbar sind**, auf Basis der aktuellen Daten aus dem iptv-org-Ökosystem.

## TiviMate

Nach einem erfolgreichen GitHub-Actions-Lauf kann diese URL als EPG-Quelle verwendet werden:

```text
https://raw.githubusercontent.com/Kaimandura/epg-de/main/epg/de.xml.gz
```

Unkomprimierte Variante:

```text
https://raw.githubusercontent.com/Kaimandura/epg-de/main/epg/de.xml
```

## Auswahl

Der Collector durchsucht das komplette aktuelle `iptv-org/epg`-Repository und nimmt einen Kanal auf, wenn mindestens eines zutrifft:

- der EPG-Eintrag ist deutschsprachig (`lang="de"` bzw. entsprechender deutscher Sprachcode),
- der zugehörige Feed ist in `iptv-org/database` für Deutschland (`c/DE` oder deutsche Unterregion) markiert,
- die XMLTV-ID ist in der aktuellen Deutschland-Playlist von `iptv-org/iptv` enthalten.

Damit werden nicht nur frei empfangbare deutsche Sender erfasst. Auch internationale, FAST-, Pay-TV- und Plattform-Sender können aufgenommen werden, sofern iptv-org dafür einen nutzbaren EPG-Grabber und eine passende Zuordnung bereitstellt.

## Quellen und Dubletten

Alle passenden EPG-Sites werden berücksichtigt. Bei mehreren Quellen für dieselbe `xmltv_id` wird eine priorisierte Quelle gewählt; wenn diese keine ausreichenden Programmdaten liefert, versucht der Builder weitere Kandidaten. Aktuell haben u. a. folgende Quellen Vorrang:

1. `web.magentatv.de`
2. `plex.tv`
3. alle weiteren gefundenen Sites deterministisch

Der finale XMLTV-Guide enthält pro `xmltv_id` nur einen Programmdatensatz.

## Dateien

- `epg/de.xml` – finaler XMLTV-Guide
- `epg/de.xml.gz` – komprimierter Guide für TiviMate
- `data/de-channels.xml` – bevorzugte Channel-Zuordnungen
- `reports/coverage.csv` – Abdeckung, gewählte Quelle und Programmanzahl je XMLTV-ID

## Aktualisierung

GitHub Actions aktualisiert den Guide täglich und kann zusätzlich manuell gestartet werden. Änderungen an Workflow, Scripts oder Konfiguration lösen ebenfalls einen Build aus.

## Hinweis

Dieses Repository erzeugt EPG-Daten aus externen Quellen. Verfügbarkeit, Vollständigkeit und Nutzungsbedingungen werden durch die jeweiligen Daten- und Programmanbieter bestimmt. Dieses Repository gewährt keine zusätzlichen Rechte an fremden Programmdaten.
