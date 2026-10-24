#!/usr/bin/env bash
# Deploy the seller bot to a Droplet over SSH. Run from the project root on your Mac:
#
#   deploy/deploy.sh root@<droplet-ip>            # update code, keep the server's .env and database
#   deploy/deploy.sh root@<droplet-ip> --first    # first time: also copy .env and the local database
#
# Telegram lets only one copy fetch messages, so stop the bot on your Mac before deploying.
set -euo pipefail

TARGET=${1:?usage: deploy/deploy.sh user@host [--first]}
FIRST=${2:-}
APP=/opt/delivery-sathi
DATA=/var/lib/delivery-sathi

echo "Uploading code to $TARGET:$APP"
git archive --format=tar HEAD | ssh "$TARGET" "mkdir -p $APP && tar -x -C $APP"

if [ "$FIRST" = "--first" ]; then
  echo "Copying .env and the local database"
  scp -q .env "$TARGET:$APP/.env"
  if [ -f seller_bot/sathi.db ]; then
    ssh "$TARGET" "mkdir -p $DATA"
    scp -q seller_bot/sathi.db "$TARGET:$DATA/sathi.db"
  fi
fi

ssh "$TARGET" bash -s <<REMOTE
set -euo pipefail
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ needed"'
id sathi >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin sathi
mkdir -p $DATA
chown -R sathi:sathi $DATA
chown root:sathi $APP/.env && chmod 640 $APP/.env
[ -f $DATA/sathi.db ] && chmod 600 $DATA/sathi.db
cp $APP/deploy/delivery-sathi.service /etc/systemd/system/delivery-sathi.service
systemctl daemon-reload
systemctl enable --now delivery-sathi
systemctl restart delivery-sathi
sleep 3
systemctl --no-pager --lines=8 status delivery-sathi
REMOTE
