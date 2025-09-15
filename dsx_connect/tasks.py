"""
Use invoke to run this script.
pip install invoke
invoke <task>
"""
import os
import pathlib
import re
import shutil
import sys
from invoke import task

# Compute the project root directory
project_root = pathlib.Path(__file__).resolve().parent
# Insert the project root at the beginning of sys.path (standalone-friendly)
sys.path.insert(0, str(project_root))

name = "dsx-connect"
build_dir = "dist"
repo_uname = "dsxconnect"
DEFAULT_HELM_REPO = "oci://registry-1.docker.io/dsxconnect"


def _read_version() -> str:
    """Read DSX_CONNECT_VERSION from version.py without side effects."""
    content = (project_root / "version.py").read_text()
    m = re.search(r"DSX_CONNECT_VERSION\s*=\s*[\"'](\d+\.\d+\.\d+)[\"']", content)
    if not m:
        raise ValueError("DSX_CONNECT_VERSION not found in version.py")
    return m.group(1)


def _export_folder(version: str) -> str:
    return os.path.join(build_dir, f"{name}-{version}")

@task
def bump(c):
    """Increment the patch version in version.py."""
    filename = os.path.join(project_root, "version.py")
    with open(filename, "r") as f:
        content = f.read()

    pattern = r'(DSX_CONNECT_VERSION\s*=\s*["\'])(\d+)\.(\d+)\.(\d+)(["\'])'
    match = re.search(pattern, content)
    if not match:
        print("Version string not found in version.py")
        return

    major, minor, patch = int(match.group(2)), int(match.group(3)), int(match.group(4))
    new_patch = patch + 1
    new_version = f"{major}.{minor}.{new_patch}"
    new_line = f'{match.group(1)}{new_version}{match.group(5)}'
    new_content = re.sub(pattern, new_line, content)

    with open(filename, "w") as f:
        f.write(new_content)

    print(f"Bumped version to {new_version}")
    print(f"Export folder will be {os.path.join(build_dir, f'{name}-{new_version}')} on next run")

@task
def clean(c):
    """Remove build artifacts."""
    version = _read_version()
    export_folder = _export_folder(version)
    zip_file = f"{export_folder}.zip"
    print(f"Cleaning release folder: {export_folder} and {zip_file}...")
    if os.path.exists(export_folder):
        shutil.rmtree(export_folder)
    if os.path.exists(zip_file):
        os.remove(zip_file)

@task(pre=[clean])
def prepare(c):
    """Prepare release files."""
    version = _read_version()
    export_folder = _export_folder(version)
    print(f"Preparing release files for version {version}...")
    c.run(f"mkdir -p {export_folder}/dsx_connect")
    c.run(f"cp __init__.py {export_folder}/dsx_connect/")
    c.run(f"cp config.py {export_folder}/dsx_connect/")

    folders = [
        "app",
        "auth",
        "connectors",
        "database",
        "dsxa_client",
        "messaging",
        "models",
        "security",
        "superlog",
        "taskworkers"
    ]
    for folder in folders:
        c.run(f"rsync -av --exclude '__pycache__' {folder}/ {export_folder}/dsx_connect/{folder}/")

    c.run(f"rsync -av --exclude '__pycache__' ../shared/ {export_folder}/shared")

    # move docker files to topmost directory for building
    c.run(f"cp deploy/docker/Dockerfile {export_folder}/")
    c.run(f"cp deploy/docker/docker-compose-dsx-connect-all-services.yaml {export_folder}/")
    c.run(f"cp deploy/docker/docker-compose-dsxa.yaml {export_folder}/")
    c.run(f"cp deploy/docker/README.md {export_folder}/")
    # Include Helm chart assets similar to connectors: place under export/helm
    c.run(f"rsync -av deploy/helm/ {export_folder}/helm/ 2>/dev/null || true")

    # Also package the Helm chart into the export bundle (export/charts/*.tgz)
    try:
        # Write packaged chart under dist/dsx-connect-<ver>/charts
        helm_out_dir = f"{build_dir}/{name}-{version}/charts"
        helm_package(c, out_dir=helm_out_dir, version=version, app_version=version)
    except Exception as e:
        print(f"[helm] Skipped packaging chart during prepare: {e}")
    # Include dev TLS certs (optional; safe even if not used)
    # Prefer shared certs; then dsx_connect's docker certs
    c.run(f"mkdir -p {export_folder}/certs && rsync -av ../shared/deploy/certs/ {export_folder}/certs/ 2>/dev/null || true")
    c.run(f"mkdir -p {export_folder}/certs && rsync -av deploy/docker/certs/ {export_folder}/certs/ 2>/dev/null || true")

    # (Deprecated) No longer include stack helper scripts or Makefile in bundles

    # change the docker compose image: to reflect the new image tag
    file_path = pathlib.Path(f"{export_folder}/docker-compose-dsx-connect-all-services.yaml")

    with file_path.open("r") as f:
        content = f.read()
        content = content.replace("__VERSION__", version)
    with file_path.open("w") as f:
        f.write(content)

    # # Define original and new file paths for docker compose file
    # original_file = pathlib.Path(f"{export_folder}/docker-compose-dsx-connect-all-services-__VERSION__.yaml")
    # new_file = pathlib.Path(f"{export_folder}/docker-compose-dsx-connect-all-services-{version}.yaml")
    #
    # # Rename the file
    # original_file.rename(new_file)

    c.run(f"mkdir {export_folder}/data")

    c.run(f"cp version.py {export_folder}/dsx_connect")
    # Place start scripts inside the package so Dockerfile COPY dsx_connect/ ... brings them in
    c.run(f"cp dsx-connect-api-start.py {export_folder}/dsx_connect/")
    c.run(f"cp dsx-connect-workers-start.py {export_folder}/dsx_connect/")
    c.run(f"cp requirements.txt {export_folder}/")

@task(pre=[prepare])
def zip(c):
    """Zip the contents of the export folder."""
    version = _read_version()
    export_folder = _export_folder(version)
    zip_file = f"{export_folder}.zip"
    print(f"Zipping contents of {export_folder} into {zip_file}...")
    c.run(f"cd {build_dir} && zip -r {os.path.basename(zip_file)} {os.path.basename(export_folder)}")

@task(pre=[zip])
def build(c):
    """Build the Docker image."""
    version = _read_version()
    export_folder = _export_folder(version)
    image_tag = f"{name}:{version}"
    latest_tag = f"{name}:latest"
    result = c.run(f"docker images -q {image_tag}", hide=True)
    if result.stdout.strip():
        print(f"Image {image_tag} already exists. Skipping build.")
    else:
        print(f"Building docker image {image_tag}...")
        c.run(f"docker build -t {image_tag} {export_folder}")
        # c.run(f"docker tag {image_tag} {latest_tag}")

@task(pre=[build])
def push(c):
    """Push Docker image to Docker Hub."""
    version = _read_version()
    remote_version_tag = f"{repo_uname}/{name}:{version}"
    remote_latest_tag = f"{repo_uname}/{name}:latest"

    print(f"Pushing image {remote_version_tag} to Docker Hub...")
    c.run(f"docker tag {name}:{version} {remote_version_tag}")
    c.run(f"docker push {remote_version_tag}")

    #print(f"Pushing {name}:latest as {remote_latest_tag}...")
    #c.run(f"docker tag {name}:latest {remote_latest_tag}")
    #c.run(f"docker push {remote_latest_tag}")

@task
def run(c):
    """Run the Docker Compose setup."""
    print(f"Running {name}:{version}")
    c.run("docker-compose -f deploy/docker-compose-dsx-connect-all-services.yaml up -d")

@task
def lint(c):
    """Run linters on the codebase."""
    c.run("flake8 app taskworkers database celery_app utils dsxa_client config.py endpoint_names.py")
    c.run("pylint app taskworkers database celery_app utils dsxa_client config.py endpoint_names.py")

@task
def test(c):
    """Run tests."""
    c.run("pytest tests app/tests taskworkers/tests database/tests celery_app/tests utils/tests dsxa_client/tests")

@task(pre=[bump, clean, prepare, zip, build, push])
def release(c):
    """Perform a full release cycle."""
    version = _read_version()
    print(f"Release {name}:{version} completed.")
    # Also package and push Helm chart for core
    helm_release(c)
    # Create a git tag for traceability (no push here)
    try:
        c.run(f"git tag dsx-connect-{version}", warn=True)
    except Exception:
        pass


# -------------------- Helm tasks (package + push to OCI) --------------------
@task
def helm_package(c, out_dir="dist/charts", version=None, app_version=None):
    """Package the dsx-connect Helm chart under dsx_connect/deploy/helm.

    - version/app_version default to DSX_CONNECT_VERSION.
    - Writes .tgz to out_dir.
    """
    if version is None:
        version = _read_version()
    if app_version is None:
        app_version = version
    chart_dir = project_root / "deploy" / "helm"
    out_path = project_root.parent / out_dir
    out_path.mkdir(parents=True, exist_ok=True)
    # Build deps first to ensure Chart.lock is in sync, then lint, then package
    c.run(f"helm dependency build {chart_dir}")
    lint_res = c.run(f"helm lint {chart_dir}", warn=True, hide=True)
    if lint_res.exited != 0:
        out = (lint_res.stdout or "") + (lint_res.stderr or "")
        if "Chart.lock is out of sync with the dependencies file" in out:
            print("[helm] Dependencies out of sync. Run: helm dependency update dsx_connect/deploy/helm")
        # Re-emit lint output for visibility
        if lint_res.stdout:
            print(lint_res.stdout)
        if lint_res.stderr:
            print(lint_res.stderr)
        raise SystemExit(lint_res.exited)
    c.run(
        f"helm package {chart_dir} --version {version} --app-version {app_version} --destination {out_path}"
    )
    print(f"[helm] Packaged dsx-connect chart to {out_path}")


@task
def helm_push_oci(c, repo=None, charts_dir="dist/charts", version=None):
    """Push the packaged dsx-connect chart to an OCI registry."""
    if repo is None:
        import os as _os
        repo = _os.environ.get("HELM_REPO", DEFAULT_HELM_REPO)
    if version is None:
        version = _read_version()
    # Read chart name from Chart.yaml to construct the packaged filename
    chart_yaml = (project_root / "deploy" / "helm" / "Chart.yaml").read_text()
    m = re.search(r"^name:\s*([\w.-]+)", chart_yaml, flags=re.MULTILINE)
    if not m:
        raise ValueError("Chart.yaml must have a 'name:' field")
    chart_name = m.group(1)
    tgz = project_root.parent / charts_dir / f"{chart_name}-{version}.tgz"
    if not tgz.exists():
        raise FileNotFoundError(f"Packaged chart not found: {tgz}. Run helm_package first.")
    res = c.run(f"helm push {tgz} {repo}", warn=True, hide=True)
    if res.exited != 0:
        out = (res.stdout or "") + (res.stderr or "")
        print(out)
        if "insufficient_scope" in out or "authorization failed" in out or "push access denied" in out:
            print("[helm] Push denied. Ensure you are logged in and the repo exists.")
            print("[helm] Try: helm registry login registry-1.docker.io -u <user> -p <token>")
            print(f"[helm] Or set HELM_REPO to a repo you can push: HELM_REPO={repo}")
        raise SystemExit(res.exited)
    print(f"[helm] Pushed {tgz} to {repo}")


@task
def helm_release(c, repo=None, out_dir="dist/charts", charts_dir="dist/charts", version=None, app_version=None):
    """Lint, package, and push the dsx-connect chart to the given OCI repo."""
    if repo is None:
        import os as _os
        repo = _os.environ.get("HELM_REPO", DEFAULT_HELM_REPO)
    if version is None:
        version = _read_version()
    if app_version is None:
        app_version = version
    helm_package(c, out_dir=out_dir, version=version, app_version=app_version)
    helm_push_oci(c, repo=repo, charts_dir=charts_dir, version=version)
