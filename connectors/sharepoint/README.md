# Sharepoint Connector Implementation Guide

This project implements a DSX Connector based on the DSX Connector framework.  This README is a guide for how to
implement, debug and create releases.

Documenation for deploying a release should be in file: deploy/README.md will

## Development
Implement the following in `sharepoint_connector.py`:
- **Startup/Shutdown:** Initialize and clean up resources.

and the following API endpoints as applicable:
- **full_scan:** Request for full repository scan.
- **item_action:** Execute remediation actions on a file.
- **read_file:** Request to retrieve file contents.
- **repo_check:** Request that the connector checks its connectivity to its repository
- **webhook_event:** Process external webhook events.

## Running/Testing in an IDE/Debugger
All connectors can be run from the command-line or via an IDE/Debugger.  In this directory, there is both a start.py
file and `sharepoint_connector.py` script either of which can be use to start the connector.

When running this way, the config.py file is read to configure the app, and any one of settings can be
overridden with environment settings.

You should see output similar to this:
```shell
2025-05-21 13:36:15,001 INFO     logging.py          : Log level set to DEBUG
INFO:     Started server process [81998]
INFO:     Waiting for application startup.
2025-05-21 13:36:15,723 INFO     dsx_connector.py    : Connection to dsx-connect at http://0.0.0.0:8586 success.
2025-05-21 13:36:15,723 INFO     sharepoint_connector.py: Starting up connector sharepoint
2025-05-21 13:36:15,723 INFO     sharepoint_connector.py: sharepoint-connector version: 0.1.0.
...
2025-05-21 13:36:15,733 INFO     dsx_connector.py    : Connection to dsx-connect at http://0.0.0.0:8586 success.
INFO:     Application startup complete.
...
```

### Changing Configuration
The easiest method is to edit config.py directly, however, just keep in mind that config.py file defines all of
the default config values. When you release a connector, you will likely want your defaults to make sense
(i.e. most of the time, the default value suffices), as opposed to whatever settings you tested the connector with.
The best way to test connectors with various configuration settings, without permanently changing the defaults, is to
override settings with environment settings.

For example, in the config file:

    name: str = 'sharepoint-connector'
    connector_url: HttpUrl = Field(default="http://0.0.0.0:8599",
                                   description="Base URL (http(s)://ip.add.ddr.ess|URL:port) of this connector entry point")
    item_action: ItemActionEnum = ItemActionEnum.NOTHING
    dsx_connect_url: HttpUrl = Field(default="http://0.0.0.0:8586",
                                     description="Complete URL (http(s)://ip.add.ddr.ess|URL:port) of the dsxa entry point")
    test_mode: bool = True

    ### Connector specific configuration

    class Config:
        env_prefix = "DSXCONNECTOR_"
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "forbid"
if you wanted to change the dsx_connect_url, you can do so adding a setting on the command to run:

DSXCONNECTOR_DSX_CONNECT_URL=https://sdfsdfgasdg.aws.com:8586 python ```start.py```
or by adding this setting in a .env file. The format for overriding a setting is: DSXCONNECTOR_<capitalized config variable>



## Build a Deployment Release

Connectors use Invoke to manage tasks for bundling up files, creating requirements (for pip) and
building a Docker image.  All the steps needed to prepare a new release for deployment.

### Configuration

- Required env vars (can be set via `.env` or compose):
  - `DSXCONNECTOR_SP_TENANT_ID`: Azure AD Tenant ID
  - `DSXCONNECTOR_SP_CLIENT_ID`: App (client) ID
  - `DSXCONNECTOR_SP_CLIENT_SECRET`: App client secret
  - `DSXCONNECTOR_SP_HOSTNAME`: e.g., `contoso.sharepoint.com`
  - `DSXCONNECTOR_SP_SITE_PATH`: e.g., `MySite`
  - Optional: `DSXCONNECTOR_SP_DRIVE_NAME`
  - TLS: `DSXCONNECTOR_SP_VERIFY_TLS=true|false`, `DSXCONNECTOR_SP_CA_BUNDLE=/path/to/ca.pem`

`connectors/sharepoint/.env.example` contains a ready-to-copy template.

### Handlers

This connector implements handlers via `DSXConnector`:

- `full_scan`: Enumerates files in the configured SharePoint drive (recursive when `recursive=True`), enqueuing scan requests (location=item-id).
- `read_file`: Streams file content via Microsoft Graph `/drives/{drive}/items/{id}/content`.
- `item_action`: Supports `DELETE` (removes item by id). Other actions return NOT_IMPLEMENTED.
- `repo_check`: Validates connectivity by resolving site/drive and listing root.

### Compose

The compose file under `deploy/` includes the SP_* envs and TLS toggles. Provide values via a `.env` file or your environment.
Local running Docker instance: if building a Docker image (a "release")

Ideally, also a remote docker hub instance that you can push the image to.  This can be configured in the tasks.py file by setting the following:
repo_uname = "<your repo>"

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
* Docker images tagged with sharepoint-connector:<version> and sharepoint-connector:latest

Other invoke options:
* bump - increments the patch version in version.py (e.g., 1.0.0 to 1.0.1).
* clean - removes the distribution folder (dist/sharepoint-connector-<version>) and its associated zip file if they exist.
* prepare - prepares files for a versioned build.  Copies and moves file into dist/sharepoint-connector-<version>; generates requirements.txt.
* build - (runs bump, clean, prepare) and builds a Docker image tagged as sharepoint-connector:<version> from the prepared dist folder if it doesn’t already exist.
* push - (runs build) tags the Docker image with the repository username (dsxconnect/<name>:<version>) and pushes it to Docker Hub.
* release - executes the full release cycle by running the following tasks in order: bump, clean, prepare, build, and push.

