#!/bin/sh
# Download am-i-exposed's entity index (generated data, about 100 MB, that its repository keeps
# and this one does not) into web/public/data, from the upstream commit in web/UPSTREAM, and
# check it against web/public/data/entity-data.sha256. Files already there and right are kept.
set -eu
cd "$(dirname "$0")/../web"
read -r repo commit <UPSTREAM
raw="$(echo "$repo" | sed 's#^https://github.com/#https://raw.githubusercontent.com/#')/$commit/public/data"
cd public/data
while read -r sum file; do
    [ "$(sha256sum "$file" 2>/dev/null | cut -d' ' -f1)" = "$sum" ] && continue
    echo "entity-data.sh: $file"
    if command -v curl >/dev/null; then curl -fsSL -o "$file" "$raw/$file"; else wget -q -O "$file" "$raw/$file"; fi
done <entity-data.sha256
sha256sum -c entity-data.sha256
