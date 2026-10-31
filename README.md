# srdata

Collecte des données des bateaux pendant la Route du Rhum 2026.

## Serveur

VPS OVH `51.210.45.237` (Ubuntu 26.04, utilisateur `ubuntu`, accès par clé SSH).

| Service  | Version | Port | Accès |
|----------|---------|------|-------|
| InfluxDB | 2.x     | 8086 | local uniquement (fermé par UFW) → tunnel SSH |
| Grafana  | 13.x    | 3000 | public : http://51.210.45.237:3000 |

- InfluxDB : org `srdata`, bucket `rdr2026` (rétention infinie).
- Grafana : sources de données `InfluxDB (Flux)` (par défaut) et `InfluxDB (InfluxQL)` provisionnées,
  avec un token en lecture seule sur `rdr2026`.

### Installation / mise à jour

Le script est idempotent (il peut être relancé sans risque) :

```bash
ssh ubuntu@51.210.45.237 'sudo bash -s' < server/install_influx_grafana.sh
```

### Identifiants

Générés sur le serveur dans `/root/srdata-credentials.env` (mode 600) :
mots de passe admin InfluxDB / Grafana, `INFLUX_ADMIN_TOKEN`, `INFLUX_GRAFANA_TOKEN` (lecture),
`INFLUX_WRITE_TOKEN` (lecture/écriture du bucket, à utiliser par les collecteurs).

```bash
ssh ubuntu@51.210.45.237 'sudo cat /root/srdata-credentials.env'
```

## Collecteur

Source : le tracker Geovoile de [carto.routedurhum.com](https://carto.routedurhum.com/fr/index.html)
(positions YB Tracking). Il n'existe pas d'API publique ni d'export ; le site sert des fichiers
statiques obfusqués (`/data/tracker_*.hwx`) que [collector/geovoile.py](collector/geovoile.py) décode.

[collector/collector.py](collector/collector.py) (Python 3, sans dépendance) interroge
`versions.txt` chaque minute et, à chaque nouveau pointage, écrit dans le bucket `rdr2026` les
bateaux des classes `Ultim`, `IMOCA` et `Class40` :

| Mesure     | Horodatage            | Tags                                 | Champs |
|------------|-----------------------|--------------------------------------|--------|
| `report`   | heure du pointage     | `class`, `boat`, `boat_id`, `skipper` | `rank`, `status`, `dtf` (distance à l'arrivée, mn), `dtl` (retard sur le leader), `heading`, `speed`, `vmg`, `heading1/dist1/vmg1/dog1` et `heading2/dist2/vmg2/dog2` (moyennes sur deux périodes ; le site les paramètre à 240 et 1440 min, à confirmer pendant la course), `vitmax*`, `dog`, météo (`windspeed`, `winddir`, `waveheight`…), `lat`, `lon` |
| `position` | heure de chaque point | idem                                 | `lat`, `lon`, `dtf` |

Au démarrage, et si un pointage a été manqué, tout l'historique (pointages + traces) est
réécrit (écritures idempotentes). Les données publiées avant le départ, datées dans le futur,
sont ignorées. Les fichiers décodés sont archivés dans `/var/lib/srdata/archive/`.

Déploiement / mise à jour (service systemd `srdata-collector`) :

```bash
server/deploy_collector.sh
```

Logs :

```bash
ssh ubuntu@51.210.45.237 'journalctl -u srdata-collector -f'
```

## Dashboard Grafana

[grafana/dashboards/route-du-rhum.json](grafana/dashboards/route-du-rhum.json), provisionné dans le
dossier « Route du Rhum » : http://51.210.45.237:3000/d/route-du-rhum-2026

- filtres classe / bateau / pas des traces ;
- carte des positions et traces, heure du dernier pointage, leaders par classe ;
- une section par classe : classement complet et courbes (retard sur le leader, vitesse, VMG,
  distance sur 24 h).

Déploiement / mise à jour :

```bash
server/deploy_grafana.sh
```

Le fichier du dépôt est la référence : les modifications faites dans l'UI sont écrasées au
prochain déploiement si le fichier a changé. Pour les garder, exporter le JSON depuis Grafana
et le remplacer dans le dépôt.

### Accéder à l'UI InfluxDB

```bash
ssh -N -L 8086:localhost:8086 ubuntu@51.210.45.237
```

puis ouvrir http://localhost:8086.
