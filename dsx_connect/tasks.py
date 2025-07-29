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

# Compute the project root directory (two directories up from this tasks file)
project_root = pathlib.Path(__file__).resolve().parent
print(f"Project Root: {project_root}")
# Insert the project root at the beginning of sys.path
sys.path.insert(0, str(project_root))

from version import DSX_CONNECT_VERSION

name = "dsx-connect"
version = DSX_CONNECT_VERSION.strip()
build_dir = "dist"
export_folder = os.path.join(build_dir, f"{name}-{version}")
repo_uname = "logangilbert"

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

    global version, export_folder
    version = new_version
    export_folder = os.path.join(build_dir, f"{name}-{version}")
    print(f"Bumped version to {new_version}")
    print(f"Export folder changed to {export_folder}")

@task
def clean(c):
    """Remove build artifacts."""
    zip_file = f"{export_folder}.zip"
    print(f"Cleaning release folder: {export_folder} and {zip_file}...")
    if os.path.exists(export_folder):
        shutil.rmtree(export_folder)
    if os.path.exists(zip_file):
        os.remove(zip_file)

@task(pre=[clean])
def prepare(c):
    """Prepare release files."""
    print(f"Preparing release files for version {version}...")
    c.run(f"mkdir -p {export_folder}/dsx_connect")
    c.run(f"cp __init__.py {export_folder}/dsx_connect/")
    c.run(f"cp config.py {export_folder}/dsx_connect/")
    c.run(f"rsync -av --exclude '__pycache__' app/ {export_folder}/dsx_connect/app/")
    c.run(f"rsync -av --exclude '__pycache__' connector_utils/ {export_folder}/dsx_connect/connector_utils/")
    c.run(f"rsync -av --exclude '__pycache__' taskworkers/ {export_folder}/dsx_connect/taskworkers/")
    c.run(f"rsync -av --exclude '__pycache__' diagrams/ {export_folder}/dsx_connect/diagrams/")
    c.run(f"rsync -av --exclude '__pycache__' database/ {export_folder}/dsx_connect/database/")
    c.run(f"rsync -av --exclude '__pycache__' models/ {export_folder}/dsx_connect/models/")
    c.run(f"rsync -av --exclude '__pycache__' taskqueue/ {export_folder}/dsx_connect/taskqueue/")
    c.run(f"rsync -av --exclude '__pycache__' utils/ {export_folder}/dsx_connect/utils/")
    c.run(f"rsync -av --exclude '__pycache__' dsxa_client/ {export_folder}/dsx_connect/dsxa_client/")
    c.run(f"rsync -av --exclude '__pycache__' config.py {export_folder}/dsx_connect/")

    # move docker files to topmost directory for building
    c.run(f"cp deploy/docker/Dockerfile {export_folder}/")
    c.run(f"cp deploy/docker/docker-compose-dsx-connect-all-services.yaml {export_folder}/")
    c.run(f"cp deploy/docker/docker-compose-dsxa.yaml {export_folder}/")

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
    c.run(f"cp dsx-connect-api-start.py {export_folder}/")
    c.run(f"cp dsx-connect-workers-start.py {export_folder}/")
    c.run(f"cp requirements.txt {export_folder}/")
    c.run(f"cp README.md {export_folder}/")

@task(pre=[prepare])
def zip(c):
    """Zip the contents of the export folder."""
    zip_file = f"{export_folder}.zip"
    print(f"Zipping contents of {export_folder} into {zip_file}...")
    c.run(f"cd {build_dir} && zip -r {os.path.basename(zip_file)} {os.path.basename(export_folder)}")

@task(pre=[zip])
def build(c):
    """Build the Docker image."""
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
    c.run("flake8 app taskworkers database taskqueue utils dsxa_client config.py constants.py")
    c.run("pylint app taskworkers database taskqueue utils dsxa_client config.py constants.py")

@task
def test(c):
    """Run tests."""
    c.run("pytest tests app/tests taskworkers/tests database/tests taskqueue/tests utils/tests dsxa_client/tests")

@task(pre=[bump, clean, prepare, zip, build, push])
def release(c):
    """Perform a full release cycle."""
    print(f"Release {name}:{version} completed.")