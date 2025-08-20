import os
import re
import shutil
from pathlib import Path

from invoke import Context


def bump_patch_version(version_file: str) -> str:
    """
    Increment the patch version in the given version.py file.
    Expects a line like: VERSION = "X.Y.Z"
    Returns the new version string.
    """
    path = Path(version_file)
    if not path.exists():
        raise FileNotFoundError(f"Version file not found: {version_file}")
    content = path.read_text()

    pattern = r'(VERSION\s*=\s*["\'])(\d+)\.(\d+)\.(\d+)(["\'])'
    match = re.search(pattern, content)
    if not match:
        raise ValueError(f"Version string not found in {version_file}")

    major = int(match.group(2))
    minor = int(match.group(3))
    patch = int(match.group(4))
    new_patch = patch + 1
    new_version = f"{major}.{minor}.{new_patch}"
    new_line = f"{match.group(1)}{new_version}{match.group(5)}"
    new_content = re.sub(pattern, new_line, content)
    path.write_text(new_content)
    return new_version


def clean_export(export_folder: str):
    """
    Remove existing distribution folder and its zip archive.
    """
    zip_file = f"{export_folder}.zip"
    print(f"Cleaning release folder: {export_folder} and {zip_file}...")
    if os.path.exists(export_folder):
        shutil.rmtree(export_folder)
    if os.path.exists(zip_file):
        os.remove(zip_file)


def prepare_shared_files(c: Context, project_root: str, export_folder: str):
    base = Path(export_folder) / "shared"
    c.run(f"rsync -av --exclude='__pycache__' {project_root} {base}/")
    (base / "__init__.py").touch()


def prepare_dsx_connect_files(c: Context, project_root: str, export_folder: str):
    base = Path(export_folder) / "dsx_connect"
    base.mkdir(parents=True, exist_ok=True)  # <-- build the parent tree
    for sub in ("models", "utils"):
        src = f"{project_root}/dsx_connect/{sub}/"
        dst = base / sub
        c.run(f"rsync -av --exclude='__pycache__' {src} {dst}/")
    (base / "__init__.py").touch()


def prepare_common_files(c: Context, project_slug: str, connector_name: str, version: str, project_root_dir: str,
                         export_folder: str):
    #c.run(f"mkdir -p {export_folder}/connectors/azure_blob_storage")
    c.run(
        f"rsync -av --exclude '__pycache__' {project_root_dir}/connectors/{project_slug}/ {export_folder}/connectors/{project_slug}/ --exclude 'deploy' --exclude 'dist' --exclude 'tasks.py'")
    c.run(
        f"rsync -av --exclude '__pycache__' {project_root_dir}/connectors/framework/ {export_folder}/connectors/framework/ --exclude 'tasks'")
    c.run(f"touch {export_folder}/connectors/__init__.py")
    # Copy start.py
    c.run(f"cp start.py {export_folder}")

    # move Dockerfile, docker-compose files and requirements.txt to topmost directory
    c.run(f"rsync -av {project_root_dir}/connectors/{project_slug}/deploy/ {export_folder}")

    # change the docker compose image: to reflect the new image tag
    file_path = Path(f"{export_folder}/docker-compose-{connector_name}.yaml")

    with file_path.open("r") as f:
        content = f.read()
        content = content.replace("__VERSION__", version)
    with file_path.open("w") as f:
        f.write(content)


def zip_export(c: Context, export_folder: str, build_dir: str):
    """
    Zip the contents of the export_folder into a .zip file alongside it.
    """
    zip_file = f"{export_folder}.zip"
    print(f"Zipping contents of {export_folder} into {zip_file}...")
    # Change directory to build_dir to ensure correct zip structure
    c.run(
        f"cd {build_dir} && zip -r {os.path.basename(zip_file)} {os.path.basename(export_folder)}"
    )


def build_image(c: Context, name: str, version: str, export_folder: str):
    image_tag = f"{name}:{version}"
    if c.run(f"docker images -q {image_tag}", hide=True).stdout.strip():
        print(f"Image {image_tag} already exists. Skipping build.")
    else:
        print(f"Building docker image {image_tag}…")
        c.run(f"docker build -t {image_tag} {export_folder}")


def push_image(c: Context, repo: str, name: str, version: str):
    versioned = f"{repo}/{name}:{version}"
    print(f"Pushing {versioned}…")
    c.run(f"docker tag {name}:{version} {versioned}")
    c.run(f"docker push {versioned}")
