#!/usr/bin/env bash
#
# install_cron.sh — sync scripts/cron/ into the user crontab.
#
# Every executable in scripts/cron/ declares its own schedule in two comment
# lines near the top:
#
#     # cron: on          (or `off` — kept in the repo, kept out of crontab)
#     # schedule: 15 7 * * *
#
# The schedule then lives in version control next to the code it runs, which a
# crontab does not: a bare crontab is per-machine, unreviewed, and silently
# diverges from whatever anyone believes is scheduled.
#
# Writes only between its own markers and leaves every other crontab line
# alone, so this is safe to run on a machine with unrelated jobs and safe to
# run repeatedly — the block is rebuilt from scratch each time, so removing a
# file or flipping it to `off` removes the entry.
#
# Usage:
#   bash scripts/install_cron.sh            # show what would change, then apply
#   bash scripts/install_cron.sh --dry-run  # show only
#   bash scripts/install_cron.sh --list     # what each file declares
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CRON_DIR=$REPO/scripts/cron
# Namespaced by repo so two governed checkouts on one machine own separate
# blocks instead of silently overwriting each other's. Keyed on the directory
# name because that is what an adopter sees in the crontab and can match back
# to a tree.
REPO_NAME=$(basename "$REPO")
BEGIN="# BEGIN $REPO_NAME (managed by make cron — edits between these markers are overwritten)"
END="# END $REPO_NAME"

mode=${1:-apply}

[ -d "$CRON_DIR" ] || {
	echo "no $CRON_DIR — nothing to install"
	exit 0
}

# Read a `# key: value` declaration from the header. Header-only on purpose:
# scanning the whole file would match any line that merely talks about cron.
decl() { sed -n "1,40p" "$1" | sed -n "s/^# *$2: *//p" | head -1; }

rows=()
listing=""
for f in "$CRON_DIR"/*; do
	[ -f "$f" ] || continue
	case "$f" in *.md) continue ;; esac
	state=$(decl "$f" cron)
	sched=$(decl "$f" schedule)
	name=$(basename "$f")

	if [ -z "$state" ]; then
		listing+=$(printf '  %-28s %s\n' "$name" "(no 'cron:' declaration — skipped)")
		continue
	fi
	if [ "$state" != "on" ]; then
		listing+=$(printf '  %-28s %s\n' "$name" "off")$'\n'
		continue
	fi
	if [ -z "$sched" ]; then
		echo "error: $name declares 'cron: on' but no 'schedule:'" >&2
		exit 1
	fi
	if [ ! -x "$f" ]; then
		echo "error: $name is 'cron: on' but not executable — chmod +x it" >&2
		exit 1
	fi
	rows+=("$sched $f")
	listing+=$(printf '  %-28s %s\n' "$name" "on   $sched")$'\n'
done

if [ "$mode" = "--list" ]; then
	printf '%s\n' "$listing"
	exit 0
fi

block=$BEGIN$'\n'
for r in "${rows[@]:-}"; do
	[ -n "$r" ] && block+="$r"$'\n'
done
block+=$END

current=$(crontab -l 2>/dev/null || true)
# Strip any previous managed block, keeping everything else verbatim.
without=$(printf '%s\n' "$current" | awk -v b="$BEGIN" -v e="$END" '
  $0==b {skip=1; next} $0==e {skip=0; next} !skip {print}')

proposed=$(printf '%s\n%s\n' "$(printf '%s' "$without" | sed '/^$/d')" "$block")

echo "managed block:"
printf '%s\n' "$block" | sed 's/^/  /'

if [ "$mode" = "--dry-run" ]; then
	echo
	echo "(dry run — crontab not modified)"
	exit 0
fi

printf '%s\n' "$proposed" | crontab -
echo
echo "installed. verify with: crontab -l"
