#!/bin/bash
# Run this once on the Hostinger VPS as root
# Usage: bash setup-hostinger.sh
set -e

DOMAIN="portal.hundredfold.digital"
APP_DIR="/var/www/employee_portal"
REPO="https://github.com/macsamsonx/hfportal.git"

echo "=== Installing system packages ==="
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git

echo "=== Cloning repo ==="
mkdir -p $APP_DIR
git clone $REPO $APP_DIR || (cd $APP_DIR && git pull origin main)

echo "=== Creating Python venv ==="
cd $APP_DIR
python3 -m venv venv
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q

echo "=== Creating required directories ==="
mkdir -p secure_vault/avatars secure_vault/docs secure_vault/posters
chown -R www-data:www-data $APP_DIR

echo "=== Writing .env ==="
cat > $APP_DIR/.env <<EOF
SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
RECAPTCHA_SITE_KEY=6Lcy5kstAAAAAKcPre2oxvMX98rNFR29ErNolUVN
RECAPTCHA_SECRET_KEY=6Lcy5kstAAAAAH8aKLLH4YF-eoqI-JhgBwRtQcB5
EOF
chmod 600 $APP_DIR/.env

echo "=== Installing systemd service ==="
cp $APP_DIR/deploy/employee-portal.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable employee-portal
systemctl start employee-portal

echo "=== Configuring nginx ==="
cp $APP_DIR/deploy/nginx.conf /etc/nginx/sites-available/$DOMAIN
ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/$DOMAIN
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "=== Getting SSL certificate ==="
certbot --nginx -d $DOMAIN --non-interactive --agree-tos -m admin@hundredfold.digital

echo ""
echo "=== Done! Portal is live at https://$DOMAIN ==="
