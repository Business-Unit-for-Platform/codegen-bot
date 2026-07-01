#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def copy_tree_contents(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def copy_selected_files(src_root: Path, dst_root: Path, rel_files: list[Path]) -> None:
    for rel in rel_files:
        src = src_root / rel
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def find_frontend_src_dirs(generated_dir: Path) -> list[Path]:
    return [p for p in generated_dir.rglob("src") if p.parent.name == "yudao-ui-admin-vue3"]


def find_backend_modules(generated_dir: Path) -> list[Path]:
    modules: list[Path] = []
    for p in generated_dir.rglob("yudao-module-*"):
        if p.is_dir() and p.name.startswith("yudao-module-"):
            modules.append(p)
    modules.sort()
    return modules


def module_key(module_name: str) -> str:
    name = module_name.removeprefix("yudao-module-")
    name = name.removesuffix("-api").removesuffix("-biz")
    return name


def module_suffix(module_name: str) -> str | None:
    if module_name.endswith("-api"):
        return "api"
    if module_name.endswith("-biz"):
        return "biz"
    return None


def target_module_artifact(source_module_name: str, split_kind: str | None, layout_mode: str) -> str:
    base = module_key(source_module_name)
    if layout_mode == "future-layout":
        if split_kind:
            return f"future-module-{base}-{split_kind}"
        return f"future-module-{base}"
    if split_kind:
        if source_module_name.endswith(f"-{split_kind}"):
            return source_module_name
        return f"{source_module_name}-{split_kind}"
    return source_module_name


def target_module_dir(backend_root: Path, source_module_name: str, split_kind: str | None, layout_mode: str) -> Path:
    artifact = target_module_artifact(source_module_name, split_kind, layout_mode)
    if layout_mode == "future-layout":
        return backend_root / "modules" / "custom" / module_key(source_module_name) / artifact
    return backend_root / artifact


def pom_module_path(backend_root: Path, module_dir: Path) -> str:
    return module_dir.relative_to(backend_root).as_posix()


def validate_layout_mode(layout_mode: str) -> None:
    if layout_mode not in {"yudao-upstream", "future-layout"}:
        raise ValueError(f"unsupported layout mode: {layout_mode}")


def ensure_module_pom(backend_root: Path, module_name: str) -> None:
    module_dir = backend_root / module_name
    target_pom = module_dir / "pom.xml"
    if target_pom.exists():
        return

    template_pom = backend_root / "yudao-module-member" / "pom.xml"
    if not template_pom.exists():
        raise FileNotFoundError(f"template pom not found: {template_pom}")

    content = template_pom.read_text(encoding="utf-8")
    content = content.replace("yudao-module-member", module_name)
    content = content.replace(
        "member 模块，我们放会员业务。",
        f"{module_name.removeprefix('yudao-module-')} 模块，自动生成。",
    )
    content = content.replace(
        "例如说：会员中心等等",
        f"例如说：{module_name.removeprefix('yudao-module-')} 业务。",
    )
    target_pom.write_text(content, encoding="utf-8")


def ensure_split_module_pom(module_dir: Path, artifact_id: str, split_kind: str, api_artifact_id: str | None = None) -> None:
    target_pom = module_dir / "pom.xml"
    if target_pom.exists():
        return

    description = f"{artifact_id} 模块，自动生成。"
    dependencies = ""
    if split_kind == "biz" and api_artifact_id:
        dependencies = f"""
    <dependencies>
        <dependency>
            <groupId>cn.iocoder.boot</groupId>
            <artifactId>{api_artifact_id}</artifactId>
            <version>${{revision}}</version>
        </dependency>
    </dependencies>
"""

    content = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<project xmlns=\"http://maven.apache.org/POM/4.0.0\"
         xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"
         xsi:schemaLocation=\"http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd\">
    <modelVersion>4.0.0</modelVersion>

    <parent>
        <groupId>cn.iocoder.boot</groupId>
        <artifactId>yudao</artifactId>
        <version>${{revision}}</version>
    </parent>

    <artifactId>{artifact_id}</artifactId>
    <packaging>jar</packaging>
    <name>${{project.artifactId}}</name>
    <description>{description}</description>
{dependencies}</project>
"""
    target_pom.write_text(content, encoding="utf-8")


def ensure_future_module_aggregate_pom(backend_root: Path, module: str, module_dirs: list[Path]) -> None:
    aggregate_dir = backend_root / "modules" / "custom" / module
    aggregate_dir.mkdir(parents=True, exist_ok=True)
    aggregate_pom = aggregate_dir / "pom.xml"
    modules_xml = "".join(f"        <module>{d.name}</module>\n" for d in module_dirs)
    content = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<project xmlns=\"http://maven.apache.org/POM/4.0.0\"
         xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"
         xsi:schemaLocation=\"http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd\">
    <modelVersion>4.0.0</modelVersion>

    <parent>
        <groupId>cn.iocoder.boot</groupId>
        <artifactId>yudao</artifactId>
        <version>${{revision}}</version>
    </parent>

    <artifactId>future-module-{module}</artifactId>
    <packaging>pom</packaging>
    <name>${{project.artifactId}}</name>
    <description>{module} 聚合模块，自动生成。</description>

    <modules>
{modules_xml}    </modules>
</project>
"""
    aggregate_pom.write_text(content, encoding="utf-8")

def insert_before_first(text: str, needle: str, block: str) -> str:
    if needle in text:
        return text.replace(needle, block + needle, 1)

    stripped_needle = needle.strip()
    match = re.search(rf"^[ \\t]*{re.escape(stripped_needle)}", text, flags=re.MULTILINE)
    if not match:
        return text
    return text[:match.start()] + block + text[match.start():]


def ensure_root_module_declared(backend_root: Path, module_path: str) -> None:
    root_pom = backend_root / "pom.xml"
    content = root_pom.read_text(encoding="utf-8")
    marker = f"<module>{module_path}</module>"
    if marker in content:
        return

    block = f"        <module>{module_path}</module>\n"
    updated = insert_before_first(content, "    </modules>", block)
    root_pom.write_text(updated, encoding="utf-8")


def server_pom_path(backend_root: Path, layout_mode: str) -> Path:
    candidates = []
    if layout_mode == "future-layout":
        candidates.append(backend_root / "apps" / "future-server" / "pom.xml")
    candidates.append(backend_root / "yudao-server" / "pom.xml")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def ensure_server_dependency(backend_root: Path, module_artifact_id: str, layout_mode: str = "yudao-upstream") -> None:
    server_pom = server_pom_path(backend_root, layout_mode)
    content = server_pom.read_text(encoding="utf-8")
    marker = f"<artifactId>{module_artifact_id}</artifactId>"
    if marker in content:
        return

    block = f"""
        <dependency>
            <groupId>cn.iocoder.boot</groupId>
            <artifactId>{module_artifact_id}</artifactId>
            <version>${{revision}}</version>
        </dependency>
"""
    updated = insert_before_first(content, "    </dependencies>", block)
    server_pom.write_text(updated, encoding="utf-8")


def collect_frontend_manifest_dirs(src_dir: Path) -> list[str]:
    result: set[str] = set()
    for top in sorted(p for p in src_dir.iterdir() if p.is_dir()):
        children = sorted(p for p in top.iterdir() if p.is_dir())
        if children:
            for child in children:
                result.add(str(Path("src") / top.name / child.name))
        else:
            result.add(str(Path("src") / top.name))
    return sorted(result)


def sync_frontend(generated_dir: Path, frontend_root: Path) -> list[str]:
    src_dirs = find_frontend_src_dirs(generated_dir)
    target_src = frontend_root / "src"
    target_src.mkdir(parents=True, exist_ok=True)

    frontend_dirs: set[str] = set()

    if not src_dirs:
        print("No generated frontend src found")
        return []

    for src_dir in src_dirs:
        copy_tree_contents(src_dir, target_src)
        for rel_dir in collect_frontend_manifest_dirs(src_dir):
            frontend_dirs.add(rel_dir)
        print(f"Synced frontend src from {src_dir}")

    return sorted(frontend_dirs)


def coerce_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", ""}:
        return None if value != "" else ""
    return value.strip('"').strip("'")


def parse_minimal_yaml(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by tools/codegen/split_api_biz.yml.

    PyYAML is used when available. The fallback supports nested mappings and
    scalar lists, which is enough for the checked-in split config.
    """
    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(text) or {}
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    rows: list[tuple[int, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        rows.append((len(line) - len(line.lstrip(" ")), line.strip()))

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(rows):
            return {}, index
        is_list = rows[index][1].startswith("- ")
        if is_list:
            result: list[Any] = []
            while index < len(rows):
                row_indent, stripped = rows[index]
                if row_indent < indent:
                    break
                if row_indent > indent:
                    raise ValueError(f"unexpected nested list indentation: {stripped}")
                if not stripped.startswith("- "):
                    break
                result.append(coerce_scalar(stripped[2:]))
                index += 1
            return result, index

        result: dict[str, Any] = {}
        while index < len(rows):
            row_indent, stripped = rows[index]
            if row_indent < indent:
                break
            if row_indent > indent:
                raise ValueError(f"unexpected mapping indentation: {stripped}")
            if stripped.startswith("- "):
                break
            if ":" not in stripped:
                raise ValueError(f"invalid yaml line: {stripped}")
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            index += 1
            if value:
                result[key] = coerce_scalar(value)
                continue
            if index < len(rows) and rows[index][0] > row_indent:
                child, index = parse_block(index, rows[index][0])
                result[key] = child
            else:
                result[key] = {}
        return result, index

    parsed, _ = parse_block(0, rows[0][0] if rows else 0)
    return parsed if isinstance(parsed, dict) else {}

def load_split_config(path: Path | None) -> dict[str, Any]:
    default_config: dict[str, Any] = {
        "split_api_biz": {
            "enabled": False,
            "default": False,
            "default_api_packages": ["api", "enums"],
            "default_biz_packages": ["controller", "service", "dal", "convert", "job", "listener"],
            "modules": {},
        }
    }
    if path is None or not path.exists():
        return default_config
    parsed = parse_minimal_yaml(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        return default_config
    cfg = default_config
    cfg.update(parsed)
    cfg.setdefault("split_api_biz", default_config["split_api_biz"])
    return cfg


def split_settings(config: dict[str, Any], module_name: str) -> dict[str, Any]:
    root = config.get("split_api_biz", {}) or {}
    module = module_key(module_name)
    module_cfg = ((root.get("modules") or {}).get(module) or {}) if isinstance(root.get("modules") or {}, dict) else {}
    enabled = bool(root.get("enabled", False)) and bool(module_cfg.get("enabled", root.get("default", False)))
    return {
        "enabled": enabled,
        "api_packages": list(module_cfg.get("api_packages") or root.get("default_api_packages") or ["api", "enums"]),
        "biz_packages": list(module_cfg.get("biz_packages") or root.get("default_biz_packages") or []),
        "module": module,
    }


def first_module_package_segment(rel: Path, module: str) -> str | None:
    parts = rel.parts
    for idx, part in enumerate(parts[:-1]):
        if part == module and idx > 0 and parts[idx - 1] == "module" and idx + 1 < len(parts):
            return parts[idx + 1]
    # Fallback for fixtures or non-standard package roots: src/main/java/<segment>/...
    if len(parts) > 4 and parts[:3] == ("src", "main", "java"):
        return parts[3]
    return None


def classify_module_files(module_dir: Path, settings: dict[str, Any]) -> tuple[list[Path], list[Path]]:
    module = settings["module"]
    api_packages = set(settings["api_packages"])
    all_files = [p.relative_to(module_dir) for p in module_dir.rglob("*") if p.is_file()]
    api_files: list[Path] = []
    biz_files: list[Path] = []
    for rel in all_files:
        if rel.name == "pom.xml":
            continue
        segment = first_module_package_segment(rel, module)
        if segment in api_packages:
            api_files.append(rel)
        else:
            biz_files.append(rel)
    return api_files, biz_files


def sync_backend(
    generated_dir: Path,
    backend_root: Path,
    split_config: dict[str, Any],
    layout_mode: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    validate_layout_mode(layout_mode)
    modules = find_backend_modules(generated_dir)
    backend_modules: list[str] = []
    split_results: list[dict[str, Any]] = []

    if not modules:
        print("No generated backend modules found")
        return backend_modules, split_results

    for module_dir in modules:
        module_name = module_dir.name
        source_split_kind = module_suffix(module_name)

        # If upstream already generated api/biz modules, preserve that split but map it to the requested layout.
        if source_split_kind:
            artifact = target_module_artifact(module_name, source_split_kind, layout_mode)
            target_dir = target_module_dir(backend_root, module_name, source_split_kind, layout_mode)
            target_dir.mkdir(parents=True, exist_ok=True)
            copy_tree_contents(module_dir, target_dir)
            ensure_split_module_pom(target_dir, artifact, source_split_kind)
            if layout_mode == "future-layout":
                ensure_future_module_aggregate_pom(backend_root, module_key(module_name), [target_dir])
                ensure_root_module_declared(backend_root, f"modules/custom/{module_key(module_name)}")
            else:
                ensure_root_module_declared(backend_root, pom_module_path(backend_root, target_dir))
            if source_split_kind == "biz":
                ensure_server_dependency(backend_root, artifact, layout_mode)
            backend_modules.append(pom_module_path(backend_root, target_dir) if layout_mode == "future-layout" else artifact)
            split_results.append(
                {
                    "module": module_key(module_name),
                    "source": module_name,
                    "mode": "already_split",
                    "layout_mode": layout_mode,
                    "artifact": artifact,
                    "target_path": pom_module_path(backend_root, target_dir),
                }
            )
            print(f"Synced already split backend module {module_name} -> {target_dir}")
            continue

        settings = split_settings(split_config, module_name)
        if not settings["enabled"]:
            artifact = target_module_artifact(module_name, None, layout_mode)
            target_dir = target_module_dir(backend_root, module_name, None, layout_mode)
            target_dir.mkdir(parents=True, exist_ok=True)
            copy_tree_contents(module_dir, target_dir)
            ensure_split_module_pom(target_dir, artifact, "biz")
            if layout_mode == "future-layout":
                ensure_future_module_aggregate_pom(backend_root, settings["module"], [target_dir])
                ensure_root_module_declared(backend_root, f"modules/custom/{settings['module']}")
            else:
                ensure_root_module_declared(backend_root, pom_module_path(backend_root, target_dir))
            ensure_server_dependency(backend_root, artifact, layout_mode)
            backend_modules.append(pom_module_path(backend_root, target_dir) if layout_mode == "future-layout" else artifact)
            split_results.append(
                {
                    "module": settings["module"],
                    "source": module_name,
                    "mode": "disabled",
                    "layout_mode": layout_mode,
                    "artifact": artifact,
                    "target_path": pom_module_path(backend_root, target_dir),
                }
            )
            print(f"Synced backend module {module_name} -> {target_dir}")
            continue

        api_artifact = target_module_artifact(module_name, "api", layout_mode)
        biz_artifact = target_module_artifact(module_name, "biz", layout_mode)
        api_dir = target_module_dir(backend_root, module_name, "api", layout_mode)
        biz_dir = target_module_dir(backend_root, module_name, "biz", layout_mode)
        api_dir.mkdir(parents=True, exist_ok=True)
        biz_dir.mkdir(parents=True, exist_ok=True)

        api_files, biz_files = classify_module_files(module_dir, settings)
        copy_selected_files(module_dir, api_dir, api_files)
        copy_selected_files(module_dir, biz_dir, biz_files)
        ensure_split_module_pom(api_dir, api_artifact, "api")
        ensure_split_module_pom(biz_dir, biz_artifact, "biz", api_artifact_id=api_artifact)
        if layout_mode == "future-layout":
            ensure_future_module_aggregate_pom(backend_root, settings["module"], [api_dir, biz_dir])
            ensure_root_module_declared(backend_root, f"modules/custom/{settings['module']}")
        else:
            ensure_root_module_declared(backend_root, pom_module_path(backend_root, api_dir))
            ensure_root_module_declared(backend_root, pom_module_path(backend_root, biz_dir))
        ensure_server_dependency(backend_root, biz_artifact, layout_mode)

        api_output = pom_module_path(backend_root, api_dir) if layout_mode == "future-layout" else api_artifact
        biz_output = pom_module_path(backend_root, biz_dir) if layout_mode == "future-layout" else biz_artifact
        backend_modules.extend([api_output, biz_output])
        split_results.append(
            {
                "module": settings["module"],
                "source": module_name,
                "mode": "split",
                "layout_mode": layout_mode,
                "api_module": api_artifact,
                "biz_module": biz_artifact,
                "api_target_path": pom_module_path(backend_root, api_dir),
                "biz_target_path": pom_module_path(backend_root, biz_dir),
                "api_packages": settings["api_packages"],
                "biz_packages": settings["biz_packages"],
                "api_file_count": len(api_files),
                "biz_file_count": len(biz_files),
            }
        )
        print(f"Split backend module {module_name} -> {api_dir}, {biz_dir}")

    return backend_modules, split_results

def copy_file(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(f"template file not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def sync_workflow_templates(project_root: Path, backend_root: Path, frontend_root: Path | None) -> None:
    backend_template = project_root / "templates" / "workflows" / "backend-maven.yml"
    frontend_template = project_root / "templates" / "workflows" / "frontend-build.yml"

    copy_file(backend_template, backend_root / ".github" / "workflows" / "backend-maven.yml")
    print(f"Synced backend workflow from {backend_template}")

    if frontend_root is not None:
        copy_file(frontend_template, frontend_root / ".github" / "workflows" / "frontend-build.yml")
        print(f"Synced frontend workflow from {frontend_template}")


def rel_files(root: Path, limit: int = 2000) -> list[str]:
    files: list[str] = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            files.append(str(p.relative_to(root)))
            if len(files) >= limit:
                files.append(f"... truncated after {limit} files")
                break
    return files


def build_manifest(
    *,
    args: argparse.Namespace,
    repo_kind: str,
    repo_root: Path,
    frontend_dirs: list[str],
    backend_modules: list[str],
    split_config_path: Path | None,
    split_results: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "1.2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.publish_mode,
        "publish_target": args.publish_target,
        "repo_kind": repo_kind,
        "layout_mode": args.layout_mode,
        "source": {
            "engine_repo": os.getenv("CODEGEN_ENGINE_REPO", "YunaiV/ruoyi-vue-pro"),
            "engine_ref": os.getenv("CODEGEN_ENGINE_REF", "master-jdk17"),
        },
        "generator": {
            "repo": os.getenv("GITHUB_REPOSITORY", "Business-Unit-for-Platform/codegen-bot"),
            "commit": os.getenv("GITHUB_SHA", ""),
            "workflow": os.getenv("GITHUB_WORKFLOW", ""),
            "run_id": os.getenv("GITHUB_RUN_ID", ""),
        },
        "target": {
            "owner": args.target_owner,
            "backend_repo": args.backend_repo,
            "frontend_repo": args.frontend_repo,
            "backend_branch": args.backend_branch,
            "frontend_branch": args.frontend_branch,
        },
        "codegen": {
            "modules_env": os.getenv("CODEGEN_MODULES", ""),
            "module_name_env": os.getenv("CODEGEN_MODULE_NAME", ""),
            "table_prefix_env": os.getenv("CODEGEN_TABLE_PREFIX", ""),
            "base_package": os.getenv("CODEGEN_BASE_PACKAGE", "cn.iocoder.yudao"),
        },
        "split_api_biz": {
            "config_path": str(split_config_path) if split_config_path else "",
            "results": split_results,
        },
        "outputs": {
            "frontend_dirs": frontend_dirs,
            "backend_modules": backend_modules,
            "files_sample": rel_files(repo_root),
        },
        "manual_review_checklist": [
            "Review generated app/user controllers for unsafe admin API exposure.",
            "Review tenant/user/data-permission boundaries.",
            "Confirm generated frontend routes, menus, and permissions.",
            "Confirm api/biz split files landed in the expected module.",
            "Confirm module POMs and aggregator POMs are correct.",
            "Confirm generated modules landed in the selected layout mode.",
            "Run target backend/frontend builds before merge or release.",
        ],
    }


def write_manifest(repo_root: Path, manifest: dict[str, Any]) -> None:
    generated_dir = repo_root / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = generated_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    legacy_path = repo_root / "generated-manifest.json"
    legacy_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote manifest to {manifest_path} and {legacy_path}")


def write_report(repo_root: Path, manifest: dict[str, Any]) -> None:
    generated_dir = repo_root / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    report_path = generated_dir / "codegen-report.md"
    outputs = manifest["outputs"]
    target = manifest["target"]
    split_results = manifest.get("split_api_biz", {}).get("results", [])
    lines = [
        "# Codegen Report",
        "",
        f"- Generated at: `{manifest['generated_at']}`",
        f"- Mode: `{manifest['mode']}`",
        f"- Generator: `{manifest['generator']['repo']}` @ `{manifest['generator']['commit']}`",
        f"- Engine: `{manifest['source']['engine_repo']}` @ `{manifest['source']['engine_ref']}`",
        f"- Target owner: `{target['owner']}`",
        f"- Backend repo: `{target['backend_repo']}` / branch `{target['backend_branch']}`",
        f"- Frontend repo: `{target['frontend_repo']}` / branch `{target['frontend_branch']}`",
        "",
        "## Backend modules",
        "",
        *(f"- `{m}`" for m in outputs["backend_modules"]),
        "",
        "## api/biz split",
        "",
        *(f"- `{r.get('source')}`: `{r.get('mode')}`" for r in split_results),
        "",
        "## Frontend directories",
        "",
        *(f"- `{d}`" for d in outputs["frontend_dirs"]),
        "",
        "## Manual review checklist",
        "",
        *(f"- [ ] {item}" for item in manifest["manual_review_checklist"]),
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote report to {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generated-dir", required=True)
    parser.add_argument("--backend-root", required=True)
    parser.add_argument("--frontend-root", required=True)
    parser.add_argument("--publish-mode", default="update_existing_repo_with_pr")
    parser.add_argument("--target-owner", default="Business-Unit-for-Platform")
    parser.add_argument("--backend-repo", default="future-vue-pro")
    parser.add_argument("--frontend-repo", default="future-ui-admin-vue3")
    parser.add_argument("--backend-branch", default="master-jdk17")
    parser.add_argument("--frontend-branch", default="master")
    parser.add_argument("--publish-target", default=os.getenv("PUBLISH_TARGET", "full-stack"), choices=["full-stack", "backend-only"])
    parser.add_argument("--split-config", default="")
    parser.add_argument("--layout-mode", default=os.getenv("LAYOUT_MODE", "yudao-upstream"), choices=["yudao-upstream", "future-layout"])
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    generated_dir = Path(args.generated_dir).resolve()
    backend_root = Path(args.backend_root).resolve()
    frontend_root = Path(args.frontend_root).resolve()
    split_config_path = Path(args.split_config).resolve() if args.split_config else project_root / "tools" / "codegen" / "split_api_biz.yml"
    split_config = load_split_config(split_config_path)

    if not generated_dir.exists():
        raise FileNotFoundError(f"generated dir not found: {generated_dir}")
    if not backend_root.exists():
        raise FileNotFoundError(f"backend root not found: {backend_root}")
    if args.publish_target == "full-stack" and not frontend_root.exists():
        raise FileNotFoundError(f"frontend root not found: {frontend_root}")

    frontend_dirs = sync_frontend(generated_dir, frontend_root) if args.publish_target == "full-stack" else []
    backend_modules, split_results = sync_backend(generated_dir, backend_root, split_config, args.layout_mode)
    sync_workflow_templates(project_root, backend_root, frontend_root if args.publish_target == "full-stack" else None)

    backend_manifest = build_manifest(
        args=args,
        repo_kind="backend",
        repo_root=backend_root,
        frontend_dirs=frontend_dirs,
        backend_modules=backend_modules,
        split_config_path=split_config_path,
        split_results=split_results,
    )
    frontend_manifest = None
    if args.publish_target == "full-stack":
        frontend_manifest = build_manifest(
            args=args,
            repo_kind="frontend",
            repo_root=frontend_root,
            frontend_dirs=frontend_dirs,
            backend_modules=backend_modules,
            split_config_path=split_config_path,
            split_results=split_results,
        )

    write_manifest(backend_root, backend_manifest)
    write_report(backend_root, backend_manifest)
    if frontend_manifest is not None:
        write_manifest(frontend_root, frontend_manifest)
        write_report(frontend_root, frontend_manifest)


if __name__ == "__main__":
    main()
