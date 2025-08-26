import os
import re
import json
from pathlib import Path
from invoke import task, Exit
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------- Edit me ----------
# Explicit, human-edited list of connectors (folder names under ./connectors)
# Flip enabled=True/False or add/remove lines as you like.
CONNECTORS_CONFIG = [
    {"name": "aws_s3", "enabled": True},
    {"name": "azure_blob_storage", "enabled": True},
    {"name": "filesystem", "enabled": True},
    {"name": "google_cloud_storage", "enabled": True},
    # {"name": "sharepoint", "enabled": False},  # example future connector
]
# ---------- /Edit me ----------

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
    # Connectors (manifest still scans actual dirs so it's accurate even if disabled)
    if CONNECTORS_DIR.exists():
        for connector_path in CONNECTORS_DIR.iterdir():
            version_file = connector_path / "version.py"
            if version_file.exists():
                manifest[connector_path.name] = read_version_file(version_file)
    # Write manifest
    (PROJECT_ROOT / out).write_text(json.dumps(manifest, indent=2))
    print(f"Manifest written to {out}")


def _configured_names(include_disabled: bool = False) -> list[str]:
    names = []
    for cfg in CONNECTORS_CONFIG:
        if include_disabled or cfg.get("enabled", True):
            names.append(cfg["name"])
    return names


def _build_inv_cmd_for_module(modpath: str, extra: str = "") -> str:
    extra = extra.strip()
    return f"invoke -c {modpath} release{(' ' + extra) if extra else ''}"


def _build_core_cmd(extra: str = "") -> str:
    # Run the default 'tasks.py' inside dsx_connect by changing cwd
    extra = extra.strip()
    return f"invoke release{(' ' + extra) if extra else ''}"



def _connector_cmd(name: str, extra: str = "") -> str:
    # Connectors run "invoke release" from within their folder (they each define a 'release' task).
    extra = extra.strip()
    return f"invoke release{(' ' + extra) if extra else ''}"


def _run(c, cmd: str, *, cwd: Path | None = None, dry_run: bool = False, env: dict | None = None) -> int:
    print(f"[release] {cmd} (cwd={cwd or PROJECT_ROOT})")
    if dry_run:
        return 0
    run_env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT), **(env or {})}
    if cwd:
        with c.cd(str(cwd)):
            r = c.run(cmd, hide=False, warn=True, env=run_env)
    else:
        r = c.run(cmd, hide=False, warn=True, env=run_env)
    return r.exited


@task
def release_core(c, extra: str = "", dry_run: bool = False):
    """Run the core dsx_connect release task (passes through any 'extra' flags)."""
    cmd = _build_core_cmd(extra=extra)
    code = _run(
        c,
        cmd,
        cwd=PROJECT_ROOT / "dsx_connect",  # <<< key change
        dry_run=dry_run,
    )
    if code != 0:
        raise Exit(code)



@task
def release_connector(c, name: str, extra: str = "", dry_run: bool = False):
    """Run release for a single connector by name (e.g., inv release-connector --name=aws_s3)."""
    if name not in _configured_names(include_disabled=True):
        raise Exit(f"Connector '{name}' is not in CONNECTORS_CONFIG.", code=2)
    cmd = _connector_cmd(name, extra=extra)
    code = _run(c, cmd, cwd=CONNECTORS_DIR / name, dry_run=dry_run)
    if code != 0:
        raise Exit(code)


@task
def connectors_list(c, all: bool = False):
    """
    Print the configured connector list.
    Use --all to include disabled ones.
    """
    names = _configured_names(include_disabled=all)
    print("Configured connectors:")
    for cfg in CONNECTORS_CONFIG:
        if cfg["name"] in names:
            mark = "✅" if cfg.get("enabled", True) else "⛔"
            print(f"  {mark} {cfg['name']}")


@task
def release_connectors(
        c,
        only: str = "",            # CSV of connector names to run (overrides enabled list)
        skip: str = "",            # CSV of connector names to skip
        extra: str = "",           # extra args passed to each connector's 'release' (e.g., "--bump=patch --push")
        parallel: bool = False,
        max_workers: int = 4,
        continue_on_error: bool = True,
        dry_run: bool = False,
):
    """
    Release for many connectors based on the explicit CONNECTORS_CONFIG list.
    - By default runs all connectors with enabled=True.
    - Use --only to run a subset:   inv release-connectors --only=aws_s3,filesystem
    - Use --skip to exclude some:   inv release-connectors --skip=google_cloud_storage
    """
    chosen = _configured_names(include_disabled=False)

    if only:
        wanted = {n.strip() for n in only.split(",") if n.strip()}
        unknown = wanted - set(_configured_names(include_disabled=True))
        if unknown:
            raise Exit(f"Unknown connector(s) in --only: {', '.join(sorted(unknown))}", code=2)
        chosen = [n for n in chosen if n in wanted]

    if skip:
        banned = {n.strip() for n in skip.split(",") if n.strip()}
        unknown = banned - set(_configured_names(include_disabled=True))
        if unknown:
            raise Exit(f"Unknown connector(s) in --skip: {', '.join(sorted(unknown))}", code=2)
        chosen = [n for n in chosen if n not in banned]

    if not chosen:
        print("[release] No connectors selected.")
        return

    work = [(n, _connector_cmd(n, extra=extra)) for n in chosen]
    errors: list[tuple[str, int]] = []

    def _do(n: str, cmd: str) -> tuple[str, int]:
        code = _run(c, cmd, cwd=CONNECTORS_DIR / n, dry_run=dry_run)
        return (n, code)

    if parallel:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_do, n, cmd): n for n, cmd in work}
            for fut in as_completed(futures):
                n, code = fut.result()
                if code != 0:
                    print(f"[release] FAILED: {n} (exit {code})")
                    errors.append((n, code))
                    if not continue_on_error:
                        raise Exit(code)
    else:
        for n, cmd in work:
            _, code = _do(n, cmd)
            if code != 0:
                errors.append((n, code))
                if not continue_on_error:
                    raise Exit(code)

    if errors:
        bad = ", ".join([f"{n}:{code}" for n, code in errors])
        raise Exit(f"Some releases failed: {bad}", code=1)


@task(pre=[generate_manifest])
def release_all(
        c,
        extra_core: str = "",
        extra_connectors: str = "",
        only: str = "",
        skip: str = "",
        parallel: bool = False,
        dry_run: bool = False,
):
    """
    Release core + selected connectors. Uses generate_manifest first.
    You can restrict connectors with --only/--skip (same semantics as release-connectors).
    """
    print("=== Releasing core (dsx_connect) ===")
    release_core(c, extra=extra_core, dry_run=dry_run)
    print("=== Releasing connectors ===")
    release_connectors(
        c,
        only=only,
        skip=skip,
        extra=extra_connectors,
        parallel=parallel,
        dry_run=dry_run,
    )


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
