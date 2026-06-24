# Makefile — owned root. YOU own this file; racecar never rewrites it.
#
# The canonical racecar build (every standard target + the shape-derived
# variables) lives in racecar.mk, which racecar regenerates for this repo's
# detected shape on `make sync`. `make help` lists every target from both files.
#
# Customize here, never in racecar.mk:
#   - add a project-specific target below the include;
#   - override a canonical recipe by redefining it below (last definition wins);
#   - override a shape variable by setting it with := ABOVE the include
#     (racecar.mk assigns shape vars with ?=, so an earlier := wins).

# shape=src: racecar.mk defaults PKG to SRC (= src), but the package lives at
# src/wicket and racecar.mk cannot know the package name from the layout alone.
# check_cli_commands / coverage need the package dir, so set it here (above the
# include; racecar.mk uses ?= so this := wins).
PKG := src/wicket

include racecar.mk

# --- project-specific targets ---
# Otherwise stdlib-only with shape=src; racecar.mk's detected defaults
# (SRC=src, LIB_PYPROJECT=pyproject.toml) are exactly right.
