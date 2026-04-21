#!/bin/bash
set -e

cd /home/ubuntu/clyde-ai

echo ">>> Syncing with origin/development..."
git fetch origin
git checkout development
git reset --hard origin/development

echo ">>> Rebuilding containers..."
docker compose -f docker-compose-dev.yaml down --remove-orphans
docker compose -f docker-compose-dev.yaml build

echo ">>> Running Alembic migrations..."
docker compose -f docker-compose-dev.yaml run --rm app alembic upgrade head

echo ">>> Starting app..."
docker compose -f docker-compose-dev.yaml up -d --force-recreate

docker image prune -f

echo ">>> Deploy finished."

sudo systemctl restart nginx
echo ">>> nginx has been restarted"
