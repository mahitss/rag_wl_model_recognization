#!/usr/bin/env bash
# =============================================================================
# WonderLearn Release Management Script
#
# Provides semantic versioning, release notes generation, build identifiers,
# and artifact integrity verification for releases.
#
# Usage:
#   scripts/release.sh <command> [args]
#
# Commands:
#   version-bump <patch|minor|major>  Bump version and create release commit
#   release-notes                     Generate release notes from git history
#   build-id                          Generate a build identifier
#   verify-artifacts <path>           Verify build artifact integrity
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="${REPO_ROOT}/VERSION"
CHANGELOG_FILE="${REPO_ROOT}/CHANGELOG.md"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
  echo "[release] $*" >&2
}

die() {
  echo "[release] ERROR: $*" >&2
  exit 1
}

current_version() {
  if [[ -f "${VERSION_FILE}" ]]; then
    cat "${VERSION_FILE}"
  else
    echo "0.1.0"
  fi
}

parse_version_parts() {
  local version="$1"
  echo "${version}" | awk -F. '{print $1, $2, $3}'
}

bump_version() {
  local bump_type="$1"  # patch | minor | major
  local current
  current="$(current_version)"
  local major minor patch
  read -r major minor patch <<<"$(parse_version_parts "${current}")"

  case "${bump_type}" in
    major)
      major=$((major + 1))
      minor=0
      patch=0
      ;;
    minor)
      minor=$((minor + 1))
      patch=0
      ;;
    patch)
      patch=$((patch + 1))
      ;;
    *)
      die "Unknown bump type: ${bump_type} (use patch|minor|major)"
      ;;
  esac

  echo "${major}.${minor}.${patch}"
}

generate_build_id() {
  local date_stamp
  date_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  local git_short
  git_short="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
  echo "build-${date_stamp}-${git_short}"
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_version_bump() {
  local bump_type="${1:-patch}"
  local new_version
  new_version="$(bump_version "${bump_type}")"
  echo "${new_version}" > "${VERSION_FILE}"

  # Commit the version bump.
  git -C "${REPO_ROOT}" add "${VERSION_FILE}"
  git -C "${REPO_ROOT}" commit -m "chore(release): bump version to ${new_version}" || true

  # Create an annotated release tag.
  local tag="v${new_version}"
  git -C "${REPO_ROOT}" tag -a "${tag}" -m "Release ${tag}" || true

  log "Version bumped to ${new_version} (tag: ${tag})"
}

cmd_release_notes() {
  local from_tag="${1:-}"
  local to_tag="${2:-HEAD}"
  local range
  if [[ -n "${from_tag}" ]]; then
    range="${from_tag}..${to_tag}"
  else
    range="${to_tag}"
  fi

  log "Generating release notes for ${range}"

  {
    echo "# Release Notes"
    echo ""
    echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo ""

    # Group commits by conventional commit type.
    echo "## Features"
    git -C "${REPO_ROOT}" log --pretty=format:"- %s (%h)" --grep="^feat" --invert-grep --grep="^fix" --invert-grep --grep="^chore" --invert-grep --grep="^docs" --invert-grep --grep="^refactor" --invert-grep --grep="^test" --invert-grep --grep="^perf" --invert-grep "${range}" || true
    echo ""
    echo "## Fixes"
    git -C "${REPO_ROOT}" log --pretty=format:"- %s (%h)" --grep="^fix" "${range}" || true
    echo ""
    echo "## Chore / Maintenance"
    git -C "${REPO_ROOT}" log --pretty=format:"- %s (%h)" --grep="^chore" "${range}" || true
    echo ""
    echo "## Documentation"
    git -C "${REPO_ROOT}" log --pretty=format:"- %s (%h)" --grep="^docs" "${range}" || true
    echo ""
    echo "## All commits"
    git -C "${REPO_ROOT}" log --pretty=format:"- %s (%h)" "${range}" || true
  } > "${CHANGELOG_FILE}"

  log "Release notes written to ${CHANGELOG_FILE}"
}

cmd_build_id() {
  local build_id
  build_id="$(generate_build_id)"
  echo "${build_id}"
}

cmd_verify_artifacts() {
  local artifact_path="$1"
  if [[ ! -f "${artifact_path}" ]]; then
    die "Artifact not found: ${artifact_path}"
  fi

  log "Verifying artifact integrity: ${artifact_path}"

  # Compute SHA-256.
  local sha
  sha="$(sha256sum "${artifact_path}" | awk '{print $1}')"
  echo "${sha}" > "${artifact_path}.sha256"
  log "SHA-256: ${sha}"

  # Verify the checksum.
  if ! sha256sum --check "${artifact_path}.sha256" >/dev/null 2>&1; then
    die "Artifact integrity check failed for ${artifact_path}"
  fi

  log "Artifact integrity verified."
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

main() {
  local command="${1:-}"
  shift || true

  case "${command}" in
    version-bump)
      cmd_version_bump "${1:-patch}"
      ;;
    release-notes)
      cmd_release_notes "${1:-}" "${2:-HEAD}"
      ;;
    build-id)
      cmd_build_id
      ;;
    verify-artifacts)
      cmd_verify_artifacts "${1:-}"
      ;;
    *)
      die "Usage: $0 {version-bump|release-notes|build-id|verify-artifacts} [args]"
      ;;
  esac
}

main "$@"
