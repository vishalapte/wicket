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
# (fetch, then a compare-and-swap of the worktree's branch, or a fast-forward of the branch a
# fold lands on); a dry run deletes the clone.
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
  pf_import_path
  return 0
}

pf_import_path() {
  # Puts the clone's package home first on PYTHONPATH, so every hook and the gate import the
  # package the clone holds. The linked venv may hold an editable install of the package, and
  # that install names the tree it was made from: the main checkout, whose code is not the
  # commit being tried. The home is the directory above racecar.mk's PKG (`src` for
  # `src/<pkg>`, the root for a flat `<pkg>`); with no PKG nothing is set.
  local pkg
  pkg=$(make -s --no-print-directory -C "$PF_CLONE" print-PKG 2>/dev/null) || return 0
  [ -n "$pkg" ] || return 0
  export PYTHONPATH="$PF_CLONE/$(dirname "$pkg")${PYTHONPATH:+:$PYTHONPATH}"
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
  # bump) is kept rather than overwritten. A new file is copied, and so is a path on disk
  # that your index does not hold: one you `git rm`-ed and made again, which `git diff` would
  # read only through the index, as a removal. A path missing from your tree is a removal only
  # when the start tracks it; anything else is a typo, and is refused.
  # Applied per commit, never all at once, so the clone holds only the commit being tried.
  local root="$1" base="$2" p on_disk indexed
  shift 2
  for p in "$@"; do
    case "$PF_APPLIED" in *$'\n'"$p"$'\n'*) continue ;; esac
    PF_APPLIED="$PF_APPLIED$p"$'\n'
    on_disk=0
    { [ -e "$root/$p" ] || [ -L "$root/$p" ]; } && on_disk=1
    indexed=0
    git -C "$root" ls-files --error-unmatch -- "$p" >/dev/null 2>&1 && indexed=1
    if git -C "$root" cat-file -e "$base:$p" 2>/dev/null && { [ "$indexed" = 1 ] || [ "$on_disk" = 0 ]; }; then
      # A path you did not edit (a CHANGELOG only the bookkeeping writes) has no diff.
      git -C "$root" diff --quiet "$base" -- "$p" && continue
      git -C "$root" diff --binary --no-renames "$base" -- "$p" | git apply --3way --whitespace=nowarn -
    elif [ "$on_disk" = 1 ]; then
      mkdir -p "$PF_CLONE/$(dirname "$p")"
      rm -f "$PF_CLONE/$p"
      cp -a "$root/$p" "$PF_CLONE/$p"
    else
      echo "preflight: refusing -- expected file missing: $p" >&2
      return 2
    fi
  done
}
PF_APPLIED=$'\n'

pf_pick() {
  # $1 = a commit to replay. Its change goes into the clone's tree and index, uncommitted, in
  # place of pf_apply. The changelog and the brief inventory go back to HEAD: the runbook
  # writes the commit's entries under [Unreleased] and regenerates the inventory, so a
  # conflict in either is expected and resolved that way. A conflict in any other path is
  # refused, and so is a pick that fails without one.
  local sha="$1" p others="" picked=0
  git cherry-pick --no-commit "$sha" >/dev/null 2>&1 && picked=1
  while IFS= read -r p; do
    case "$p" in
      CHANGELOG.md | docs/summary/*-INVENTORY.md) ;;
      *) others="$others  $p"$'\n' ;;
    esac
  done < <(git diff --name-only --diff-filter=U)
  if [ -n "$others" ]; then
    printf 'preflight: refusing -- replaying %s conflicts outside the changelog:\n%s' "$sha" "$others" >&2
    return 2
  fi
  if [ "$picked" = 0 ] && [ -z "$(git diff --name-only --diff-filter=U)" ]; then
    echo "preflight: refusing -- git could not replay $sha" >&2
    return 2
  fi
  while IFS= read -r p; do
    git cat-file -e "HEAD:$p" 2>/dev/null && git checkout -q HEAD -- "$p"
  done < <(printf '%s\n' CHANGELOG.md; git ls-files 'docs/summary/*-INVENTORY.md')
  # The replay is committed by the runbook, with its own message, so git's cherry-pick
  # state is dropped here.
  git cherry-pick --quit >/dev/null 2>&1 || true
  return 0
}

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
