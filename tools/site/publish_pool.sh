#!/usr/bin/env bash
# Publish one built book into a subject site that keeps its files under
# opaque names, and push.
#
#   publish_pool.sh BUILT_BOOK_DIR [SUBJECT_DIR]
#
# Needs the environment variables SUBJECT_KEY (the subject key) and
# TEMPLATE_TAG (the exact tag of the item UI). SUBJECT_DIR is a checkout of
# the subject site (default: the current folder). Run by the workflow in
# templates/publish-encrypted-book.yml, from the same tag that built the
# book.
#
# The commit message is always "Update" and nothing here prints a book name
# or a file name, because the subject site is public and so is anything
# that ends up in its history. When the push is refused the script starts
# again from the newest commit, which is safe because build_pool.py can be
# repeated (an unchanged file keeps its name and is not written again).
set -euo pipefail

BOOK_DIR="$(cd "$1" && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "${2:-.}"

: "${SUBJECT_KEY:?SUBJECT_KEY is not set}"
: "${TEMPLATE_TAG:?TEMPLATE_TAG is not set}"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

for attempt in 1 2 3 4 5; do
  python3 "$HERE/build_pool.py" --site . --template-tag "$TEMPLATE_TAG" \
    "$BOOK_DIR"
  git add -A
  if git diff --cached --quiet; then
    echo "Nothing changed"
    exit 0
  fi
  git commit -q -m "Update"
  if git push -q origin "HEAD:$BRANCH"; then
    exit 0
  fi
  echo "Push refused (attempt $attempt); starting again from the newest commit"
  git fetch -q origin "$BRANCH"
  git reset -q --hard "origin/$BRANCH"
  sleep $((attempt * 3))
done
echo "Could not push after 5 attempts" >&2
exit 1
