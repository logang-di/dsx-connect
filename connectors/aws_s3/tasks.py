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
import sys

from invoke import task, run
from version import CONNECTOR_VERSION

project_slug = 'aws_s3'
name = 'aws-s3-connector'
repo_uname = "dsxconnect"

build_dir = "dist"
project_root_dir = str(pathlib.Path(__file__).resolve().parent.parent.parent)

# total hack, but necessary to import common task helpers
sys.path.insert(0, project_root_dir)
from connectors.framework.tasks.common import build_image, push_image, prepare_common_files, bump_patch_version, clean_export, zip_export, prepare_shared_files

version = bump_patch_version("version.py")
export_folder = os.path.join(build_dir, f"{name}-{version}")


@task
def clean(c):
    print(f"Clean {export_folder}...")
    clean_export(export_folder)


@task(pre=[clean])
def prepare(c):
    """Prepare distribution folder with necessary files."""
    print(f"Preparing release files for version {version}...")
    prepare_shared_files(c, project_root=project_root_dir, export_folder=export_folder)
    prepare_common_files(c, project_slug, name, version, project_root_dir, export_folder)


@task(pre=[prepare])
def zip(c):
    zip_export(c, export_folder, build_dir)


@task(pre=[zip])
def build(c):
    build_image(c=c, name=name, version=version, export_folder=export_folder)


@task(pre=[build])
def push(c):
    push_image(c, repo=repo_uname, name=name, version=version)


@task
def release(c):
    """Perform full release cycle."""
    clean(c)
    prepare(c)
    zip(c)
    build(c)
    push(c)
