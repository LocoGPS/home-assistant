# LocoGPS für Home Assistant

Bringt deine LocoGPS-Tracker in Home Assistant. Positionen und Geozäune kommen live an, ohne Verzögerung durch Abfrageintervalle. Damit kannst du zum Beispiel über deine Echo-Lautsprecher ansagen lassen, wenn dein Hund das Zuhause verlässt oder zurückkommt, oder dich an das Aufladen erinnern lassen.

Die ausführliche Anleitung mit Bildern und Beispielen findest du im [LocoGPS-Hilfecenter](https://locogps.de/helpcenter/erweiterte-funktionen/home-assistant-einbinden/).

Die ausführliche Anleitung findest du im [LocoGPS-Hilfecenter](https://locogps.de/helpcenter/erweiterte-funktionen/home-assistant-einbinden/).

*English below.*

## Was du bekommst

Pro Tracker legt die Integration ein Gerät mit diesen Entitäten an:

| Entität | Beispiel | Bedeutung |
|---|---|---|
| Standort | `device_tracker.edda` | Position auf der Karte, zuhause oder unterwegs |
| Geozaun | `binary_sensor.edda_geozaun_zuhause` | Drinnen oder Draußen, eine Entität pro Geozaun des Trackers |
| Akku | `sensor.edda_akku` | Akkustand in Prozent |
| Laden | `binary_sensor.edda_laden` | ob der Tracker gerade lädt |
| Online | `binary_sensor.edda_online` | ob der Tracker mit dem Server verbunden ist |
| Letzte Aktualisierung | `sensor.edda_letzte_aktualisierung` | wann sich der Tracker zuletzt gemeldet hat |
| WLAN-Energiesparmodus | `binary_sensor.edda_wlan_energiesparmodus` | ob ein LV2 gerade an einem gespeicherten WLAN Strom spart, mit dem Namen der WLAN-Zone |

Für Automationen rund um das Zuhause nimmst du am besten den Geozaun. Der wird direkt von LocoGPS berechnet und schaltet genau dann, wenn auch die Benachrichtigung in der App kommt.

## Installation

Du brauchst Home Assistant ab Version 2026.3 und [HACS](https://hacs.xyz).

1. In HACS oben rechts das Menü öffnen und **Benutzerdefinierte Repositories** wählen.
2. `https://github.com/LocoGPS/home-assistant` eintragen, als Typ **Integration** wählen und hinzufügen.
3. **LocoGPS** in HACS suchen, herunterladen und Home Assistant neu starten.
4. Unter **Einstellungen > Geräte & Dienste > Integration hinzufügen** nach **LocoGPS** suchen.
5. Mit der E-Mail-Adresse und dem Passwort deines LocoGPS-Kontos anmelden.

Dein Passwort wird nicht gespeichert. Home Assistant bekommt einen eigenen Zugangsschlüssel, der ein Jahr gilt und sich von selbst verlängert. Entfernst du die Integration, wird der Schlüssel gelöscht.

## Beispiele

Die Ansagen laufen über die Integration [Alexa Media Player](https://github.com/alandtse/alexa_media_player). Trag bei `target` deinen Echo ein und bei `entity_id` die Entitäten deines Trackers.

Ansage, wenn der Hund das Zuhause verlässt:

```yaml
alias: Edda hat das Zuhause verlassen
triggers:
  - trigger: state
    entity_id: binary_sensor.edda_geozaun_zuhause
    from: "on"
    to: "off"
actions:
  - action: notify.alexa_media
    data:
      target: media_player.echo_kueche
      message: Edda hat das Zuhause verlassen.
      data:
        type: announce
```

Für die Ankunft tauschst du `from` und `to` und passt den Text an.

Erinnerung, wenn der Akku unter 20 Prozent fällt:

```yaml
alias: Eddas Tracker aufladen
triggers:
  - trigger: numeric_state
    entity_id: sensor.edda_akku
    below: 20
actions:
  - action: notify.alexa_media
    data:
      target: media_player.echo_kueche
      message: Der Akku von Eddas Tracker ist fast leer, bitte aufladen.
      data:
        type: announce
```

## Hilfe

Fragen und Fehlermeldungen gerne an [LocoGPS](https://locogps.de/kontakt) oder als [Issue](https://github.com/LocoGPS/home-assistant/issues).

---

## English

Brings your LocoGPS trackers into Home Assistant with live updates. Install it through HACS as a custom repository (`https://github.com/LocoGPS/home-assistant`, type Integration), restart Home Assistant, add the **LocoGPS** integration and log in with your LocoGPS account. Your password is not stored. Home Assistant receives its own access token that is valid for one year, renews itself and is revoked when you remove the integration.

Every tracker gets a device tracker, one binary sensor per geofence (Inside or Outside), battery level, charging, online status, last update and, for LV2 trackers, the WLAN power saving state. For automations around home, prefer the geofence sensor. It is computed by LocoGPS and switches exactly when the LocoGPS app notifies you.

## Development

```
pip install -r requirements_test.txt ruff
ruff check . && ruff format --check .
pytest
```

The tests run the integration in Home Assistant against a small fake LocoGPS server (`tests/fake_server.py`). `scripts/smoke_test.py` checks the API client against the real server without Home Assistant and changes nothing on the account.
