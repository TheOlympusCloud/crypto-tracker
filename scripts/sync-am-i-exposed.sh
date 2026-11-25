#!/usr/bin/env bash
# Merge am-i-exposed's changes since the commit recorded in web/UPSTREAM into web/, as a
# three-way merge with this repository's own changes. Run it from a clean working tree:
#
#   scripts/sync-am-i-exposed.sh [tag-or-branch]      # default: main
#
# Only upstream's two commits are fetched, never its history. Conflicts are left in the files,
# as git leaves them; web/UPSTREAM and the entity index checksums move to the new commit.
set -euo pipefail

die() {
    echo "sync-am-i-exposed.sh: $*" >&2
    exit 1
}

cd "$(git rev-parse --show-toplevel)"
[ -z "$(git status --porcelain)" ] || die "commit or set aside your changes first"
read -r repo old <web/UPSTREAM
git fetch -q --depth 1 --no-tags "$repo" "$old"
git fetch -q --depth 1 --no-tags "$repo" "${1:-main}"
new=$(git rev-parse FETCH_HEAD)
if [ "$new" = "$old" ]; then
    echo "web/ is at $new already."
    exit 0
fi

# What this repository leaves out of upstream (README, "Built on am-i-exposed").
skip=(':!CLAUDE.md' ':!docs/superpowers' ':!.github' ':!public/media' ':!.cache' ':!docs/archive' ':!public/data/*.bin')
merged=yes
git diff --binary "$old" "$new" -- . "${skip[@]}" | git apply -3 --directory=web || merged=no

printf '%s %s\n' "$repo" "$new" >web/UPSTREAM
sums=web/public/data/entity-data.sha256
while read -r _ file; do
    printf '%s  %s\n' "$(git show "$new:public/data/$file" | sha256sum | cut -d' ' -f1)" "$file"
done <"$sums" >"$sums.new"
mv "$sums.new" "$sums"

cat <<EOF

web/ now holds am-i-exposed's changes from ${old:0:12} to ${new:0:12}.
$([ "$merged" = yes ] && echo "They merged without conflicts." || echo "Some conflict: resolve the files 'git status' lists as unmerged.")
web/pnpm-lock.yaml: on a conflict take either side, then 'corepack pnpm install' in web/ rebuilds it.
Then, in web/: corepack pnpm install, lint, type-check, test and build; scripts/entity-data.sh
fetches the new entity index. Commit it all as one merge of am-i-exposed.
EOF
[ "$merged" = yes ]
