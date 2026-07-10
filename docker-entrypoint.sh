#!/bin/sh
set -e

echo "Waiting for PostgreSQL at ${DATABASE_HOST:-postgres}:${DATABASE_PORT:-5432}..."
until python -c "import os, socket; s = socket.socket(); s.settimeout(2); s.connect((os.environ.get('DATABASE_HOST', 'postgres'), int(os.environ.get('DATABASE_PORT', '5432')))); s.close()" 2>/dev/null; do
  sleep 1
done
echo "PostgreSQL is ready."

python manage.py migrate --noinput

exec "$@"
