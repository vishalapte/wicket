#!/usr/bin/env bash
# Install system dependencies that cannot be pip-installed.
# Idempotent: checks presence before installing.
# Called by `make system-deps` (which `make install` depends on).
#
# Usage: add one install_if_missing call per dependency.
# Example:
#   install_if_missing pdftotext poppler poppler-utils
#
# Arguments: <command-to-check> <brew-package> <apt-package>
set -euo pipefail

install_if_missing() {
    local cmd=$1 brew_pkg=$2 apt_pkg=$3
    if command -v "$cmd" >/dev/null 2>&1; then
        echo "✓ $cmd present ($(command -v "$cmd"))"
        return
    fi
    case "$(uname -s)" in
        Darwin)
            if ! command -v brew >/dev/null 2>&1; then
                echo "ERROR: Homebrew required but not found. Install from https://brew.sh" >&2
                exit 1
            fi
            echo "→ brew install $brew_pkg"
            brew install "$brew_pkg"
            ;;
        Linux)
            echo "→ apt-get install -y $apt_pkg"
            apt-get install -y "$apt_pkg"
            ;;
        *)
            echo "WARNING: unsupported platform $(uname -s); install $apt_pkg manually" >&2
            ;;
    esac
}

# Add project-specific system dependencies below.
# Format: install_if_missing <command> <brew-package> <apt-package>
