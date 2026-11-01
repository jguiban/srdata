"""Collecte des pointages de la Route du Rhum vers InfluxDB.

Interroge versions.txt toutes les POLL_SECONDS secondes ; à chaque nouvelle
version de « live », écrit le pointage dans InfluxDB. Au démarrage, ou si des
pointages ont été manqués, tout l'historique (tracker_reports) est réécrit.
À chaque nouvelle version de « tracks », les nouveaux points de trace (plus
denses que les pointages : toutes les 5 à 15 min) sont écrits ; au démarrage,
toute la trace. Les écritures sont idempotentes (même série + même horodatage
= écrasement).

Mesures écrites :
  report   (horodatage du pointage) : classement, distances, vitesses, météo…
  position (horodatage de chaque point de trace) : lat, lon, dtf

Variables d'environnement : INFLUX_URL, INFLUX_ORG, INFLUX_BUCKET,
INFLUX_WRITE_TOKEN, CLASSES (défaut « Ultim,IMOCA,Class40 »),
POLL_SECONDS (défaut 60), ARCHIVE_DIR (copie brute des fichiers décodés,
désactivée si vide).
"""
import gzip
import logging
import os
import pathlib
import time
import urllib.parse
import urllib.request
from datetime import datetime

import geovoile

log = logging.getLogger("collector")

INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_ORG = os.environ["INFLUX_ORG"]
INFLUX_BUCKET = os.environ["INFLUX_BUCKET"]
INFLUX_TOKEN = os.environ["INFLUX_WRITE_TOKEN"]
CLASSES = {c.strip() for c in os.environ.get("CLASSES", "Ultim,IMOCA,Class40").split(",")}
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
ARCHIVE_DIR = os.environ.get("ARCHIVE_DIR", "")

# Les données de test publiées avant le départ sont datées dans le futur : on les ignore.
FUTURE_TOLERANCE = 300

# Colonnes non numériques ou traitées à part
SKIP_COLUMNS = {"boat", "racestatus", "track"}


def _tag(value):
    return str(value).replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def _str_field(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _tags(boat_id, boat):
    return (
        f"class={_tag(boat['class'])},boat={_tag(boat['name'])},"
        f"boat_id={_tag(boat_id)},skipper={_tag(boat['skipper'] or '-')}"
    )


def report_to_lines(report, boats):
    ts = int(datetime.fromisoformat(report["date"]).timestamp())
    # Pointage daté dans le futur : on garde quand même les positions déjà passées de la trace
    report_is_future = ts > time.time() + FUTURE_TOLERANCE
    out = []
    for line in report["lines"]:
        boat_id = str(line["boat"])
        boat = boats.get(boat_id)
        if not boat or boat["class"] not in CLASSES:
            continue
        tags = _tags(boat_id, boat)
        fields = [f"status={_str_field(line['racestatus'])}", f"report_id={int(report['id'])}i"]
        for col, value in line.items():
            if col in SKIP_COLUMNS or not isinstance(value, (int, float)):
                continue
            # Tout en flottant : évite les conflits de type InfluxDB (92 puis 92.5)
            fields.append(f"{col}={float(value)}")
        track = [p for p in line.get("track") or [] if len(p) >= 3]
        if track:
            fields.append(f"lat={float(track[-1][1])}")
            fields.append(f"lon={float(track[-1][2])}")
        if not report_is_future:
            out.append(f"report,{tags} {','.join(fields)} {ts}")
        out += track_to_lines(boat_id, track, boats)
    return out


def track_to_lines(boat_id, track, boats):
    """Points [ts, lat, lon, (dtf)] → mesure position."""
    boat = boats.get(boat_id)
    if not boat or boat["class"] not in CLASSES:
        return []
    tags = _tags(boat_id, boat)
    out = []
    for p in track:
        if len(p) < 3 or p[0] > time.time() + FUTURE_TOLERANCE:
            continue
        pos = [f"lat={float(p[1])}", f"lon={float(p[2])}"]
        if len(p) >= 4:
            pos.append(f"dtf={float(p[3])}")
        out.append(f"position,{tags} {','.join(pos)} {int(p[0])}")
    return out


def write_lines(lines, batch=5000):
    query = urllib.parse.urlencode({"org": INFLUX_ORG, "bucket": INFLUX_BUCKET, "precision": "s"})
    for i in range(0, len(lines), batch):
        req = urllib.request.Request(
            f"{INFLUX_URL}/api/v2/write?{query}",
            data="\n".join(lines[i:i + batch]).encode(),
            headers={"Authorization": f"Token {INFLUX_TOKEN}", "Content-Type": "text/plain; charset=utf-8"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=30).close()


def archive(resource, version, raw, overwrite=False):
    if not ARCHIVE_DIR:
        return
    path = pathlib.Path(ARCHIVE_DIR) / f"{resource}_{version}.gz"
    if overwrite or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(raw))


def write_reports(reports, boats):
    lines = [l for r in reports for l in report_to_lines(r, boats)]
    if lines:
        write_lines(lines)
    return len(lines)


class Collector:
    def __init__(self):
        self.versions = {}
        self.boats = {}
        self.last_report_id = None
        # Horodatage du dernier point de trace écrit (None : tout l'historique reste à écrire)
        self.tracks_until = None

    def tick(self):
        versions = geovoile.fetch_versions()

        if versions.get("config") != self.versions.get("config"):
            raw = geovoile.fetch_raw("config", versions["config"])
            archive("config", versions["config"], raw)
            self.boats = geovoile.parse_config(raw)
            selected = sum(b["class"] in CLASSES for b in self.boats.values())
            log.info("config v%s : %d bateaux, %d suivis (%s)",
                     versions["config"], len(self.boats), selected, ", ".join(sorted(CLASSES)))
            self.versions["config"] = versions["config"]

        if versions.get("live") != self.versions.get("live"):
            raw = geovoile.fetch_raw("live", versions["live"])
            archive("live", versions["live"], raw)
            live = geovoile.parse_reports(raw)
            latest_id = max(r["id"] for r in live) if live else None
            gap = self.last_report_id is None or (
                latest_id is not None and latest_id > self.last_report_id + 1)
            if gap:
                raw = geovoile.fetch_raw("reports", versions["reports"])
                archive("reports", versions["reports"], raw)
                reports = geovoile.parse_reports(raw) + live
                log.info("rattrapage : %d pointages dans l'historique", len(reports))
            else:
                reports = live
            n = write_reports(reports, self.boats)
            log.info("live v%s : pointage %s, %d lignes écrites", versions["live"], latest_id, n)
            self.last_report_id = latest_id
            self.versions["live"] = versions["live"]

        # Les traces sont plus denses que les pointages (positions toutes les 5 à 15 min) :
        # on écrit les nouveaux points à chaque version, avec 1 h de recouvrement.
        if versions.get("tracks") != self.versions.get("tracks"):
            raw = geovoile.fetch_raw("tracks", versions["tracks"])
            archive("tracks", time.strftime("%Y%m%d", time.gmtime()), raw, overwrite=True)
            since = 0 if self.tracks_until is None else self.tracks_until - 3600
            lines, until = [], self.tracks_until or 0
            for boat_id, track in geovoile.parse_tracks(raw).items():
                recent = [p for p in track if p[0] >= since]
                lines += track_to_lines(boat_id, recent, self.boats)
                until = max([until] + [p[0] for p in recent if p[0] <= time.time() + FUTURE_TOLERANCE])
            if lines:
                write_lines(lines)
            log.info("tracks v%s : %d positions écrites", versions["tracks"], len(lines))
            self.tracks_until = until
            self.versions["tracks"] = versions["tracks"]


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    collector = Collector()
    while True:
        try:
            collector.tick()
        except Exception:
            log.exception("échec de la collecte, nouvel essai dans %ss", POLL_SECONDS)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
