# AWS S3 Connector Implementation Guide

This project implements a AWS S3 Connector based on the DSX Connector framework.  This README is a guide for how to
implement, debug and create releases.

Documenation for deploying a release should be in file: deploy/README.md will


## Development and Debugging
Implement the following in `aws_s3_connector.py`:
- **Startup/Shutdown:** Initialize and clean up resources.

and the following API endpoints as applicable:
- **full_scan:** Request for full repository scan.
- **item_action:** Execute remediation actions on a file.
- **read_file:** Request to retrieve file contents.
- **repo_check:** Request that the connector checks its connectivity to its repository
- **webhook_event:** Process external webhook events.

### Changing Configuration (dev)

Leave `config.py` alone — it contains sane defaults. During development, override via:

- `.devenv` file next to `config.py` (not included in releases)
  - Example:
    - `DSXCONNECTOR_USE_TLS=false`
    - `DSXCONNECTOR_TLS_CERTFILE=../framework/deploy/certs/dev.localhost.crt`
    - `DSXCONNECTOR_TLS_KEYFILE=../framework/deploy/certs/dev.localhost.key`
    - `DSXCONNECTOR_CONNECTOR_URL=https://aws-s3-connector:8591`
    - `DSXCONNECTOR_DSX_CONNECT_URL=https://dsx-connect-api:8586`
    - `DSXCONNECTOR_VERIFY_TLS=false`
    - `DSXCONNECTOR_ASSET=lg-test-02`
    - `DSXCONNECTOR_FILTER=`
  - Or set `DSXCONNECTOR_ENV_FILE=/path/to/custom.env` to use a different file.

- Environment variables (shell/Compose/CI)
  - Any setting can be overridden as `DSXCONNECTOR_<SETTING_NAME>`.

## Build a Deployment Release

Connectors use Invoke to manage tasks for bundling up files, creating requirements (for pip) and
building a Docker image.  All the steps needed to prepare a new release for deployment.

### Prerequisites
Local running Docker instance: if building a Docker image (a "release")

### Using invoke
```python
pip install invoke
```
Navigate to the root directory (where the tasks.py file resides) and use invoke cli to run tasks
```python
invoke release
```
* Files will be bundled up in the dist folder.
* If docker is running locally, a docker image will be built.
* If access to a docker repository is given, the docker image will be pushed to that repository
* Docker images tagged with aws-s3-connector:<version> and aws-s3-connector:latest

Other invoke options:
* bump - increments the patch version in version.py (e.g., 1.0.0 to 1.0.1).
* clean - removes the distribution folder (dist/aws-s3-connector-<version>) and its associated zip file if they exist.
* prepare - prepares files for a versioned build.  Copies and moves file into dist/aws-s3-connector-<version>; generates requirements.txt.
* build - (runs bump, clean, prepare) and builds a Docker image tagged as aws-s3-connector:<version> from the prepared dist folder if it doesn’t already exist.
* push - (runs build) tags the Docker image with the repository username (logangilbert/<name>:<version>) and pushes it to Docker Hub.
* release - executes the full release cycle by running the following tasks in order: bump, clean, prepare, build, and push.

