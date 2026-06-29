#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${CODEGEN_OUTPUT_DIR:-${ROOT}/out/generated}"
WORK_DIR="${ROOT}/out/publish"

OWNER="${GITHUB_OWNER:-FutureTechQuant}"
BACKEND_REPO="${BACKEND_REPO:-ruoyi-vue-pro}"
FRONTEND_REPO="${FRONTEND_REPO:-yudao-ui-admin-vue3}"
BACKEND_BRANCH="${BACKEND_BRANCH:-master-jdk17}"
FRONTEND_BRANCH="${FRONTEND_BRANCH:-master}"
PUBLISH_MODE="${PUBLISH_MODE:-update_existing_repo_with_pr}"
PR_BRANCH_PREFIX="${PR_BRANCH_PREFIX:-codegen}"
TARGET_PRIVATE="${TARGET_PRIVATE:-false}"

# Source repositories used when explicitly rebuilding disposable/generated targets.
GITEE_BACKEND_URL="${GITEE_BACKEND_URL:-https://gitee.com/zhijiantianya/ruoyi-vue-pro.git}"
GITEE_BACKEND_BRANCH="${GITEE_BACKEND_BRANCH:-master-jdk17}"
GITEE_FRONTEND_URL="${GITEE_FRONTEND_URL:-https://gitee.com/yudaocode/yudao-ui-admin-vue3.git}"
GITEE_FRONTEND_BRANCH="${GITEE_FRONTEND_BRANCH:-master}"

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "ERROR: missing env ${name}"
    exit 1
  fi
}

repo_url() {
  local repo="$1"
  echo "https://x-access-token:${GH_TOKEN}@github.com/${OWNER}/${repo}.git"
}

api_auth_header() {
  printf '%s' "Authorization: Bearer ${GH_TOKEN}"
}

repo_exists() {
  local repo="$1"
  local status
  status=$(curl -sS -o /dev/null -w "%{http_code}" \
    -H "$(api_auth_header)" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/repos/${OWNER}/${repo}")
  [[ "${status}" == "200" ]]
}

delete_repo_if_exists() {
  local repo="$1"
  local url="https://api.github.com/repos/${OWNER}/${repo}"
  local status del_status

  echo "==> Check repo before rebuild: ${OWNER}/${repo}"
  status=$(curl -sS -o /dev/null -w "%{http_code}" \
    -H "$(api_auth_header)" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "${url}")

  if [[ "${status}" == "404" ]]; then
    echo "Repo ${OWNER}/${repo} not found, skip delete"
    return
  fi

  if [[ "${status}" != "200" ]]; then
    echo "ERROR: unexpected status when getting ${OWNER}/${repo}: ${status}"
    exit 1
  fi

  echo "==> Delete existing repo for rebuild_from_upstream: ${OWNER}/${repo}"
  del_status=$(curl -sS -o /dev/null -w "%{http_code}" \
    -X DELETE \
    -H "$(api_auth_header)" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "${url}")

  if [[ "${del_status}" == "204" ]]; then
    echo "Deleted repo ${OWNER}/${repo}"
  else
    echo "ERROR: failed to delete ${OWNER}/${repo}, status=${del_status}"
    exit 1
  fi
}

create_repo() {
  local repo="$1"
  local description="$2"
  local private_flag="${3:-${TARGET_PRIVATE}}"
  local payload create_status

  echo "==> Create repo ${OWNER}/${repo}"
  payload=$(jq -n \
    --arg name "${repo}" \
    --arg desc "${description}" \
    --argjson private "${private_flag}" \
    '{name: $name, private: $private, description: $desc, auto_init: false}')

  create_status=$(curl -sS -o /tmp/create-"${repo}".json -w "%{http_code}" \
    -X POST \
    -H "$(api_auth_header)" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -H "Content-Type: application/json" \
    -d "${payload}" \
    "https://api.github.com/orgs/${OWNER}/repos")

  if [[ "${create_status}" != "201" ]]; then
    echo "ERROR: failed to create repo ${OWNER}/${repo}, status=${create_status}"
    cat /tmp/create-"${repo}".json || true
    exit 1
  fi

  echo "Created repo ${OWNER}/${repo}"
}

bootstrap_repo_from_gitee() {
  local repo="$1"
  local gitee_url="$2"
  local gitee_branch="$3"
  local description="$4"

  local tmp_work="${WORK_DIR}/gitee-working-${repo}"
  local tmp_init="${WORK_DIR}/init-${repo}"

  rm -rf "${tmp_work}" "${tmp_init}"
  mkdir -p "${tmp_work}" "${tmp_init}"

  echo "==> Shallow clone from Gitee: ${gitee_url} (branch ${gitee_branch})"
  git clone --depth 1 --branch "${gitee_branch}" "${gitee_url}" "${tmp_work}"
  rm -rf "${tmp_work}/.git"

  echo "==> Prepare initial Git history for ${repo}"
  cp -R "${tmp_work}/." "${tmp_init}/"

  cd "${tmp_init}"
  git init -b "${gitee_branch}"
  git add -A
  git commit -m "chore: bootstrap from Gitee ${gitee_branch}"

  create_repo "${repo}" "${description}" "${TARGET_PRIVATE}"

  git remote add origin "$(repo_url "${repo}")"
  echo "==> Push initial snapshot to GitHub ${OWNER}/${repo} (${gitee_branch})"
  git push -u origin "${gitee_branch}"
}

clone_target() {
  local repo="$1"
  local dir="$2"
  local branch="${3:-}"

  rm -rf "${dir}"

  if [[ -n "${branch}" ]] && git ls-remote --heads "$(repo_url "${repo}")" "${branch}" | grep -q "refs/heads/${branch}"; then
    echo "Cloning ${repo} branch ${branch}"
    git clone --branch "${branch}" --single-branch "$(repo_url "${repo}")" "${dir}"
  else
    echo "Cloning ${repo} default branch"
    git clone "$(repo_url "${repo}")" "${dir}"
  fi
}

sync_generated_code() {
  local backend_dir="$1"
  local frontend_dir="$2"

  python3 "${ROOT}/tools/codegen/publish_sync.py" \
    --generated-dir "${OUT_DIR}" \
    --backend-root "${backend_dir}" \
    --frontend-root "${frontend_dir}" \
    --publish-mode "${PUBLISH_MODE}" \
    --target-owner "${OWNER}" \
    --backend-repo "${BACKEND_REPO}" \
    --frontend-repo "${FRONTEND_REPO}" \
    --backend-branch "${BACKEND_BRANCH}" \
    --frontend-branch "${FRONTEND_BRANCH}" \
    --split-config "${SPLIT_API_BIZ_CONFIG:-${ROOT}/tools/codegen/split_api_biz.yml}"
}

commit_and_push() {
  local dir="$1"
  local msg="$2"
  local branch="$3"

  cd "${dir}"
  git add -A

  if git diff --cached --quiet; then
    echo "No changes: ${dir}"
    return 1
  fi

  git commit -m "${msg}"
  git push -u origin "HEAD:${branch}"
}

create_pr_if_possible() {
  local repo="$1"
  local head_branch="$2"
  local base_branch="$3"
  local title="$4"
  local body_file="$5"

  if ! command -v gh >/dev/null 2>&1; then
    echo "WARN: gh not found; pushed branch ${head_branch} but did not create PR for ${OWNER}/${repo}"
    return 0
  fi

  echo "==> Create or reuse PR for ${OWNER}/${repo}: ${head_branch} -> ${base_branch}"
  if gh pr list \
      --repo "${OWNER}/${repo}" \
      --head "${head_branch}" \
      --base "${base_branch}" \
      --json number \
      --jq '.[0].number // empty' | grep -q '^[0-9]'; then
    echo "PR already exists for ${OWNER}/${repo}:${head_branch}"
    return 0
  fi

  gh pr create \
    --repo "${OWNER}/${repo}" \
    --head "${head_branch}" \
    --base "${base_branch}" \
    --title "${title}" \
    --body-file "${body_file}"
}

write_pr_body() {
  local file="$1"
  cat > "${file}" <<EOF
## Summary

Sync generated RuoYi/Yudao code from codegen-bot.

## Mode

\`${PUBLISH_MODE}\`

## Review checklist

- [ ] Confirm generated backend module boundaries.
- [ ] Confirm generated frontend pages/routes/permissions.
- [ ] Review app/user controllers for unsafe admin API exposure.
- [ ] Review tenant/user/data-permission boundaries.
- [ ] Confirm generated manifest/report are accurate.
- [ ] Run relevant backend/frontend builds in the target repositories.

## Generated artifacts

- \`generated/manifest.json\`
- \`generated/codegen-report.md\`
EOF
}

validate_mode() {
  case "${PUBLISH_MODE}" in
    rebuild_from_upstream|update_existing_repo_with_pr) ;;
    *)
      echo "ERROR: unsupported PUBLISH_MODE=${PUBLISH_MODE}; expected rebuild_from_upstream or update_existing_repo_with_pr"
      exit 1
      ;;
  esac
}

git config --global user.name "future-codegen-bot"
git config --global user.email "actions@users.noreply.github.com"

require_env GH_TOKEN
validate_mode
mkdir -p "${WORK_DIR}"

BACKEND_DIR="${WORK_DIR}/${BACKEND_REPO}"
FRONTEND_DIR="${WORK_DIR}/${FRONTEND_REPO}"

case "${PUBLISH_MODE}" in
  rebuild_from_upstream)
    echo "==> Publish mode: rebuild_from_upstream"
    echo "==> Delete existing GitHub repos if any"
    delete_repo_if_exists "${BACKEND_REPO}"
    delete_repo_if_exists "${FRONTEND_REPO}"

    echo "==> Bootstrap new repos from Gitee working tree"
    bootstrap_repo_from_gitee "${BACKEND_REPO}" "${GITEE_BACKEND_URL}" "${GITEE_BACKEND_BRANCH}" "Backend repo synced from Gitee branch ${GITEE_BACKEND_BRANCH} + codegen"
    bootstrap_repo_from_gitee "${FRONTEND_REPO}" "${GITEE_FRONTEND_URL}" "${GITEE_FRONTEND_BRANCH}" "Frontend repo synced from Gitee branch ${GITEE_FRONTEND_BRANCH} + codegen"

    echo "==> Clone recreated GitHub repos"
    clone_target "${BACKEND_REPO}" "${BACKEND_DIR}" "${BACKEND_BRANCH}"
    clone_target "${FRONTEND_REPO}" "${FRONTEND_DIR}" "${FRONTEND_BRANCH}"

    echo "==> Sync generated code into GitHub repos"
    sync_generated_code "${BACKEND_DIR}" "${FRONTEND_DIR}"

    echo "==> Commit and push generated code"
    commit_and_push "${BACKEND_DIR}" "chore: sync generated backend code" "${BACKEND_BRANCH}" || true
    commit_and_push "${FRONTEND_DIR}" "chore: sync generated frontend code" "${FRONTEND_BRANCH}" || true
    ;;

  update_existing_repo_with_pr)
    echo "==> Publish mode: update_existing_repo_with_pr"
    if ! repo_exists "${BACKEND_REPO}"; then
      echo "ERROR: backend repo ${OWNER}/${BACKEND_REPO} does not exist. Use rebuild_from_upstream for disposable/generated rebuilds."
      exit 1
    fi
    if ! repo_exists "${FRONTEND_REPO}"; then
      echo "ERROR: frontend repo ${OWNER}/${FRONTEND_REPO} does not exist. Use rebuild_from_upstream for disposable/generated rebuilds."
      exit 1
    fi

    clone_target "${BACKEND_REPO}" "${BACKEND_DIR}" "${BACKEND_BRANCH}"
    clone_target "${FRONTEND_REPO}" "${FRONTEND_DIR}" "${FRONTEND_BRANCH}"

    ts="$(date -u +%Y%m%d%H%M%S)"
    BACKEND_PR_BRANCH="${PR_BRANCH_PREFIX}/backend-${ts}"
    FRONTEND_PR_BRANCH="${PR_BRANCH_PREFIX}/frontend-${ts}"

    (cd "${BACKEND_DIR}" && git checkout -B "${BACKEND_PR_BRANCH}")
    (cd "${FRONTEND_DIR}" && git checkout -B "${FRONTEND_PR_BRANCH}")

    echo "==> Sync generated code into existing repos"
    sync_generated_code "${BACKEND_DIR}" "${FRONTEND_DIR}"

    body_file="${WORK_DIR}/codegen-pr-body.md"
    write_pr_body "${body_file}"

    if commit_and_push "${BACKEND_DIR}" "chore: sync generated backend code" "${BACKEND_PR_BRANCH}"; then
      create_pr_if_possible "${BACKEND_REPO}" "${BACKEND_PR_BRANCH}" "${BACKEND_BRANCH}" "chore: sync generated backend code" "${body_file}"
    fi

    if commit_and_push "${FRONTEND_DIR}" "chore: sync generated frontend code" "${FRONTEND_PR_BRANCH}"; then
      create_pr_if_possible "${FRONTEND_REPO}" "${FRONTEND_PR_BRANCH}" "${FRONTEND_BRANCH}" "chore: sync generated frontend code" "${body_file}"
    fi
    ;;
esac

echo "Done"
