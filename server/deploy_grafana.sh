#!/usr/bin/env bash
# Déploie les tableaux de bord Grafana (provisionnés depuis grafana/dashboards/).
# Usage : server/deploy_grafana.sh [ubuntu@51.210.45.237]
set -euo pipefail

HOST="${1:-ubuntu@51.210.45.237}"
cd "$(dirname "$0")/.."

tmp=$(ssh "$HOST" mktemp -d)
scp -q grafana/provisioning/dashboards.yaml grafana/dashboards/*.json "$HOST:$tmp/"
ssh "$HOST" "sudo bash -s" <<EOF
set -euo pipefail
install -d -o root -g grafana -m 0750 /etc/grafana/provisioning/dashboards
install -m 0640 -g grafana $tmp/dashboards.yaml /etc/grafana/provisioning/dashboards/srdata.yaml
install -d -o root -g grafana -m 0750 /var/lib/grafana/dashboards/srdata
install -m 0640 -g grafana $tmp/*.json /var/lib/grafana/dashboards/srdata/
rm -rf $tmp
# Nouveau fournisseur de provisioning : Grafana doit être redémarré pour le prendre en compte
systemctl restart grafana-server
for _ in \$(seq 60); do curl -fs http://localhost:3000/api/health >/dev/null && break; sleep 1; done
echo "Grafana redémarré"
EOF
