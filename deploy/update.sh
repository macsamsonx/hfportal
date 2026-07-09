#!/bin/bash
# Run this on the VPS to pull latest code and restart
# Usage: bash /var/www/employee_portal/deploy/update.sh
set -e
cd /var/www/employee_portal
git pull origin main
venv/bin/pip install -r requirements.txt -q
systemctl restart employee-portal
systemctl status employee-portal --no-pager
echo "Deploy complete."
