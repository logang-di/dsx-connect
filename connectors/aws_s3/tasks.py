"""
Use invoke to run this script.
pip install invoke
invoke <task>

ex: invoke release
"""
import pathlib
import re
import os
import shutil
from invoke import task, run
from version import CONNECTOR_VERSION

name = 'aws-s3-connector'
version = CONNECTOR_VERSION.strip()
build_dir = "dist"
export_folder = os.path.join(build_dir, f"{name}-{version}")
project_root_dir = str(pathlib.Path(__file__).resolve().parent.parent.parent)
repo_uname = "logangilbert"

@task
def bump(c):
    """Increment the build (patch) version in version.py."""
    filename = "version.py"
    with open(filename, "r") as f:
        content = f.read()

    pattern = r'(VERSION\s*=\s*["\'])(\d+)\.(\d+)\.(\d+)(["\'])'
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

    global version, export_folder
    version = new_version
    export_folder = os.path.join(build_dir, f"{name}-{version}")
    print(f"Bumped version to {new_version}")
    print(f"Export folder changed to {export_folder}")

@task
def clean(c):
    """Remove existing distribution folder and zip."""
    zip_file = f"{export_folder}.zip"
    print(f"Cleaning release folder: {export_folder} and {zip_file}...")
    if os.path.exists(export_folder):
        shutil.rmtree(export_folder)
    if os.path.exists(zip_file):
        os.remove(zip_file)

@task(pre=[clean])
def prepare(c):
    """Prepare distribution folder with necessary files."""
    print(f"Preparing release files for version {version}...")
    c.run(f"mkdir -p {export_folder}/connectors/aws_s3")
    c.run(f"mkdir -p {export_folder}/dsx_connect/models")
    c.run(f"mkdir -p {export_folder}/dsx_connect/utils")

    # Copy connectors
    c.run(f"rsync -av --exclude '__pycache__' {project_root_dir}/connectors/aws_s3/ {export_folder}/connectors/aws_s3/ --exclude 'deploy' --exclude 'dist' --exclude 'tasks.py'")
    c.run(f"rsync -av --exclude '__pycache__' {project_root_dir}/connectors/framework/ {export_folder}/connectors/framework/")

    # Copy dsx_connect/models
    c.run(f"rsync -av --exclude '__pycache__' {project_root_dir}/dsx_connect/models/ {export_folder}/dsx_connect/models/")


    # Copy dsx_connect/utils
    c.run(f"rsync -av --exclude '__pycache__' {project_root_dir}/dsx_connect/utils/ {export_folder}/dsx_connect/utils/")

    # Copy top-level __init__.py
    # c.run(f"touch {export_folder}/__init__.py")
    c.run(f"touch {export_folder}/connectors/__init__.py")
    c.run(f"touch {export_folder}/dsx_connect/__init__.py")

    # Copy start.py to to
    c.run(f"cp start.py {export_folder}")

    # move Dockerfile and docker-compose to topmost directory
    c.run(f"rsync -av {project_root_dir}/connectors/aws_s3/deploy/ {export_folder}")

    # change the docker compose image: to reflect the new image tag
    file_path = pathlib.Path(f"{export_folder}/docker-compose.yaml")

    with file_path.open("r") as f:
        content = f.read()
        content = content.replace("__VERSION__", version)
    with file_path.open("w") as f:
        f.write(content)


@task(pre=[prepare])
def build(c):
    """Build Docker image from distribution."""
    image_tag = f"{name}:{version}"
    latest_tag = f"{name}:latest"

    result = c.run(f"docker images -q {image_tag}", hide=True)
    if result.stdout.strip():
        print(f"Image {image_tag} already exists. Skipping build.")
    else:
        print(f"Building docker image {image_tag}...")
        c.run(f"docker build -t {image_tag} {export_folder}")
        # print(f"Tagging {image_tag} as {latest_tag}")
        # c.run(f"docker tag {image_tag} {latest_tag}")

@task(pre=[build])
def push(c):
    """Push Docker image to Docker Hub."""
    remote_version_tag = f"{repo_uname}/{name}:{version}"
    remote_latest_tag = f"{repo_uname}/{name}:latest"

    print(f"Pushing image {remote_version_tag} to Docker Hub...")
    c.run(f"docker tag {name}:{version} {remote_version_tag}")
    c.run(f"docker push {remote_version_tag}")

    # print(f"Tagging and pushing {name}:latest as {remote_latest_tag}...")
    # c.run(f"docker tag {name}:latest {remote_latest_tag}")
    # c.run(f"docker push {remote_latest_tag}")

# @task
# def run(c):
#     """Run Docker image locally."""
#     print(f"Running image {name}:{version}")
#     c.run(f"docker run -p 0:0 {name}:{version}")

@task
def release(c):
    """Perform full release cycle."""
    bump(c)
    clean(c)
    prepare(c)
    build(c)
    push(c)
