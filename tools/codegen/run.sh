#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUOYI_DIR="${ROOT_DIR}/ruoyi"
BASE_SQL_ROOT="${BASE_SQL_ROOT:-${ROOT_DIR}/ruoyi-vue-pro-mysql}"
BASE_SQL_FILES="${BASE_SQL_FILES:-ruoyi-vue-pro.sql}"

MYSQL_HOST="${MYSQL_HOST:-127.0.0.1}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_USER="${MYSQL_USER:-root}"
MYSQL_PWD="${MYSQL_PWD:-root}"

CODEGEN_DB_NAME="${CODEGEN_DB_NAME:-ruoyi-vue-pro}"
CODEGEN_MODULE_NAME="${CODEGEN_MODULE_NAME:-}"
CODEGEN_TABLE_PREFIX="${CODEGEN_TABLE_PREFIX:-}"
CODEGEN_MODULES="${CODEGEN_MODULES:-}"
CODEGEN_BASE_PACKAGE="${CODEGEN_BASE_PACKAGE:-cn.iocoder.yudao}"
CODEGEN_OUTPUT_DIR="${CODEGEN_OUTPUT_DIR:-${ROOT_DIR}/out/generated}"

DB_URL="${DB_URL:-jdbc:mysql://${MYSQL_HOST}:${MYSQL_PORT}/${CODEGEN_DB_NAME}?useSSL=false&serverTimezone=Asia/Shanghai&allowPublicKeyRetrieval=true&rewriteBatchedStatements=true&nullCatalogMeansCurrent=true}"
DB_USER="${DB_USER:-${MYSQL_USER}}"
DB_PWD="${DB_PWD:-${MYSQL_PWD}}"

mysql_exec() {
  MYSQL_PWD="${MYSQL_PWD}" mysql \
    -h"${MYSQL_HOST}" \
    -P"${MYSQL_PORT}" \
    -u"${MYSQL_USER}" \
    --default-character-set=utf8mb4 \
    "$@"
}

echo "== prepare output dir =="
rm -rf "${CODEGEN_OUTPUT_DIR}"
mkdir -p "${CODEGEN_OUTPUT_DIR}"

echo "== recreate database ${CODEGEN_DB_NAME} =="
mysql_exec -e "DROP DATABASE IF EXISTS \`${CODEGEN_DB_NAME}\`;"
mysql_exec -e "CREATE DATABASE \`${CODEGEN_DB_NAME}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

echo "== import ruoyi base sql =="
if [[ ! -d "${BASE_SQL_ROOT}" ]]; then
  echo "ERROR: missing base sql root: ${BASE_SQL_ROOT}"
  exit 1
fi

BASE_SQL_LIST=()
IFS=',' read -r -a BASE_SQL_SPECS <<< "${BASE_SQL_FILES}"
for spec in "${BASE_SQL_SPECS[@]}"; do
  spec="${spec//[[:space:]]/}"
  [[ -z "${spec}" ]] && continue
  if [[ "${spec}" == "*.sql" ]]; then
    while IFS= read -r f; do
      BASE_SQL_LIST+=("${f}")
    done < <(find "${BASE_SQL_ROOT}" -maxdepth 1 -type f -name '*.sql' | sort)
  else
    BASE_SQL_LIST+=("${BASE_SQL_ROOT}/${spec}")
  fi
done

if [[ ${#BASE_SQL_LIST[@]} -eq 0 ]]; then
  echo "ERROR: no base sql files resolved from BASE_SQL_FILES=${BASE_SQL_FILES}"
  exit 1
fi

for base_sql in "${BASE_SQL_LIST[@]}"; do
  if [[ ! -f "${base_sql}" ]]; then
    echo "ERROR: missing base sql: ${base_sql}"
    exit 1
  fi
  echo "import base -> ${base_sql}"
  mysql_exec "${CODEGEN_DB_NAME}" < "${base_sql}"
done

echo "== snapshot base tables =="
mapfile -t BASE_TABLES < <(
  mysql_exec -N -B -e "
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = '${CODEGEN_DB_NAME}'
      AND table_type = 'BASE TABLE'
    ORDER BY table_name
  "
)

echo "== import business sql from this repo =="
SQL_ROOT="${ROOT_DIR}/sql/schema"
if [[ ! -d "${SQL_ROOT}" ]]; then
  echo "ERROR: missing business sql dir: ${SQL_ROOT}"
  exit 1
fi

mapfile -t SQL_FILES < <(find "${SQL_ROOT}" -type f -name '*.sql' | sort)

if [[ ${#SQL_FILES[@]} -eq 0 ]]; then
  echo "ERROR: no sql files found under ${SQL_ROOT}"
  exit 1
fi

for f in "${SQL_FILES[@]}"; do
  echo "import -> ${f}"
  mysql_exec "${CODEGEN_DB_NAME}" < "${f}"
done

echo "== snapshot all tables after business import =="
mapfile -t ALL_TABLES < <(
  mysql_exec -N -B -e "
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = '${CODEGEN_DB_NAME}'
      AND table_type = 'BASE TABLE'
    ORDER BY table_name
  "
)

echo "== detect newly added business tables =="
declare -A BASE_TABLE_SET=()
for t in "${BASE_TABLES[@]}"; do
  BASE_TABLE_SET["$t"]=1
done

NEW_TABLES=()
for t in "${ALL_TABLES[@]}"; do
  if [[ -z "${BASE_TABLE_SET[$t]+x}" ]]; then
    NEW_TABLES+=("$t")
  fi
done

if [[ ${#NEW_TABLES[@]} -eq 0 ]]; then
  echo "ERROR: no new business tables detected after importing ${SQL_ROOT}"
  exit 1
fi

printf 'new table -> %s\n' "${NEW_TABLES[@]}"

declare -a GENERATE_MODULE_NAMES=()
declare -a GENERATE_TABLE_PREFIXES=()
declare -A GENERATE_MODULE_SEEN=()

add_codegen_module() {
  local module_name="$1"
  local table_prefix="$2"
  local key="${module_name}:${table_prefix}"

  if [[ -z "${module_name}" ]]; then
    echo "ERROR: empty codegen module name"
    exit 1
  fi
  if [[ -z "${table_prefix}" ]]; then
    echo "ERROR: empty table prefix for module ${module_name}"
    exit 1
  fi
  if [[ -n "${GENERATE_MODULE_SEEN[$key]+x}" ]]; then
    return
  fi

  GENERATE_MODULE_SEEN["$key"]=1
  GENERATE_MODULE_NAMES+=("${module_name}")
  GENERATE_TABLE_PREFIXES+=("${table_prefix}")
}

if [[ -n "${CODEGEN_MODULES}" ]]; then
  MODULE_SPECS="${CODEGEN_MODULES//,/ }"
  MODULE_SPECS="${MODULE_SPECS//;/ }"

  for spec in ${MODULE_SPECS}; do
    if [[ "${spec}" == *":"* ]]; then
      module_name="${spec%%:*}"
      table_prefix="${spec#*:}"
    elif [[ "${spec}" == *"="* ]]; then
      module_name="${spec%%=*}"
      table_prefix="${spec#*=}"
    else
      module_name="${spec}"
      table_prefix="${spec}_"
    fi

    add_codegen_module "${module_name}" "${table_prefix}"
  done
elif [[ -n "${CODEGEN_MODULE_NAME}" || -n "${CODEGEN_TABLE_PREFIX}" ]]; then
  if [[ -z "${CODEGEN_TABLE_PREFIX}" ]]; then
    CODEGEN_TABLE_PREFIX="${CODEGEN_MODULE_NAME}_"
  fi
  if [[ -z "${CODEGEN_MODULE_NAME}" ]]; then
    CODEGEN_MODULE_NAME="${CODEGEN_TABLE_PREFIX%_}"
  fi

  add_codegen_module "${CODEGEN_MODULE_NAME}" "${CODEGEN_TABLE_PREFIX}"
else
  declare -A PREFIX_SEEN=()

  for t in "${NEW_TABLES[@]}"; do
    prefix="${t%%_*}"
    if [[ "${prefix}" == "${t}" ]]; then
      echo "ERROR: cannot infer table prefix from table without underscore: ${t}"
      exit 1
    fi
    prefix="${prefix}_"
    if [[ -z "${PREFIX_SEEN[$prefix]+x}" ]]; then
      PREFIX_SEEN["$prefix"]=1
      add_codegen_module "${prefix%_}" "${prefix}"
    fi
  done
fi

if [[ ${#GENERATE_MODULE_NAMES[@]} -eq 0 ]]; then
  echo "ERROR: no codegen modules resolved"
  exit 1
fi

export DB_URL DB_USER DB_PWD
export CODEGEN_DB_NAME CODEGEN_OUTPUT_DIR CODEGEN_BASE_PACKAGE

echo "== resolved codegen modules =="
for i in "${!GENERATE_MODULE_NAMES[@]}"; do
  echo "module=${GENERATE_MODULE_NAMES[$i]} prefix=${GENERATE_TABLE_PREFIXES[$i]}"
done

echo "== check all tables count =="
mysql_exec -e "
  SELECT COUNT(*) AS cnt
  FROM information_schema.tables
  WHERE table_schema = '${CODEGEN_DB_NAME}'
    AND table_type = 'BASE TABLE'
" || true

echo "== check module table counts =="
for i in "${!GENERATE_MODULE_NAMES[@]}"; do
  module_name="${GENERATE_MODULE_NAMES[$i]}"
  table_prefix="${GENERATE_TABLE_PREFIXES[$i]}"
  escaped_prefix="${table_prefix//_/\\\\_}"

  mapfile -t PREFIX_TABLES < <(
    mysql_exec -N -B -e "
      SELECT table_name
      FROM information_schema.tables
      WHERE table_schema = '${CODEGEN_DB_NAME}'
        AND table_type = 'BASE TABLE'
        AND table_name LIKE '${escaped_prefix}%'
      ORDER BY table_name
    "
  )

  if [[ ${#PREFIX_TABLES[@]} -eq 0 ]]; then
    echo "ERROR: no tables found for module=${module_name}, prefix=${table_prefix}"
    exit 1
  fi

  for table_name in "${PREFIX_TABLES[@]}"; do
    printf 'module=%s table -> %s\n' "${module_name}" "${table_name}"
  done
done

echo "== install integration test into ruoyi =="
mkdir -p "${RUOYI_DIR}/yudao-server/src/test/java/ci/codegen"
mkdir -p "${RUOYI_DIR}/yudao-server/src/test/resources"

cp "${ROOT_DIR}/tools/codegen/CiCodegenIT.java" \
   "${RUOYI_DIR}/yudao-server/src/test/java/ci/codegen/CiCodegenIT.java"

cp "${ROOT_DIR}/tools/codegen/application-ci-codegen.yaml" \
   "${RUOYI_DIR}/yudao-server/src/test/resources/application-ci-codegen.yaml"

echo "== override ruoyi codegen templates =="

LOCAL_TPL_DIR="${ROOT_DIR}/tools/codegen/templates"
RUOYI_TPL_DIR="${RUOYI_DIR}/yudao-module-infra/src/main/resources/codegen"

if [[ ! -d "${RUOYI_TPL_DIR}" ]]; then
  echo "ERROR: ruoyi codegen template dir not found: ${RUOYI_TPL_DIR}"
  exit 1
fi

if [[ -f "${LOCAL_TPL_DIR}/java/enums/errorcode.vm" ]]; then
  mkdir -p "${RUOYI_TPL_DIR}/java/enums"
  cp "${LOCAL_TPL_DIR}/java/enums/errorcode.vm" \
     "${RUOYI_TPL_DIR}/java/enums/errorcode.vm"
  echo "overridden: ${RUOYI_TPL_DIR}/java/enums/errorcode.vm"
else
  echo "WARN: local errorcode.vm not found at ${LOCAL_TPL_DIR}/java/enums/errorcode.vm"
fi

echo "== verify copied files =="
ls -l "${RUOYI_DIR}/yudao-server/src/test/java/ci/codegen/CiCodegenIT.java"
ls -l "${RUOYI_DIR}/yudao-server/src/test/resources/application-ci-codegen.yaml"

echo "== patch yudao-server pom with test deps =="
python3 - <<'PY'
from pathlib import Path

pom = Path("ruoyi/yudao-server/pom.xml")
text = pom.read_text(encoding="utf-8")

marker = "</dependencies>"
block = """
        <!-- added by future-codegen-bot for CI integration test -->
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-test</artifactId>
            <scope>test</scope>
        </dependency>
        <dependency>
            <groupId>org.junit.jupiter</groupId>
            <artifactId>junit-jupiter</artifactId>
            <scope>test</scope>
        </dependency>
"""

if "spring-boot-starter-test" not in text:
    if marker not in text:
        raise SystemExit("ERROR: </dependencies> not found in yudao-server/pom.xml")
    text = text.replace(marker, block + "\n    " + marker, 1)
    pom.write_text(text, encoding="utf-8")
PY

echo "== verify pom patch =="
grep -n "spring-boot-starter-test\|junit-jupiter" "${RUOYI_DIR}/yudao-server/pom.xml" || true

for i in "${!GENERATE_MODULE_NAMES[@]}"; do
  CODEGEN_MODULE_NAME="${GENERATE_MODULE_NAMES[$i]}"
  CODEGEN_TABLE_PREFIX="${GENERATE_TABLE_PREFIXES[$i]}"
  export CODEGEN_MODULE_NAME CODEGEN_TABLE_PREFIX

  echo "== run codegen integration test =="
  echo "CODEGEN_MODULE_NAME=${CODEGEN_MODULE_NAME}"
  echo "CODEGEN_TABLE_PREFIX=${CODEGEN_TABLE_PREFIX}"

  if ! (
    cd "${RUOYI_DIR}"
    mvn -pl yudao-server -am \
      -Dtest=ci.codegen.CiCodegenIT \
      -Dsurefire.failIfNoSpecifiedTests=false \
      test
  ); then
    echo "== surefire reports =="
    find "${RUOYI_DIR}/yudao-server/target/surefire-reports" -maxdepth 1 -type f | sort | while read -r f; do
      echo "--------------------------------------------------"
      echo "FILE: $f"
      echo "--------------------------------------------------"
      sed -n '1,240p' "$f" || true
    done
    exit 1
  fi
done

echo "== clone admin controllers to app controllers =="
APP_KEEP_HTTP_METHODS="${APP_KEEP_HTTP_METHODS:-GET}" \
APP_KEEP_METHOD_NAMES="${APP_KEEP_METHOD_NAMES:-}" \
python3 "${ROOT_DIR}/tools/codegen/clone_admin_controller_to_app.py" \
  --generated-dir "${CODEGEN_OUTPUT_DIR}"

echo "== generated files =="
find "${CODEGEN_OUTPUT_DIR}" -type f | sort || true

FIRST_GENERATED_FILE="$(find "${CODEGEN_OUTPUT_DIR}" -type f -print -quit)"
if [[ -z "${FIRST_GENERATED_FILE}" ]]; then
  echo "ERROR: no generated files under ${CODEGEN_OUTPUT_DIR}"
  exit 1
fi
