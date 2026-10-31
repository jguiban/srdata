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

### Accéder à l'UI InfluxDB

```bash
ssh -N -L 8086:localhost:8086 ubuntu@51.210.45.237
```

puis ouvrir http://localhost:8086.
