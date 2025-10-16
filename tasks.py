import os
import re
import json
from pathlib import Path
from invoke import task, Exit
from concurrent.futures import ThreadPoolExecutor, as_completed
import re as _re

# ---------- Edit me ----------
# Explicit, human-edited list of connectors (folder names under ./connectors)
# Flip enabled=True/False or add/remove lines as you like.
CONNECTORS_CONFIG = [
    {"name": "aws_s3", "enabled": True},
    {"name": "azure_blob_storage", "enabled": True},
    {"name": "filesystem", "enabled": True},
    {"name": "google_cloud_storage", "enabled": True},
    {"name": "sharepoint", "enabled": True},
    #{"name": "icap_passthrough", "enabled": True},
]
# ---------- /Edit me ----------

# Regex to extract X.Y.Z from a VERSION = "X.Y.Z" line
# Match common version constants in version.py files
# e.g., VERSION = "1.2.3" or DSX_CONNECT_VERSION = "1.2.3" or CONNECTOR_VERSION = "1.2.3"
VERSION_PATTERN = re.compile(r"(?:VERSION|DSX_CONNECT_VERSION|CONNECTOR_VERSION)\s*=\s*[\"'](\d+\.\d+\.\d+)[\"']")

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


from connectors.framework.tasks.common import (
    release_connector_no_bump as _release_connector_no_bump_impl,
)

# Default OCI Helm repo base (Docker Hub requires namespace-only base; chart name becomes the repo)
DEFAULT_HELM_REPO = "oci://registry-1.docker.io/dsxconnect"


@task
def release_connector_nobump(c, name: str, repo_uname: str = "dsxconnect"):
    """Build+push a connector image without bumping version (CI-friendly)."""
    _release_connector_no_bump_impl(c, project_slug=name, repo_uname=repo_uname)


@task
def helm_release(
    c,
    repo: str = DEFAULT_HELM_REPO,
    only: str = "",
    skip: str = "",
    include_core: bool = True,
    parallel: bool = False,
    max_workers: int = 4,
    continue_on_error: bool = True,
    dry_run: bool = False,
):
    """
    Helm release for the project:
    - Runs dsx_connect helm-release (unless --include-core=false).
    - Runs each selected connector's helm-release.

    Examples:
      inv helm-release                      # core + all enabled connectors
      inv helm-release --only=azure_blob_storage,filesystem
      inv helm-release --skip=google_cloud_storage
      inv helm-release --repo=oci://registry-1.docker.io/dsxconnect
    """
    import os as _os
    if include_core:
        print("=== Helm release: core (dsx_connect) ===")
        repo = repo or _os.environ.get("HELM_REPO", DEFAULT_HELM_REPO)
        # Pushing charts to the 'dsxconnect' namespace is safe because chart names carry a '-chart' suffix.
        core_cmd = f"invoke helm-release --repo={repo}"
        code = _run(c, core_cmd, cwd=PROJECT_ROOT / "dsx_connect", dry_run=dry_run)
        if code != 0:
            raise Exit(code)

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
        print("[helm-release] No connectors selected.")
        return

    print("=== Helm release: connectors ===")
    # Build work list, skipping connectors without a Helm chart directory
    work: list[tuple[str, str]] = []
    for n in chosen:
        chart_dir = CONNECTORS_DIR / n / "deploy" / "helm"
        if not chart_dir.exists():
            print(f"[helm-release] Skipping {n}: no Helm chart dir at {chart_dir}")
            continue
        eff_repo = repo or _os.environ.get("HELM_REPO", DEFAULT_HELM_REPO)
        cmd = f"invoke helm-release --repo={eff_repo}"
        work.append((n, cmd))
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
                    print(f"[helm-release] FAILED: {n} (exit {code})")
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
        raise Exit(f"Some helm releases failed: {bad}", code=1)


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
    # After image releases, perform Helm releases for core + selected connectors
    helm_release(c, only=only, skip=skip, include_core=True, parallel=parallel, dry_run=dry_run)


@task(pre=[generate_manifest])
def bundle(c):
    """
    Bundle Docker Compose files for core and each connector into their respective dist directories.
    """
    def _emit_bundle_to(target_dir: Path):
        # Core compose
        core_version_local = read_version_file(CORE_VERSION_FILE)
        core_compose_src_l = PROJECT_ROOT / "dsx_connect" / DEPLOYMENT_DIR / f"dsx-connect-{core_version_local}" / "docker-compose-dsx-connect-all-services.yaml"
        dsxa_compose_src_l = PROJECT_ROOT / "dsx_connect" / DEPLOYMENT_DIR / f"dsx-connect-{core_version_local}" / "docker-compose-dsxa.yaml"
        readme_src_l = PROJECT_ROOT / "dsx_connect" / DEPLOYMENT_DIR / f"dsx-connect-{core_version_local}" / "README.md"
        target_dir.mkdir(parents=True, exist_ok=True)
        # Create a docker/ folder and copy compose + README there
        docker_dir = target_dir / "docker"
        docker_dir.mkdir(parents=True, exist_ok=True)
        c.run(f"cp -f {core_compose_src_l} {docker_dir}/docker-compose-dsx-connect-all-services.yaml")
        c.run(f"cp -f {dsxa_compose_src_l} {docker_dir}/docker-compose-dsxa.yaml")
        c.run(f"cp -f {readme_src_l} {docker_dir}/README.md")
        # rsyslog config is embedded in the rsyslog service startup (no external rsyslog.conf needed)
        # Append bundle quickstart to README
        _append_bundle_readme(target_dir / "README.md")
        # Copy core Helm chart (raw files) into the bundle
        core_helm_src_l = PROJECT_ROOT / "dsx_connect" / DEPLOYMENT_DIR / f"dsx-connect-{core_version_local}" / "helm"
        if core_helm_src_l.exists():
            c.run(f"mkdir -p {target_dir}/helm && rsync -av {core_helm_src_l}/ {target_dir}/helm/")
        # Copy helper scripts and Makefile from the core export if present
        core_scripts_src_l = PROJECT_ROOT / "dsx_connect" / DEPLOYMENT_DIR / f"dsx-connect-{core_version_local}" / "scripts"
        core_makefile_src_l = PROJECT_ROOT / "dsx_connect" / DEPLOYMENT_DIR / f"dsx-connect-{core_version_local}" / "Makefile"
        # if core_scripts_src_l.exists():
        #     c.run(f"mkdir -p {target_dir}/scripts && rsync -av {core_scripts_src_l}/ {target_dir}/scripts/")
        # if core_makefile_src_l.exists():
        #     c.run(f"cp -f {core_makefile_src_l} {target_dir}/Makefile")

        # Connector composes
        if CONNECTORS_DIR.exists():
            for connector_path in CONNECTORS_DIR.iterdir():
                name = connector_path.name
                connector_name = name.replace("_", "-") + "-connector"
                version_file = connector_path / "version.py"
                if not version_file.exists():
                    continue
                version = read_version_file(version_file)
                export_dir = connector_path / DEPLOYMENT_DIR / f"{connector_name}-{version}"
                compose_primary = export_dir / f"docker-compose-{connector_name}.yaml"
                # Prefer a Docker-specific README if present; else fall back to deploy README
                readme_src_conn = export_dir / "docker" / "README.md"
                if not readme_src_conn.exists():
                    readme_src_conn = export_dir / "README.md"
                if not export_dir.exists():
                    print(f"Warning: export dir not found for {name}: {export_dir}")
                    continue
                dest_dir = target_dir / f"{connector_name}-{version}"
                dest_dir.mkdir(parents=True, exist_ok=True)
                # Create docker/ folder for each connector bundle
                conn_docker_dir = dest_dir / "docker"
                conn_docker_dir.mkdir(parents=True, exist_ok=True)
                # Copy primary compose if present (into docker/ only)
                if compose_primary.exists():
                    c.run(f"cp -f {compose_primary} {conn_docker_dir}/{compose_primary.name}")
                else:
                    print(f"Warning: primary compose not found for {name}: {compose_primary}")
                # Copy any additional compose variants (e.g., NFS examples) to docker/
                for f in export_dir.glob("docker-compose-*.yaml"):
                    if f.name == compose_primary.name:
                        continue
                    c.run(f"cp -f {f} {conn_docker_dir}/{f.name}")
                # Copy connector README if present (to docker/ only)
                if readme_src_conn.exists():
                    c.run(f"cp -f {readme_src_conn} {conn_docker_dir}/README.md")
                # Copy connector Helm chart (raw files) into the bundle
                conn_helm_src = export_dir / "helm"
                if conn_helm_src.exists():
                    c.run(f"mkdir -p {dest_dir}/helm && rsync -av {conn_helm_src}/ {dest_dir}/helm/")

    # Emit to versioned bundle directory only (no 'latest' alias)
    core_version = read_version_file(CORE_VERSION_FILE)
    versioned_dir = PROJECT_ROOT / DEPLOYMENT_DIR / f"dsx-connect-{core_version}"
    _emit_bundle_to(versioned_dir)
    print(f"Copied bundle to {versioned_dir}")


def _text_replace(path: Path, subs: list[tuple[str, str]]):
    """Apply a list of (pattern, replacement) regex substitutions to a file in-place."""
    text = path.read_text()
    for pat, repl in subs:
        text = _re.sub(pat, repl, text, flags=_re.MULTILINE)
    path.write_text(text)


def _enable_core_tls(core_compose: Path):
    # Uncomment and set TLS env vars in the dsx_connect_api service
    subs = [
        (r"^\s+# DSXCONNECT_USE_TLS: .*$", "      DSXCONNECT_USE_TLS: \"true\""),
        (r"^\s+# DSXCONNECT_TLS_CERTFILE: .*$", "      DSXCONNECT_TLS_CERTFILE: \"/app/certs/dev.localhost.crt\""),
        (r"^\s+# DSXCONNECT_TLS_KEYFILE: .*$", "      DSXCONNECT_TLS_KEYFILE: \"/app/certs/dev.localhost.key\""),
    ]
    _text_replace(core_compose, subs)


def _enable_connector_tls(compose_path: Path):
    # Force connector + dsx-connect URLs to https and enable TLS envs
    subs = [
        # Allow optional quote before the scheme
        (r"(DSXCONNECTOR_CONNECTOR_URL:\s*[\"']?)http://", r"\\1https://"),
        (r"(DSXCONNECTOR_DSX_CONNECT_URL:\s*[\"']?)http://", r"\\1https://"),
        (r"^\s+# DSXCONNECTOR_USE_TLS: .*$", "      DSXCONNECTOR_USE_TLS: \"true\""),
        (r"^\s+# DSXCONNECTOR_TLS_CERTFILE: .*$", "      DSXCONNECTOR_TLS_CERTFILE: \"/app/certs/dev.localhost.crt\""),
        (r"^\s+# DSXCONNECTOR_TLS_KEYFILE: .*$", "      DSXCONNECTOR_TLS_KEYFILE: \"/app/certs/dev.localhost.key\""),
        (r"^\s+# DSXCONNECTOR_VERIFY_TLS: .*$", "      DSXCONNECTOR_VERIFY_TLS: \"false\""),
    ]
    _text_replace(compose_path, subs)


@task(pre=[generate_manifest])
def bundle_usetls(c):
    """
    Create the bundle like `bundle`, but enable TLS everywhere:
    - dsx-connect API: DSXCONNECT_USE_TLS=true and dev cert paths
    - connectors: DSXCONNECTOR_USE_TLS=true, URLs switched to https, VERIFY_TLS=false by default

    Note: health checks may still use http in compose; the API will present HTTPS on port 8586.
    """
    bundle(c)
    core_version = read_version_file(CORE_VERSION_FILE)
    # Apply TLS transforms to the versioned bundle only
    for bundle_dir in [PROJECT_ROOT / DEPLOYMENT_DIR / f"dsx-connect-{core_version}"]:
        # Prefer docker/ compose paths; also update root if present for back-compat
        for core_compose in [bundle_dir / "docker" / "docker-compose-dsx-connect-all-services.yaml",
                             bundle_dir / "docker-compose-dsx-connect-all-services.yaml"]:
            if core_compose.exists():
                _enable_core_tls(core_compose)
        # connectors under the bundle
        if bundle_dir.exists():
            for sub in bundle_dir.iterdir():
                if sub.is_dir():
                    # Update any connector compose files (search recursively to include docker/)
                    for f in sub.rglob("docker-compose-*-connector.yaml"):
                        _enable_connector_tls(f)

        # Ensure README has bundle quickstart
        _append_bundle_readme(bundle_dir / "README.md")


@task(pre=[generate_manifest], name="bundle-tls")
def bundle_tls(c):
    """Alias for bundle_usetls (enable TLS across API and connectors in the bundle)."""
    bundle_usetls(c)

def _append_bundle_readme(path: Path):
    """Append a minimal bundle quickstart section to the README if not present."""
    section_title = "\n\n## Bundle Quickstart\n"
    if path.exists():
        content = path.read_text()
    else:
        content = ""
    if "Bundle Quickstart" in content:
        return
    quickstart = f"""{section_title}
Run the following from this bundle directory.

- Up (dsx-connect):
  - `docker compose -p $(basename $(pwd)) -f docker/docker-compose-dsx-connect-all-services.yaml up -d`
- Up (with local DSXA too, if included):
  - `docker compose -p $(basename $(pwd)) -f docker/docker-compose-dsxa.yaml -f docker/docker-compose-dsx-connect-all-services.yaml up -d`
- Up (a connector):
  - `docker compose -p $(basename $(pwd)) -f <connector-dir>/docker/docker-compose-<connector>.yaml up -d`

Stop everything:
- `docker compose -p $(basename $(pwd)) down`
"""
    path.write_text(content + quickstart)
