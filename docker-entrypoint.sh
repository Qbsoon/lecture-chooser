#!/bin/sh
set -e

# Przy pierwszym starcie bind mount jest pusty — seeduj scraped/
# z kopii zapasowej z obrazu.
SCRAPED="/lecture-chooser/app/data/scraped"
BACKUP="/opt/scraped-backup"

if [ -d "$BACKUP" ] && [ -z "$(ls -A "$SCRAPED" 2>/dev/null)" ]; then
    cp -a "$BACKUP/." "$SCRAPED/"
fi

exec "$@"
