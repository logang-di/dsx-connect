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

name = 'azure-blob-storage-connector'
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


@task
def fix_requirements(c):
    """
    1) Rename azure_storage==… → azure-storage-blob>=12.0.0
    2) Remove duplicate pkg==x.y.z lines, keeping only the highest version.
    """
    import re
    from packaging.version import parse as parse_version
    import pathlib

    req_file = pathlib.Path(export_folder) / "requirements.txt"
    if not req_file.exists():
        print(f"{req_file} not found, skipping fix_requirements")
        return

    lines = req_file.read_text().splitlines()
    pin_re = re.compile(r'^([A-Za-z0-9_.-]+)==(.+)$')

    # 1) Scan for highest pinned version of each package
    highest = {}  # pkg_name.lower() → (version_str, original_line)
    for line in lines:
        m = pin_re.match(line)
        if m:
            pkg, ver = m.group(1), m.group(2)
            key = pkg.lower()
            if key not in highest or parse_version(ver) > parse_version(highest[key][0]):
                highest[key] = (ver, line)

    # 2) Rebuild file, emitting:
    #    - only the highest-pin line for each pkg
    #    - all non-`==` lines unmodified
    new_lines = []
    seen = set()
    for line in lines:
        # first handle azure_storage rename
        if line.startswith("azure_storage=="):
            new_lines.append("azure-storage-blob>=12.0.0")
            continue

        m = pin_re.match(line)
        if m:
            key = m.group(1).lower()
            # emit only if this exact line is the highest‐version pin and not yet seen
            if key in highest and highest[key][1] == line and key not in seen:
                new_lines.append(line)
                seen.add(key)
        else:
            new_lines.append(line)

        # 3) Ensure aiohttp is present
    if not any(l.split("==")[0].split(">=")[0].lower() == "aiohttp" for l in new_lines):
        # choose whatever minimum you like; 3.8.0+ is common
        new_lines.append("aiohttp>=3.8.0")


    # ensure trailing newline
    req_file.write_text("\n".join(new_lines) + "\n")
    print(f"Patched and deduped {req_file.name}")



@task(pre=[clean], post=[fix_requirements])
def prepare(c):
    """Prepare distribution folder with necessary files."""
    print(f"Preparing release files for version {version}...")
    c.run(f"mkdir -p {export_folder}/connectors/azure_blob_storage")
    c.run(f"mkdir -p {export_folder}/dsx_connect/models")
    c.run(f"mkdir -p {export_folder}/dsx_connect/utils")

    # Copy connectors
    c.run(
        f"rsync -av --exclude '__pycache__' {project_root_dir}/connectors/azure_blob_storage/ {export_folder}/connectors/azure_blob_storage/ --exclude 'deploy' --exclude 'dist' --exclude 'tasks.py'")
    c.run(
        f"rsync -av --exclude '__pycache__' {project_root_dir}/connectors/framework/ {export_folder}/connectors/framework/")

    # Copy dsx_connect/models
    c.run(
        f"rsync -av --exclude '__pycache__' {project_root_dir}/dsx_connect/models/ {export_folder}/dsx_connect/models/")

    # Copy dsx_connect/utils
    c.run(f"rsync -av --exclude '__pycache__' {project_root_dir}/dsx_connect/utils/ {export_folder}/dsx_connect/utils/")

    # Copy top-level __init__.py
    # c.run(f"touch {export_folder}/__init__.py")
    c.run(f"touch {export_folder}/connectors/__init__.py")
    c.run(f"touch {export_folder}/dsx_connect/__init__.py")

    # Copy start.py to to
    c.run(f"cp start.py {export_folder}")

    # Generate requirements.txt
    c.run(f"pipreqs {export_folder} --force --savepath {export_folder}/requirements.txt")

    # move Dockerfile and docker-compose to topmost directory
    c.run(f"rsync -av {project_root_dir}/connectors/azure_blob_storage/deploy/ {export_folder}")


@task(pre=[prepare])
def build(c):
    """Build Docker image from distribution."""
    image_tag = f"{name}:{version}"
    result = c.run(f"docker images -q {image_tag}", hide=True)
    if result.stdout.strip():
        print(f"Image {image_tag} already exists. Skipping build.")
    else:
        print(f"Building docker image {image_tag}...")
        c.run(f"docker build -t {image_tag} {export_folder}")


@task(pre=[build])
def push(c):
    """Push Docker image to Docker Hub."""
    print("Pushing image to Docker Hub...")
    c.run(f"docker tag {name}:{version} {repo_uname}/{name}:{version}")
    c.run(f"docker push {repo_uname}/{name}:{version}")


# @task
# def run(c):
#     """Run Docker image locally."""
#     print(f"Running image {name}:{version}")
#     c.run(f"docker run -p 0:0 {name}:{version}")

@task(pre=[prepare])
def generate_requirements(c):
    """Generate requirements.txt using pipreqs."""
    c.run(f"pipreqs {export_folder} --force --savepath {export_folder}/requirements.txt")


@task
def release(c):
    """Perform full release cycle."""
    bump(c)
    clean(c)
    prepare(c)
    fix_requirements(c)
    build(c)
    push(c)
