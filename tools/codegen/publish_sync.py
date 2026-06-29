#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


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


def find_frontend_src_dirs(generated_dir: Path) -> list[Path]:
    return [p for p in generated_dir.rglob("src") if p.parent.name == "yudao-ui-admin-vue3"]


def find_backend_modules(generated_dir: Path) -> list[Path]:
    modules: list[Path] = []
    for p in generated_dir.rglob("yudao-module-*"):
        if p.is_dir() and p.name.startswith("yudao-module-"):
            modules.append(p)
    modules.sort()
    return modules


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


def insert_before_first(text: str, needle: str, block: str) -> str:
    if needle not in text:
        return text
    return text.replace(needle, block + needle, 1)


def ensure_root_module_declared(backend_root: Path, module_name: str) -> None:
    root_pom = backend_root / "pom.xml"
    content = root_pom.read_text(encoding="utf-8")
    marker = f"<module>{module_name}</module>"
    if marker in content:
        return

    block = f"        <module>{module_name}</module>\n"
    updated = insert_before_first(content, "    </modules>", block)
    root_pom.write_text(updated, encoding="utf-8")


def ensure_server_dependency(backend_root: Path, module_name: str) -> None:
    server_pom = backend_root / "yudao-server" / "pom.xml"
    content = server_pom.read_text(encoding="utf-8")
    marker = f"<artifactId>{module_name}</artifactId>"
    if marker in content:
        return

    block = f"""
        <dependency>
            <groupId>cn.iocoder.boot</groupId>
            <artifactId>{module_name}</artifactId>
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


def sync_backend(generated_dir: Path, backend_root: Path) -> list[str]:
    modules = find_backend_modules(generated_dir)
    backend_modules: list[str] = []

    if not modules:
        print("No generated backend modules found")
        return backend_modules

    for module_dir in modules:
        module_name = module_dir.name
        target_module_dir = backend_root / module_name
        target_module_dir.mkdir(parents=True, exist_ok=True)

        copy_tree_contents(module_dir, target_module_dir)
        ensure_module_pom(backend_root, module_name)
        ensure_root_module_declared(backend_root, module_name)
        ensure_server_dependency(backend_root, module_name)

        backend_modules.append(module_name)
        print(f"Synced backend module {module_name}")

    return backend_modules


def copy_file(src: Path, dst: Path) -> None:
    if not src.exists():
        raise FileNotFoundError(f"template file not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def sync_workflow_templates(project_root: Path, backend_root: Path, frontend_root: Path) -> None:
    backend_template = project_root / "templates" / "workflows" / "backend-maven.yml"
    frontend_template = project_root / "templates" / "workflows" / "frontend-build.yml"

    copy_file(backend_template, backend_root / ".github" / "workflows" / "backend-maven.yml")
    print(f"Synced backend workflow from {backend_template}")

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
) -> dict:
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.publish_mode,
        "repo_kind": repo_kind,
        "source": {
            "engine_repo": os.getenv("CODEGEN_ENGINE_REPO", "YunaiV/ruoyi-vue-pro"),
            "engine_ref": os.getenv("CODEGEN_ENGINE_REF", "master-jdk17"),
        },
        "generator": {
            "repo": os.getenv("GITHUB_REPOSITORY", "FutureTechQuant/codegen-bot"),
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
        "outputs": {
            "frontend_dirs": frontend_dirs,
            "backend_modules": backend_modules,
            "files_sample": rel_files(repo_root),
        },
        "manual_review_checklist": [
            "Review generated app/user controllers for unsafe admin API exposure.",
            "Review tenant/user/data-permission boundaries.",
            "Confirm generated frontend routes, menus, and permissions.",
            "Confirm module POMs and aggregator POMs are correct.",
            "Run target backend/frontend builds before merge or release.",
        ],
    }


def write_manifest(repo_root: Path, manifest: dict) -> None:
    generated_dir = repo_root / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = generated_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Backward-compatible location for existing consumers.
    legacy_path = repo_root / "generated-manifest.json"
    legacy_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote manifest to {manifest_path} and {legacy_path}")


def write_report(repo_root: Path, manifest: dict) -> None:
    generated_dir = repo_root / "generated"
    generated_dir.mkdir(parents=True, exist_ok=True)
    report_path = generated_dir / "codegen-report.md"
    outputs = manifest["outputs"]
    target = manifest["target"]
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
    parser.add_argument("--target-owner", default="FutureTechQuant")
    parser.add_argument("--backend-repo", default="ruoyi-vue-pro")
    parser.add_argument("--frontend-repo", default="yudao-ui-admin-vue3")
    parser.add_argument("--backend-branch", default="master-jdk17")
    parser.add_argument("--frontend-branch", default="master")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    generated_dir = Path(args.generated_dir).resolve()
    backend_root = Path(args.backend_root).resolve()
    frontend_root = Path(args.frontend_root).resolve()

    if not generated_dir.exists():
        raise FileNotFoundError(f"generated dir not found: {generated_dir}")
    if not backend_root.exists():
        raise FileNotFoundError(f"backend root not found: {backend_root}")
    if not frontend_root.exists():
        raise FileNotFoundError(f"frontend root not found: {frontend_root}")

    frontend_dirs = sync_frontend(generated_dir, frontend_root)
    backend_modules = sync_backend(generated_dir, backend_root)
    sync_workflow_templates(project_root, backend_root, frontend_root)

    backend_manifest = build_manifest(
        args=args,
        repo_kind="backend",
        repo_root=backend_root,
        frontend_dirs=frontend_dirs,
        backend_modules=backend_modules,
    )
    frontend_manifest = build_manifest(
        args=args,
        repo_kind="frontend",
        repo_root=frontend_root,
        frontend_dirs=frontend_dirs,
        backend_modules=backend_modules,
    )

    write_manifest(backend_root, backend_manifest)
    write_report(backend_root, backend_manifest)
    write_manifest(frontend_root, frontend_manifest)
    write_report(frontend_root, frontend_manifest)


if __name__ == "__main__":
    main()
