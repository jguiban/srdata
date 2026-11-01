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
| `report`   | heure du pointage     | `class`, `boat`, `boat_id`, `skipper` | `rank`, `status`, `dtf` (distance à l'arrivée, mn), `dtl` (retard sur le leader), `heading`, `speed`, `vmg`, `heading1/dist1/vmg1/dog1` (sur 4 h) et `heading2/dist2/vmg2/dog2` (sur 24 h), `vitmax*`, `dog`, météo (`windspeed`, `winddir`, `waveheight`…, voir les échelles plus bas), `lat`, `lon` |
| `position` | heure de chaque point | idem                                 | `lat`, `lon`, `dtf` |

Les pointages sont publiés toutes les 5 min juste après le départ, puis toutes les heures ;
les traces (`position`) sont plus denses : un point toutes les 15 min (5 min au départ). Les
nouveaux points de trace sont écrits à chaque mise à jour du site. Au démarrage, et si un
pointage a été manqué, tout l'historique (pointages + traces) est réécrit (écritures idempotentes). Les données publiées avant le départ, datées dans le futur,
sont ignorées. Les fichiers décodés sont archivés dans `/var/lib/srdata/archive/`.

Déploiement / mise à jour (service systemd `srdata-collector`) :

```bash
server/deploy_collector.sh
```

Logs :

```bash
ssh ubuntu@51.210.45.237 'journalctl -u srdata-collector -f'
```

## Données pour les polaires de vitesse

Une polaire donne la vitesse du bateau en fonction de la force du vent réel (TWS) et de
l'angle au vent réel (TWA). Les données collectées permettent de la reconstruire :

| Grandeur | Source | Remarques |
|----------|--------|-----------|
| Vitesse et route fond (SOG, COG) | `position` : points successifs toutes les 15 min | à calculer (distance et cap entre deux points) ; `report.speed`/`heading` donnent aussi SOG/COG au pointage (horaire, cap entier) |
| Direction du vent réel (TWD) | `report.winddir` | degrés, d'où vient le vent ; valeur horaire au pointage |
| Force du vent réel (TWS) | `report.windspeed`, `report.windgust` | **en dixièmes de nœud** (137 = 13,7 nds) |
| Angle au vent (TWA) | calculé | `TWD − COG`, ramené à 0–180° (bâbord/tribord confondus) |
| Courant | `report.currentdir`, `report.currentspeed` | direction en degrés, vitesse **en dixièmes de nœud** ; permet de passer de la vitesse fond à la vitesse surface |
| État de la mer | `report.waveheight`, `report.wavedir` | hauteur **en décimètres** |
| Températures | `report.tempair`, `report.tempwater` | **en dixièmes de °C** |

Échelles déduites des valeurs publiées (vent 13,7–21,6 nds, rafales jusqu'à 29,7 nds le soir
du départ) ; à recouper avec l'affichage du site. `clouds` et `rain` : unité inconnue.

Limites à garder en tête :

- **Vent de modèle** : la météo est fournie par le site à la position de chaque bateau (modèle
  du partenaire météo), pas mesurée à bord ; elle n'est donnée qu'aux pointages (horaires). Il
  faut l'interpoler dans le temps pour les points de trace intermédiaires.
- **Vitesse fond** : les traces donnent la vitesse fond ; la correction du courant donne la
  vitesse surface.
- **Pas de contexte à bord** : manœuvres, voiles, réduction de toile, avaries… La polaire
  « réelle » s'obtient en gardant un haut percentile des vitesses par case TWS × TWA et en
  écartant les points proches d'un virement ou d'un empannage (forte variation de cap).

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
