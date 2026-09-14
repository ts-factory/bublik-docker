#!/bin/sh
# Emit git metadata for a repository as shell-quoted `export` lines.
#
# Usage: ./scripts/git_version_env.sh <repo-dir> <PREFIX>
#   e.g. eval "$(./scripts/git_version_env.sh ./bublik BUBLIK)"
#
# The Docker build context cannot see git: .dockerignore strips `.git`, and every
# submodule's `.git` is a file pointing into the superproject's .git/modules/.
# So git data is captured on the host here and injected as build args.
#
# This is the Docker-native equivalent of check_repo_revisions() in
# bublik/scripts/deploy, which sed-patches settings.py on bare-metal deploys.
#
# Emits nothing (exit 0) when <repo-dir> is not a git repository, so callers
# degrade to the 'null' placeholders instead of failing the build.

set -u

repo="${1:-.}"
prefix="${2:-BUBLIK}"

git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || exit 0

# Single-quote a value for safe `eval`: ' -> '\''
quote() {
	printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

emit() {
	printf 'export %s_%s=%s\n' "$prefix" "$1" "$(quote "$2")"
}

git_out() {
	git -C "$repo" "$@" 2>/dev/null || true
}

# --- remote url, normalised to a browsable https:// form ------------------
url=$(git_out remote get-url origin)
case "$url" in
	git@*)
		# git@host:org/repo(.git) -> https://host/org/repo
		url=$(printf '%s' "$url" | sed -e 's|^git@||' -e 's|:|/|')
		url="https://$url"
		;;
	ssh://git@*)
		url=$(printf '%s' "$url" | sed -e 's|^ssh://git@|https://|')
		;;
esac
url=${url%.git}
# Collapse ssh-config host aliases such as github.com-<account> back to the real host.
url=$(printf '%s' "$url" | sed -E 's#^https://(github\.com|gitlab\.com|bitbucket\.org)-[A-Za-z0-9._-]+/#https://\1/#')

# --- branch ---------------------------------------------------------------
# CI (actions/checkout with submodules: true) leaves submodules detached, where
# `rev-parse --abbrev-ref HEAD` yields the literal string "HEAD". Emit an empty
# branch instead; formatVersion() in the UI renders that cleanly.
branch=$(git_out rev-parse --abbrev-ref HEAD)
[ "$branch" = 'HEAD' ] && branch=''

rev=$(git_out rev-parse --short HEAD)
date=$(git_out show -s --format=%cI HEAD)
# Strip newlines: values must stay single-line for $GITHUB_ENV and --build-arg.
summary=$(git_out show -s --format=%s HEAD | tr -d '\r\n')

# --- tag ------------------------------------------------------------------
# Clean tree sitting exactly on a tag -> that release tag. Anything else is a
# local build: "dev", or "dev-dirty" when tracked files are modified.
dirty=''
git -C "$repo" diff --quiet HEAD -- 2>/dev/null || dirty='-dirty'

tag=''
if [ -z "$dirty" ]; then
	tag=$(git -C "$repo" describe --tags --exact-match HEAD 2>/dev/null || true)
fi
[ -n "$tag" ] || tag="dev${dirty}"

emit REPO_URL "$url"
emit REPO_BRANCH "$branch"
emit REPO_TAG "$tag"
emit COMMIT_REV "$rev"
emit COMMIT_DATE "$date"
emit COMMIT_SUMMARY "$summary"
emit BUILD_DATE "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
