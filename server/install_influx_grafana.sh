#!/usr/bin/env bash
# Installation d'InfluxDB 2 + Grafana sur le VPS (Ubuntu), idempotent.
# Usage : ssh ubuntu@51.210.45.237 'sudo bash -s' < server/install_influx_grafana.sh
#
# Les identifiants générés sont écrits dans /root/srdata-credentials.env (mode 600).
set -euo pipefail

INFLUX_ORG="${INFLUX_ORG:-srdata}"
INFLUX_BUCKET="${INFLUX_BUCKET:-rdr2026}"
INFLUX_USER="${INFLUX_USER:-admin}"
GRAFANA_USER="admin"
CREDS=/root/srdata-credentials.env

export DEBIAN_FRONTEND=noninteractive

# --- Identifiants (générés une seule fois) -----------------------------------
if [[ ! -f "$CREDS" ]]; then
  install -m 600 /dev/null "$CREDS"
  cat > "$CREDS" <<EOF
INFLUX_URL=http://localhost:8086
INFLUX_ORG=$INFLUX_ORG
INFLUX_BUCKET=$INFLUX_BUCKET
INFLUX_USER=$INFLUX_USER
INFLUX_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')
GRAFANA_USER=$GRAFANA_USER
GRAFANA_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')
EOF
fi
# shellcheck disable=SC1090
source "$CREDS"

# --- Dépôts APT -------------------------------------------------------------
apt-get update -q || true  # les dépôts tiers peuvent ne pas être encore valides
apt-get install -y -q curl gpg ca-certificates apt-transport-https jq
install -d -m 0755 /etc/apt/keyrings

if [[ ! -f /etc/apt/keyrings/influxdata-archive.gpg ]]; then
  curl -fsSL https://repos.influxdata.com/influxdata-archive.key -o /tmp/influxdata-archive.key
  gpg --show-keys --with-fingerprint --with-colons /tmp/influxdata-archive.key \
    | grep -q '^fpr:\+24C975CBA61A024EE1B631787C3D57159FC2F927:$' \
    || { echo "Empreinte de la clé InfluxData inattendue" >&2; exit 1; }
  gpg --dearmor < /tmp/influxdata-archive.key > /etc/apt/keyrings/influxdata-archive.gpg
fi
echo 'deb [signed-by=/etc/apt/keyrings/influxdata-archive.gpg] https://repos.influxdata.com/debian stable main' \
  > /etc/apt/sources.list.d/influxdata.list

if [[ ! -f /etc/apt/keyrings/grafana.gpg ]]; then
  curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor > /etc/apt/keyrings/grafana.gpg
fi
echo 'deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main' \
  > /etc/apt/sources.list.d/grafana.list
chmod 644 /etc/apt/keyrings/influxdata-archive.gpg /etc/apt/keyrings/grafana.gpg

apt-get update -q
apt-get install -y -q influxdb2 influxdb2-cli grafana

# --- InfluxDB ---------------------------------------------------------------
systemctl enable --now influxdb
for _ in $(seq 30); do curl -fs http://localhost:8086/health >/dev/null && break; sleep 1; done

if ! influx org list --json 2>/dev/null | jq -e --arg o "$INFLUX_ORG" '.[] | select(.name==$o)' >/dev/null; then
  influx setup --force \
    --username "$INFLUX_USER" --password "$INFLUX_PASSWORD" \
    --org "$INFLUX_ORG" --bucket "$INFLUX_BUCKET" --retention 0
fi

# Token admin (créé par `influx setup`, stocké dans la config CLI de root)
# (`influx auth list` ne renvoie plus les tokens en clair)
if ! grep -q '^INFLUX_ADMIN_TOKEN=.' "$CREDS"; then
  sed -i '/^INFLUX_ADMIN_TOKEN=/d' "$CREDS"
  token=$(sed -n '/^\[default\]/,/^\[/s/^ *token = "\(.*\)"/\1/p' /root/.influxdbv2/configs)
  echo "INFLUX_ADMIN_TOKEN=$token" >> "$CREDS"
fi

# Token lecture seule pour Grafana, token écriture pour les collecteurs
bucket_id=$(influx bucket list --name "$INFLUX_BUCKET" --json | jq -r '.[0].id')
if ! grep -q '^INFLUX_GRAFANA_TOKEN=' "$CREDS"; then
  token=$(influx auth create --org "$INFLUX_ORG" --read-bucket "$bucket_id" \
    --description "grafana (lecture $INFLUX_BUCKET)" --json | jq -r '.token')
  echo "INFLUX_GRAFANA_TOKEN=$token" >> "$CREDS"
fi
if ! grep -q '^INFLUX_WRITE_TOKEN=' "$CREDS"; then
  token=$(influx auth create --org "$INFLUX_ORG" --read-bucket "$bucket_id" --write-bucket "$bucket_id" \
    --description "collecteur (lecture/écriture $INFLUX_BUCKET)" --json | jq -r '.token')
  echo "INFLUX_WRITE_TOKEN=$token" >> "$CREDS"
fi
# shellcheck disable=SC1090
source "$CREDS"

# --- Grafana ----------------------------------------------------------------
install -d -o root -g grafana -m 0750 /etc/grafana/provisioning/datasources
install -m 640 -g grafana /dev/null /etc/grafana/provisioning/datasources/influxdb.yaml
cat > /etc/grafana/provisioning/datasources/influxdb.yaml <<EOF
apiVersion: 1
datasources:
  - name: InfluxDB (Flux)
    uid: influx-flux
    type: influxdb
    access: proxy
    url: http://localhost:8086
    isDefault: true
    jsonData:
      version: Flux
      organization: $INFLUX_ORG
      defaultBucket: $INFLUX_BUCKET
    secureJsonData:
      token: $INFLUX_GRAFANA_TOKEN
  - name: InfluxDB (InfluxQL)
    uid: influx-influxql
    type: influxdb
    access: proxy
    url: http://localhost:8086
    jsonData:
      dbName: $INFLUX_BUCKET
      httpMode: POST
      httpHeaderName1: Authorization
    secureJsonData:
      httpHeaderValue1: Token $INFLUX_GRAFANA_TOKEN
EOF

# Pas d'inscription publique, pas d'accès anonyme
sed -i \
  -e 's/^;\?allow_sign_up = .*/allow_sign_up = false/' \
  -e 's/^;\?reporting_enabled = .*/reporting_enabled = false/' \
  /etc/grafana/grafana.ini

systemctl enable grafana-server
systemctl restart grafana-server
for _ in $(seq 60); do curl -fs http://localhost:3000/api/health >/dev/null && break; sleep 1; done
grafana cli --homepath /usr/share/grafana --config /etc/grafana/grafana.ini \
  admin reset-admin-password "$GRAFANA_PASSWORD" >/dev/null

# --- Pare-feu ---------------------------------------------------------------
# Grafana ouvert au public ; InfluxDB accessible seulement en local / via tunnel SSH.
ufw allow 3000/tcp comment 'Grafana' >/dev/null

echo "OK - identifiants dans $CREDS"
