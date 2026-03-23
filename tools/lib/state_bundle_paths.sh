#!/usr/bin/env bash
set -euo pipefail

# Filled by resolver functions for diagnostics/manifest.
STATE_BUNDLE_PATH_SOURCE=""
STATE_BUNDLE_PATH_REASON=""
STATE_BUNDLE_RESOLVED_PATH=""

_resolve_mount_source_from_running_container() {
  local env_file="$1"
  local compose_file="$2"
  local service="$3"
  local destination="$4"

  local container_id=""
  container_id="$(docker compose --env-file "${env_file}" -f "${compose_file}" ps -q "${service}" 2>/dev/null | head -n 1 || true)"
  if [[ -z "${container_id}" ]]; then
    return 0
  fi

  docker inspect --format '{{range .Mounts}}{{if eq .Destination "'"${destination}"'"}}{{.Source}}{{end}}{{end}}' "${container_id}" 2>/dev/null || true
}

resolve_qdrant_data_path() {
  local explicit_path="$1"
  local env_file="$2"
  local compose_file="$3"
  local service="$4"

  STATE_BUNDLE_PATH_SOURCE=""
  STATE_BUNDLE_PATH_REASON=""

  if [[ -n "${explicit_path}" ]]; then
    STATE_BUNDLE_PATH_SOURCE="override"
    STATE_BUNDLE_PATH_REASON="explicit_path"
    STATE_BUNDLE_RESOLVED_PATH="${explicit_path}"
    printf '%s\n' "${explicit_path}"
    return 0
  fi

  local inspected=""
  inspected="$(_resolve_mount_source_from_running_container "${env_file}" "${compose_file}" "${service}" "/qdrant/storage")"
  if [[ -n "${inspected}" ]]; then
    STATE_BUNDLE_PATH_SOURCE="docker_inspect"
    STATE_BUNDLE_PATH_REASON="running_container_mount"
    STATE_BUNDLE_RESOLVED_PATH="${inspected}"
    printf '%s\n' "${inspected}"
    return 0
  fi

  local candidates=("docker/data/qdrant" "data/qdrant")
  local candidate=""
  for candidate in "${candidates[@]}"; do
    if [[ -d "${candidate}" ]]; then
      STATE_BUNDLE_PATH_SOURCE="fallback_existing"
      STATE_BUNDLE_PATH_REASON="existing_directory"
      STATE_BUNDLE_RESOLVED_PATH="${candidate}"
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  STATE_BUNDLE_PATH_SOURCE="fallback_default"
  STATE_BUNDLE_PATH_REASON="default_candidate"
  STATE_BUNDLE_RESOLVED_PATH="${candidates[0]}"
  printf '%s\n' "${candidates[0]}"
}

resolve_mongo_data_path() {
  local explicit_path="$1"
  local env_file="$2"
  local compose_file="$3"
  local service="$4"

  STATE_BUNDLE_PATH_SOURCE=""
  STATE_BUNDLE_PATH_REASON=""

  if [[ -n "${explicit_path}" ]]; then
    STATE_BUNDLE_PATH_SOURCE="override"
    STATE_BUNDLE_PATH_REASON="explicit_path"
    STATE_BUNDLE_RESOLVED_PATH="${explicit_path}"
    printf '%s\n' "${explicit_path}"
    return 0
  fi

  local inspected=""
  inspected="$(_resolve_mount_source_from_running_container "${env_file}" "${compose_file}" "${service}" "/data/db")"
  if [[ -n "${inspected}" ]]; then
    STATE_BUNDLE_PATH_SOURCE="docker_inspect"
    STATE_BUNDLE_PATH_REASON="running_container_mount"
    STATE_BUNDLE_RESOLVED_PATH="${inspected}"
    printf '%s\n' "${inspected}"
    return 0
  fi

  local candidates=("docker/data" "data")
  local candidate=""
  for candidate in "${candidates[@]}"; do
    if [[ -d "${candidate}" ]]; then
      STATE_BUNDLE_PATH_SOURCE="fallback_existing"
      STATE_BUNDLE_PATH_REASON="existing_directory"
      STATE_BUNDLE_RESOLVED_PATH="${candidate}"
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  STATE_BUNDLE_PATH_SOURCE="fallback_default"
  STATE_BUNDLE_PATH_REASON="default_candidate"
  STATE_BUNDLE_RESOLVED_PATH="${candidates[0]}"
  printf '%s\n' "${candidates[0]}"
}

path_is_within() {
  local child="$1"
  local parent="$2"
  local child_norm parent_norm

  child_norm="$(realpath -m "${child}")"
  parent_norm="$(realpath -m "${parent}")"

  [[ "${child_norm}" == "${parent_norm}" || "${child_norm}" == "${parent_norm}/"* ]]
}
