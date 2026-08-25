#!/bin/sh
# Pull one complete, user-authorized Google Drive export into the scanner's
# `current` directory.  A failed/partial copy never replaces the last snapshot.
set -eu

remote_name="${IG_TRENDS_GDRIVE_REMOTE:-igdrive}"
remote_path="${IG_TRENDS_GDRIVE_PATH:-IG Exports/current}"
interval="${IG_TRENDS_GDRIVE_SYNC_INTERVAL_SECONDS:-900}"
incoming_root="/incoming"
stage_dir="${incoming_root}/.gdrive-stage"
current_dir="${incoming_root}/current"
previous_dir="${incoming_root}/.gdrive-previous"

if [ -z "$remote_name" ] || [ -z "$remote_path" ]; then
  echo "IG_TRENDS_GDRIVE_REMOTE and IG_TRENDS_GDRIVE_PATH must not be empty" >&2
  exit 2
fi

case "$interval" in
  *[!0-9]*|'')
    echo "IG_TRENDS_GDRIVE_SYNC_INTERVAL_SECONDS must be a positive integer" >&2
    exit 2
    ;;
esac

if [ "$interval" -eq 0 ]; then
  echo "IG_TRENDS_GDRIVE_SYNC_INTERVAL_SECONDS must be greater than zero" >&2
  exit 2
fi

has_complete_relationship_export() {
  find "$stage_dir" -type f \( -iname 'followers*.json' -o -iname 'following*.json' \) -print -quit | grep -q . \
    && find "$stage_dir" -type f -iname 'followers*.json' -print -quit | grep -q . \
    && find "$stage_dir" -type f -iname 'following*.json' -print -quit | grep -q .
}

while true; do
  rm -rf "$stage_dir"
  mkdir -p "$stage_dir"

  if rclone copy "${remote_name}:${remote_path}" "$stage_dir" \
      --checkers 4 --transfers 2 --retries 3 --low-level-retries 5; then
    if has_complete_relationship_export; then
      rm -rf "$previous_dir"
      if [ -d "$current_dir" ]; then
        mv "$current_dir" "$previous_dir"
      fi
      mv "$stage_dir" "$current_dir"
      rm -rf "$previous_dir"
      echo "Google Drive export staged successfully"
    else
      echo "Google Drive copy did not contain both followers and following JSON; keeping prior current export" >&2
    fi
  else
    echo "Google Drive copy failed; keeping prior current export" >&2
  fi

  sleep "$interval"
done
