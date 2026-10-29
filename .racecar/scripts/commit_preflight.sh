#!/usr/bin/env bash
# racecar commit trial: the functions a /racecar-commit runbook sources to try its commits in a
# throwaway clone of the worktree it runs in, never in the worktree itself.
#
# THE ONE DEFINITION of how racecar tries a commit. There is no other way in: a hand check is
# the runbook's --dry-run (`/racecar-commit --dry-run`), which runs the whole trial and lands
# nothing. Run directly, this file refuses (exit 2) and says so.
#
# WHY A CLONE. In your checkout, pre-commit stashes your unstaged changes for its run, and a
# hook that reads across files judges a commit against files other commits carry. A clone
# holds exactly the commit being tried, so nothing is stashed and every hook judges the commit
# as it will land. And a clone has its own branches and tags, where a second worktree would
# share yours: whatever the trial does stays in it. A live run brings the tried commits back
# (fetch, then a compare-and-swap of the worktree's branch); a dry run deletes the clone.
#
# bash 3.2 compatible (no mapfile, no associative arrays).

pf_find_venv() {
  # $1 = root. The venv make picks there (arch-python/MAKEFILE.md §1), relative to the root,
  # when it exists and a clone can link it at the same relative path; else nothing. An
  # absolute RC_VENV needs no link: the clone's make reads the same RC_VENV.
  local d
  d=$(make -s --no-print-directory -C "$1" print-RC_VENV 2>/dev/null) || return 0
  case "$d" in "" | /* | ../../*) return 0 ;; esac
  [ -d "$1/$d" ] && echo "$d"
  return 0
}

pf_clone() {
  # $1 = root (the worktree the runbook runs in), $2 = base commit, $3 = branch to create, $4 =
  # scratch dir. Sets PF_CLONE. A clone has its own branches and tags, so nothing done in it
  # reaches the repo until the caller fetches from it; its remote still fetches but cannot
  # push. Git does not carry untracked environments, so the root's venv and every
  # `.collections` dir (an Ansible collection cache) are linked in at the same paths.
  local root="$1" base="$2" branch="$3" scratch="$4" v c rel
  PF_CLONE="$scratch/clone"
  git clone -q --local --no-checkout "$root" "$PF_CLONE"
  git -C "$PF_CLONE" remote set-url --push origin "no-push:the-trial-never-pushes"
  # A clone never copies `.git/hooks`. Its hooks directory becomes the worktree's, so a
  # commit here runs exactly what a commit in the worktree runs, whether git reaches the
  # hooks directly or through a global core.hooksPath dispatcher that reads it.
  # Nor does it copy repo-local config. The worktree's user.* (name, email, signing key) is
  # copied in, so a commit here carries the identity a commit in the worktree carries.
  { git -C "$root" config --local --get-regexp '^user\.' || true; } | while read -r key value; do
    git -C "$PF_CLONE" config "$key" "$value"
  done
  rm -rf "$PF_CLONE/.git/hooks"
  ln -s "$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)/hooks" \
    "$PF_CLONE/.git/hooks"
  git -C "$PF_CLONE" checkout -q -b "$branch" "$base"
  # The root first, then the main working tree: a runbook's root is a worktree, which shares
  # the main checkout's .git but none of its untracked environment.
  for src in "$root" "$(dirname "$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)")"; do
    v=$(pf_find_venv "$src")
    [ -z "$v" ] || [ -e "$PF_CLONE/$v" ] || ln -s "$src/$v" "$PF_CLONE/$v"
    while IFS= read -r c; do
      rel=${c#"$src"/}
      [ -e "$PF_CLONE/$rel" ] || { mkdir -p "$PF_CLONE/$(dirname "$rel")"; ln -s "$c" "$PF_CLONE/$rel"; }
    done < <(find "$src" -maxdepth 3 -name .collections -not -path "*/.git/*" -prune 2>/dev/null)
  done
  return 0
}

pf_remove() {
  # Deletes the clone, and with it everything the trial made.
  [ -n "${PF_CLONE:-}" ] || return 0
  rm -rf "$PF_CLONE"
}

pf_trunk() {
  # $1 = root, $2 = python, $3 = delivered scripts dir, $4 = the branch landed on, $5 = the
  # clone's branch. The version gate lets only the trunk move a version. When the branch
  # landed on is the trunk, the clone's branch stands in for it, so it is declared the
  # trunk. Asked of check_version_bump itself, the one home for which branch the trunk is.
  local trunk=""
  [ -f "$3/check_version_bump.py" ] || return 0
  trunk="$(cd "$1" && "$2" -B -c 'import sys; from pathlib import Path;
sys.path.insert(0, sys.argv[1]); import check_version_bump as c;
print(c.trunk_branch(Path(".")))' "$3")" || return 0
  if [ -n "$trunk" ] && [ "$4" = "$trunk" ]; then export RACECAR_TRUNK="$5"; fi
  return 0
}

pf_apply() {
  # $1 = root, $2 = the commit the clone started from, $3.. = one commit's paths. Brings
  # your edits to each path into the clone, the first time any commit lists it: as a patch
  # against the start, so bookkeeping the clone already holds (a CHANGELOG entry, a version
  # bump) is kept rather than overwritten. A new file is copied. A path missing from your tree
  # is a removal only when the start tracks it; anything else is a typo, and is refused.
  # Applied per commit, never all at once, so the clone holds only the commit being tried.
  local root="$1" base="$2" p
  shift 2
  for p in "$@"; do
    case "$PF_APPLIED" in *$'\n'"$p"$'\n'*) continue ;; esac
    PF_APPLIED="$PF_APPLIED$p"$'\n'
    if git -C "$root" cat-file -e "$base:$p" 2>/dev/null; then
      # A path you did not edit (a CHANGELOG only the bookkeeping writes) has no diff.
      git -C "$root" diff --quiet "$base" -- "$p" && continue
      git -C "$root" diff --binary --no-renames "$base" -- "$p" | git apply --3way --whitespace=nowarn -
    elif [ -e "$root/$p" ] || [ -L "$root/$p" ]; then
      mkdir -p "$PF_CLONE/$(dirname "$p")"
      cp -a "$root/$p" "$PF_CLONE/$p"
    else
      echo "preflight: refusing -- expected file missing: $p" >&2
      return 2
    fi
  done
}
PF_APPLIED=$'\n'

pf_commit() {
  # $1 = git commit's edit flag ("-e" or ""), $2 = message file, $3.. = the commit's paths,
  # already staged. A plain `git commit`: the clone runs the worktree's own installed hooks
  # (pf_clone links them in), so every hook runs once, exactly as on a commit made in
  # the worktree. A pre-commit hook that fixes a file fails the commit before any editor
  # opens; the fix is re-staged and the same commit retried, to a fixpoint (at most 3
  # passes). A failure that fixed nothing, a commit-msg refusal included, is final.
  local edit="$1" msg="$2" pass=1
  shift 2
  while ! git commit ${edit:+"$edit"} -F "$msg"; do
    [ "$pass" -lt 3 ] || return 1
    git diff --quiet -- "$@" && return 1
    git add -A -- "$@"
    pass=$((pass + 1))
  done
}

pf_sync() {
  # $1 = root, $2 = the commit the run started from. After a landing, every path the run
  # committed takes, in your index and tree, the content that landed: your edits plus the
  # bookkeeping and autofixes made in the clone. Paths the run did not commit are untouched.
  local root="$1" from="$2" p
  while IFS= read -r p; do
    if git -C "$root" cat-file -e "HEAD:$p" 2>/dev/null; then
      git -C "$root" checkout -q HEAD -- "$p" || {
        echo "preflight: landed, but $p did not sync into your tree; run git status" >&2
        return 1
      }
    else
      git -C "$root" rm -q --cached --ignore-unmatch -- "$p" >/dev/null
      rm -f "$root/$p"
    fi
  done < <(git -C "$root" diff --name-only --no-renames "$from" HEAD)
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  echo "commit_preflight.sh is sourced by a /racecar-commit runbook; to try a commit without" >&2
  echo "landing it, run that runbook with --dry-run (/racecar-commit --dry-run)." >&2
  exit 2
fi
