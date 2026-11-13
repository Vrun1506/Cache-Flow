#!/bin/sh
# Delete session logs (final-plan.md §15.5). Logs hold timings and hashes, never raw
# student text by default, but they are still deleted after the event.
#
#   ops/purge-logs.sh [--dir DIR] [--days N] [--all] [--yes]
#
#   --dir DIR   directory that holds session-*.jsonl (default: <backend>/logs)
#   --days N    only files not modified for more than N days (default 7)
#   --all       every session log, whatever its age
#   --yes       actually delete. Without it the script only lists what it would delete.
#
# Only regular files named session-*.jsonl directly inside DIR are ever touched:
# no sub-directories, no symlinks, nothing outside DIR.
set -eu

here=$(cd "$(dirname "$0")" && pwd -P)
dir="$here/../logs"
days=7
all=0
yes=0

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) [ $# -ge 2 ] || { echo "purge-logs: --dir needs a value" >&2; exit 2; }; dir=$2; shift 2 ;;
    --days) [ $# -ge 2 ] || { echo "purge-logs: --days needs a value" >&2; exit 2; }; days=$2; shift 2 ;;
    --all) all=1; shift ;;
    --yes) yes=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "purge-logs: unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$days" in
  ''|*[!0-9]*) echo "purge-logs: --days must be a non-negative integer" >&2; exit 2 ;;
esac

[ -d "$dir" ] || { echo "purge-logs: not a directory: $dir" >&2; exit 2; }
dir=$(cd "$dir" && pwd -P)

# Refuse anything that is not clearly a log directory.
case "$dir" in
  /|/bin|/etc|/usr|/var|/tmp|/home|/Users|/private|/private/etc|/private/tmp|/private/var|"${HOME:-/nonexistent}")
    echo "purge-logs: refusing to operate on $dir" >&2; exit 2 ;;
esac

cd "$dir"
if [ "$all" -eq 1 ]; then
  list=$(find . ! -name . -prune -type f -name 'session-*.jsonl' -print)
else
  list=$(find . ! -name . -prune -type f -name 'session-*.jsonl' -mtime "+$days" -print)
fi

if [ -z "$list" ]; then
  echo "purge-logs: nothing to delete in $dir"
  exit 0
fi

count=0
for f in $list; do
  name=${f#./}
  case "$name" in
    */*|'') continue ;;  # never anything but a plain file name in DIR
  esac
  count=$((count + 1))
  if [ "$yes" -eq 1 ]; then
    rm -f -- "$name"
    echo "deleted: $name"
  else
    echo "would delete: $name"
  fi
done

if [ "$yes" -eq 1 ]; then
  echo "purge-logs: deleted $count file(s) from $dir"
else
  echo "purge-logs: dry run, $count file(s) would be deleted from $dir (use --yes)"
fi
