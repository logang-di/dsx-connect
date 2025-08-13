import os
import re
import json
from pathlib import Path
from invoke import task

# Regex to extract X.Y.Z from a VERSION = "X.Y.Z" line
VERSION_PATTERN = re.compile(r"VERSION\s*=\s*[\"'](\d+\.\d+\.\d+)[\"']")

# Base directories
PROJECT_ROOT = Path(__file__).parent.resolve()
CORE_VERSION_FILE = PROJECT_ROOT / "dsx_connect" / "version.py"
CONNECTORS_DIR = PROJECT_ROOT / "connectors"
DEPLOYMENT_DIR = "dist"

def read_version_file(path: Path) -> str:
    """Read and return the version string from a version.py file."""
    content = path.read_text()
    match = VERSION_PATTERN.search(content)
    if not match:
        raise ValueError(f"No VERSION found in {path}")
    return match.group(1)


@task
def generate_manifest(c, out: str = "versions.json"):
    """
    Scan the core and connector version.py files, write a JSON manifest of their versions.
    """
    manifest = {}
    # Core
    manifest["dsx_connect"] = read_version_file(CORE_VERSION_FILE)
    # Connectors
    if CONNECTORS_DIR.exists():
        for connector_path in CONNECTORS_DIR.iterdir():
            version_file = connector_path / "version.py"
            if version_file.exists():
                manifest[connector_path.name] = read_version_file(version_file)
    # Write manifest
    (PROJECT_ROOT / out).write_text(json.dumps(manifest, indent=2))
    print(f"Manifest written to {out}")


@task(pre=[generate_manifest])
def bundle(c):
    """
    Bundle Docker Compose files for core and each connector into their respective dist directories.
    """
    # Core compose
    core_version = read_version_file(CORE_VERSION_FILE)
    core_compose_src = PROJECT_ROOT / "dsx_connect" / DEPLOYMENT_DIR / f"dsx-connect-{core_version}" / "docker-compose-dsx-connect-all-services.yaml"
    dsxa_compose_src = PROJECT_ROOT / "dsx_connect" / DEPLOYMENT_DIR / f"dsx-connect-{core_version}" / "docker-compose-dsxa.yaml"
    readme_src = PROJECT_ROOT / "dsx_connect" / DEPLOYMENT_DIR / f"dsx-connect-{core_version}" / "README.md"
    core_dist_dir = PROJECT_ROOT / DEPLOYMENT_DIR / f"dsx-connect-{core_version}"
    core_dist_dir.mkdir(parents=True, exist_ok=True)
    c.run(f"cp {core_compose_src} {core_dist_dir}/docker-compose-dsx-connect-all-services.yaml")
    c.run(f"cp {dsxa_compose_src} {core_dist_dir}/docker-compose-dsxa.yaml")
    c.run(f"cp {readme_src} {core_dist_dir}/README.md")
    print(f"Copied core compose to {core_dist_dir}")

    # Connector composes
    if CONNECTORS_DIR.exists():
        for connector_path in CONNECTORS_DIR.iterdir():
            name = connector_path.name
            connector_name = name.replace("_", "-") + "-connector"
            version_file = connector_path / "version.py"
            if not version_file.exists():
                continue
            version = read_version_file(version_file)
            compose_src = connector_path / DEPLOYMENT_DIR / f"{connector_name}-{version}" / f"docker-compose-{connector_name}.yaml"
            readme_src = connector_path / DEPLOYMENT_DIR / f"{connector_name}-{version}" / f"README.md"
            if not compose_src.exists():
                print(f"Warning: compose file not found for {name}: {compose_src}")
                continue
            dest_dir = core_dist_dir / f"{connector_name}-{version}"
            dest_dir.mkdir(parents=True, exist_ok=True)
            c.run(f"cp {compose_src} {dest_dir}/{compose_src.name}")
            c.run(f"cp {readme_src} {dest_dir}/README.md")
            print(f"Copied {name} compose to {dest_dir}")
