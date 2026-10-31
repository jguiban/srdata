"""Accès aux données du tracker Geovoile de la Route du Rhum (carto.routedurhum.com).

Le site publie des fichiers statiques sous /data/ :
  - versions.txt         : numéro de version de chaque ressource ({config:…,live:…})
  - tracker_config.hwx   : XML, classes et bateaux
  - tracker_live.hwx     : JSON, dernier pointage (avec la trace depuis le précédent)
  - tracker_reports.hwx  : JSON, historique de tous les pointages
Les .hwx sont obfusqués (XOR par un générateur xorshift + compression LZ) ;
`decode_hwx` est le portage du décodeur JavaScript embarqué dans la page.
"""
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

BASE_URL = "https://carto.routedurhum.com/data/"
USER_AGENT = "srdata-collector"


def _keystream(skip):
    st = [0x7BC495, 0x4557FA, 0xD56AAF, 0xFF8040]

    def step():
        t = st[0]
        t ^= (t << 11) & 0xFFFFFF
        t ^= (t >> 8) & 0xFFFFFF
        st[0], st[1], st[2] = st[1], st[2], st[3]
        st[3] ^= (st[3] >> 19) & 0xFFFFFF
        st[3] ^= t

    for _ in range(skip):
        step()

    def dec(x):
        r = x ^ (st[0] & 0xFF)
        step()
        return r

    return dec


def decode_hwx(raw: bytes) -> bytes:
    k = _keystream(raw[0])
    size = (k(raw[1]) << 16) + (k(raw[2]) << 8) + k(raw[3])
    out = bytearray(size)
    i, o, n = 4, 0, len(raw)
    while i < n and o < size:
        flags = (raw[i] ^ (i & 0xFF)) ^ 0xA3
        i += 1
        for bit in range(7, -1, -1):
            if not flags & (1 << bit):
                out[o] = k(raw[i])
                o += 1
                i += 1
            else:
                b0 = k(raw[i])
                length = (b0 >> 4) + 3
                dist = (((b0 & 0xF) << 8) | k(raw[i + 1])) + 1
                i += 2
                for _ in range(length):
                    out[o] = out[o - dist]
                    o += 1
            if i >= n:
                break
    return bytes(out)


def _get(path, timeout=30):
    req = urllib.request.Request(BASE_URL + path, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_versions():
    text = _get(f"versions.txt?v={int(time.time())}").decode()
    return {k: int(v) for k, v in re.findall(r"(\w+)\s*:\s*(\d+)", text)}


def fetch_raw(resource, version):
    """Contenu décodé (bytes) de tracker_<resource>.hwx."""
    return decode_hwx(_get(f"tracker_{resource}.hwx?v={version}"))


def parse_config(raw):
    """Bateaux indexés par id : {id: {name, sail, class, skipper}}."""
    root = ET.fromstring(raw)
    boats = {}
    for bc in root.iter("boatclass"):
        for b in bc.iter("boat"):
            skippers = [f"{n.get('fname', '')} {n.get('lname', '')}".strip() for n in b.iter("navigator")]
            boats[b.get("id")] = {
                "name": b.get("name"),
                "sail": b.get("sail", ""),
                "class": bc.get("name"),
                "skipper": " / ".join(skippers),
            }
    return boats


def parse_tracks(raw, coords_factor=100000):
    """Traces complètes : {boat_id: [[ts, lat, lon], …]}.

    Chaque point est relatif au précédent (sauf le premier), coordonnées × coords_factor.
    """
    tracks = {}
    for track in json.loads(raw)["tracks"]:
        if track["id"] == 0:  # trace jour/nuit
            continue
        points, ts, lat, lon = [], 0, 0.0, 0.0
        for loc in track.get("loc") or []:
            if not loc or len(loc) < 3:
                continue
            ts += loc[0]
            lat += loc[1] / coords_factor
            lon += loc[2] / coords_factor
            points.append([ts, round(lat, 5), round(lon, 5)])
        tracks[str(track["id"])] = points
    return tracks


def parse_reports(raw):
    """Pointages : liste de {id, date, lines: [{colonne: valeur}]}."""
    data = json.loads(raw)["reports"]
    columns = data["columns"]
    return [
        {
            "id": h["id"],
            "date": h["date"],
            "lines": [dict(zip(columns, line)) for line in h["lines"]],
        }
        for h in data["history"]
    ]
