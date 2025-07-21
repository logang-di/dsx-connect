# Azure Blob Storage Connector Implementation Guide

This project implements a DSX Connector based on the DSX Connector framework.  This README is a guide for how to
implement, debug and create releases.

Documenation for deploying a release should be in file: deploy/README.md will


## Development
Implement the following in `azure_blob_storage_connector.py`:
- **Startup/Shutdown:** Initialize and clean up resources.

and the following API endpoints as applicable:
- **full_scan:** Request for full repository scan.
- **item_action:** Execute remediation actions on a file.
- **read_file:** Request to retrieve file contents.
- **repo_check:** Request that the connector checks its connectivity to its repository
- **webhook_event:** Process external webhook events.

### Running/Testing in an IDE/Debugger
All connectors can be run from the command-line or via an IDE/Debugger.  In this directory, there is both a start.py
file and `azure_blob_storage_connector.py` script either of which can be run.

When running this way, the config.py file is read to configure the app, and any one of settings can be
overridden with environment settings.

You should see output similar to this:
```shell
2025-05-21 13:36:15,001 INFO     logging.py          : Log level set to DEBUG
INFO:     Started server process [81998]
INFO:     Waiting for application startup.
2025-05-21 13:36:15,723 INFO     dsx_connector.py    : Connection to dsx-connect at http://0.0.0.0:8586 success.
2025-05-21 13:36:15,723 INFO     azure_blob_storage_connector.py: Starting up connector google-cloud-storage-connector-9788
2025-05-21 13:36:15,723 INFO     azure_blob_storage_connector.py: azure-blob-storage-connector-9788 version: 0.1.0.
2025-05-21 13:36:15,724 INFO     azure_blob_storage_connector.py: azure-blob-storage-connector-9788 configuration: name='azure-blob-storage-connector' connector_url=HttpUrl('http://0.0.0.0:8595/') item_action=<ItemActionEnum.TAG: 'tag'> dsx_connect_url=HttpUrl('http://0.0.0.0:8586/') test_mode=True gcs_bucket='lg-test-01' gcs_prefix='' gcs_recursive=True item_action_move_prefix='dsxconnect-quarantine'.
2025-05-21 13:36:15,724 INFO     azure_blob_storage_connector.py: azure-blob-storage-connector:azure-blob-storage-connector-9788 startup completed.
2025-05-21 13:36:15,733 INFO     dsx_connector.py    : Connection to dsx-connect at http://0.0.0.0:8586 success.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8595 (Press CTRL+C to quit)
```


#### Azure Connection String
In order to access Azure Blob Storage, this connector currently uses connection strings.  To get a connection string:
From Azure Portal
1. Go to Azure Portal.
2. Navigate to your Storage Account.
3. In the left-hand menu, click Access keys (under "Security + networking").
   * You'll see two key sets. Under either one:
     * Click Show keys.
     * Copy the Connection string (starts with DefaultEndpointsProtocol=https;...).

You will then either need to store this connection string as an environment setting, or pass it as an environment
setting when running the connector:
```shell
export AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net"
```
or
```shell
AZURE_STORAGE_CONNECTION_STRING="..." python start.py
```
#### Changing Configuration
The easiest method is to edit `config.py` directly, however, just keep in mind that config.py file
defines all of the default config values.  When you good to release a connector, you will likely want
your defaults to make sense (i.e. most of the time, the default value suffices), as opposed to
whatever settings you tested the connector with.   The best way to test connectors with various configuration settings,
without permanently changing the defaults, is to override settings with environment settings.

For example, in the config file:
```python
    name: str = 'azure-blob-storage-connector'
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
```
if you wanted to change the dsx_connect_url, you can do so adding a setting on the command to run:
```
DSXCONNECTOR_DSX_CONNECT_URL=https://sdfsdfgasdg.aws.com:8586 python start.py
```
or by adding this setting in a .env file.  The format for overriding a setting is:
```DSXCONNECTOR_<capitalized config variable>```


## Build a Deployment Release

Connectors use Invoke to manage tasks for bundling up files, creating requirements (for pip) and
building a Docker image.  All the steps needed to prepare a new release for deployment.

#### Prerequisites
Local running Docker instance: if building a Docker image (a "release")

#### Using invoke
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
* Docker images tagged with {{ cookiecutter.__release_name }}:<version> and {{ cookiecutter.__release_name }}:latest

Other invoke options:
* bump - increments the patch version in version.py (e.g., 1.0.0 to 1.0.1).
* clean - removes the distribution folder (dist/azure-blob-storage-connector-<version>) and its associated zip file if they exist.
* prepare - prepares files for a versioned build.  Copies and moves file into dist/azure-blob-storage-connector-<version>; generates requirements.txt.
* build - (runs bump, clean, prepare) and builds a Docker image tagged as azure-blob-storage-connector:<version> from the prepared dist folder if it doesn’t already exist.
* push - (runs build) tags the Docker image with the repository username (logangilbert/<name>:<version>) and pushes it to Docker Hub.
* release - executes the full release cycle by running the following tasks in order: bump, clean, prepare, build, and push.


