#!/bin/sh
cd $(dirname $0)
python3 -m pip install --upgrade pip
sudo git stash
sudo git stash drop
sudo git pull
sudo chown -R brandonmcfadden:brandonmcfadden .
sudo chmod +x production-upgrade.sh
pip install -r /home/brandonmcfadden/my-api/requirements.txt
sudo systemctl restart api-service.service