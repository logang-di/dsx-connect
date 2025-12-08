"""
Use invoke to run this script.
pip install invoke
invoke <task>

ex: invoke release
"""
import os
import pathlib
import sys
from invoke import task

project_slug = "salesforce"
name = "salesforce-connector"
repo_uname = "dsxconnect"
DEFAULT_HELM_REPO = "oci://registry-1.docker.io/dsxconnect"
build_dir = "dist"
project_root_dir = str(pathlib.Path(__file__).resolve().parent.parent.parent)

# make common helpers importable when working on this connector standalone
sys.path.insert(0, project_root_dir)
from connectors.framework.tasks.common import (  # noqa: E402
    build_image,
    push_image,
    prepare_common_files,
    bump_patch_version,
    clean_export,
    zip_export,
    prepare_shared_files,
    read_connector_version,
    helm_package_connector,
    helm_push_oci_connector,
)


def _current_version() -> str:
    return read_connector_version("version.py")


def _export_folder(version: str) -> str:
    return os.path.join(build_dir, f"{name}-{version}")


@task
def clean(c):
    version = _current_version()
    export_folder = _export_folder(version)
    print(f"Clean {export_folder}...")
    clean_export(export_folder)


@task(pre=[clean])
def prepare(c):
    """Prepare distribution folder with necessary files."""
    version = _current_version()
    export_folder = _export_folder(version)
    if os.environ.get("GEN_DEV_CERTS", "").lower() in ("1", "true", "yes"):
        print("[prepare] GEN_DEV_CERTS enabled — ensure dev certs exist in connectors/framework/deploy/certs")
    prepare_shared_files(c, project_root=project_root_dir, export_folder=export_folder)
    prepare_common_files(c, project_slug, name, version, project_root_dir, export_folder)


@task(pre=[prepare])
def zip(c):
    version = _current_version()
    export_folder = _export_folder(version)
    zip_export(c, export_folder, build_dir)


@task(pre=[zip])
def build(c):
    version = _current_version()
    export_folder = _export_folder(version)
    build_image(c=c, name=name, version=version, export_folder=export_folder)


@task(pre=[build])
def push(c):
    version = _current_version()
    push_image(c, repo=repo_uname, name=name, version=version)


@task
def release(c):
    """Bump version and perform full release cycle (clean → prepare → zip → build → push + Helm)."""
    new_version = bump_patch_version("version.py")
    print(f"Bumped connector version to {new_version}")
    clean(c)
    prepare(c)
    zip(c)
    build(c)
    push(c)
    helm_release(c)
    try:
        c.run(f"git tag connector-{project_slug}-{new_version}", warn=True)
    except Exception:
        pass


@task
def helm_package(c, out_dir=None, version=None, app_version=None):
    if version is None:
        version = _current_version()
    if app_version is None:
        app_version = version
    if out_dir is None:
        out_dir = f"connectors/{project_slug}/{_export_folder(version)}"
    helm_package_connector(c, project_slug=project_slug, out_dir=out_dir, version=version, app_version=app_version)


@task
def helm_push_oci(c, repo=None, charts_dir=None, version=None):
    if version is None:
        version = _current_version()
    if repo is None:
        repo = os.environ.get("HELM_REPO", DEFAULT_HELM_REPO)
    if charts_dir is None:
        charts_dir = f"connectors/{project_slug}/{_export_folder(version)}"
    helm_push_oci_connector(c, project_slug=project_slug, repo=repo, charts_dir=charts_dir, version=version)


@task
def helm_release(c, repo=None, out_dir=None, charts_dir=None, version=None, app_version=None):
    """Lint, package, and push this connector's chart to the given OCI repo."""
    if version is None:
        version = _current_version()
    if app_version is None:
        app_version = version
    if repo is None:
        repo = os.environ.get("HELM_REPO", DEFAULT_HELM_REPO)
    if out_dir is None:
        out_dir = f"connectors/{project_slug}/{_export_folder(version)}"
    if charts_dir is None:
        charts_dir = out_dir
    helm_package(c, out_dir=out_dir, version=version, app_version=app_version)
    helm_push_oci(c, repo=repo, charts_dir=charts_dir, version=version)
