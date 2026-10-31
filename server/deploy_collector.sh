#!/usr/bin/env bash
# Déploie (ou met à jour) le collecteur sur le VPS et (re)démarre le service.
# Usage : server/deploy_collector.sh [ubuntu@51.210.45.237]
set -euo pipefail

HOST="${1:-ubuntu@51.210.45.237}"
cd "$(dirname "$0")/.."

tmp=$(ssh "$HOST" mktemp -d)
scp -q collector/*.py server/srdata-collector.service "$HOST:$tmp/"
ssh "$HOST" "sudo bash -s" <<EOF
set -euo pipefail
id srdata >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin srdata
install -d -m 0755 /opt/srdata/collector
install -m 0644 $tmp/*.py /opt/srdata/collector/
install -m 0644 $tmp/srdata-collector.service /etc/systemd/system/
rm -rf $tmp
systemctl daemon-reload
systemctl enable srdata-collector >/dev/null
systemctl restart srdata-collector
sleep 5
systemctl --no-pager --lines=10 status srdata-collector
EOF
