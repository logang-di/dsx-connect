# Filesystem Connector

Filesystem Connector <-*-> DSX-Connect integration.

## Overview

This connector provides and integration for dsx-connect with a Filesystem.  

## Deploying Connector via Docker Compose

This deployment model is designed for fast, low-friction evaluation of the DSX Connect platform. It prioritizes
simplicity and ease of setup over scalability or fault tolerance, making it ideal for:
* Sales engineer demos
* Customer proofs of concept
* Internal development or QA testing

Characteristics:
* Docker-based deployment using docker-compose
* Minimal external dependencies
* Runs on a single VM, developer laptop, or cloud container service
* Easily portable across AWS, Azure, GCP, and OCI

This model is intended to get users up and running quickly without needing to provision or manage Kubernetes clusters
or complex infrastructure.

### Quick Start

For most testing and proof-of-concept deployments, you only need to modify one setting in the docker-compose file to get started quickly.

**Prerequisites:**
- DSX-Connect must already be deployed and running on the same Docker host
- Docker network `dsx-connect-network` must exist (created during DSX-Connect deployment)

**Minimal Configuration:**
1. **Edit the scan folder** in the volumes section:
  
    Change the source: to a folder on your local filesystem with files you want to scan. This will be mapped in the docker container to /app/scan_folder.

    ```yaml
    volumes:
      - type: bind
        source: /path/to/local/folder # modify to the directory to be scanned and monitored
        target: /app/scan_folder
    ```

2.  **Deploy the connector:**
   ```bash
   docker-compose -f docker-compose-filesystem-connector.yaml up -d
   ```

**What You Get with Minimal Config Changes:**
- **Full recursive scanning** of your specified directory
- **No action taken** on malicious files (files remain in place for analysis)
- **Automatic registration** with DSX-Connect (if DSX-Connect is running)
- **Web dashboard access** at `http://localhost:8586` for connector status
- **API documentation for connector** at `http://localhost:8590/docs`

**How to Use:**
1. **Initiate scans** from either:
    - DSX-Connect dashboard (recommended) - view all connectors and results
    - Connector API at `http://localhost:8590/docs` - use the `/full_scan` endpoint

2. **View scan results** in the DSX-Connect dashboard where you can:
    - See scan progress and completion status
    - Review malicious file detections
    - Examine clean file reports
    - Monitor connector health and statistics

This basic configuration is perfect for evaluation, testing, and demonstrations. For POV deployments or specific use 
cases, see the detailed configuration sections below to customize file filtering, post-scan actions, and security settings.


### Port and Volume Maps

The filesystem connector requires configuration of ports and two volume mappings for proper operation.

```yaml
      ports:
        - "8590:8590"
      volumes:
        - type: bind
          source: /path/to/local/folder # modify to the directory to be scanned and monitored
          target: /app/scan_folder
        - type: bind
          source: /path/to/local/folder/dsxconnect-quarantine # modify to the directory where files will be moved
          target: /app/quarantine
```

#### Port Configuration
**Format:** `<external port>:<internal port>`
- **External port (8590)**: Port accessible from the Docker host system
- **Internal port (8590)**: Port the connector listens on inside the container

Change the external port if there are conflicts with other services on the Docker host system. This commonly occurs when deploying multiple connectors on the same host.

#### Volume Configuration
The filesystem connector requires two volume mappings:

**1. Scan Directory Volume:**
- **Host Path**: `/path/to/local/folder` (modify to your scan directory)
- **Container Path**: `/app/scan_folder` (fixed - do not change - mirrors setting in environment)
- **Purpose**: Directory containing files to be scanned for malware

**2. Quarantine Directory Volume:**
- **Host Path**: `/path/to/local/folder/dsxconnect-quarantine` (modify to your quarantine location)
- **Container Path**: `/app/quarantine` (fixed - do not change - mirrors setting in environment)
- **Purpose**: Directory where malicious files will be moved when `DSXCONNECTOR_ITEM_ACTION` is set to `move`


### Environment Settings
To configure this connector, set name=value environment settings by
specifying DSXCONNECTOR_<NAME_OF_SETTING>=`<value>` (note all CAPS)

```yaml
      DSXCONNECTOR_CONNECTOR_URL: "http://filesystem-connector:8590" # see aliases below
      DSXCONNECTOR_DSX_CONNECT_URL: "http://dsx-connect-api:8586" # note, this works if running on the same internal network on Docker as the dsx_connect_core...
      DSXCONNECTOR_ASSET: "/app/scan_folder"
      DSXCONNECTOR_FILTER: ""
      DSXCONNECTOR_ITEM_ACTION: "nothing"
      DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO: "/app/quarantine" # this directory should have been created in the Dockerfile

      # This is definition is for docker compose deployments for dev, demos, POVs, and testing. Proper production
      # deployments should use deployment mechanisms (helm charts/k8s) where API Keys can be defined as secrets
      DSXCONNECTOR_API_KEY: "api-key-NOT-FOR-PRODUCTION"
```

#### Environment Variable Definitions

**Core Connectivity:**
- **DSXCONNECTOR_CONNECTOR_URL**: The URL where this connector instance is accessible. This should match the Docker service name and port. Used by DSX-Connect to communicate back to this connector for file retrieval and actions. Format: `http://service-name:port`.   

- **DSXCONNECTOR_DSX_CONNECT_URL**: The URL of the DSX-Connect API service. This connector will send scan requests to this endpoint. Must be accessible from within the Docker network. Format: `http://dsx-connect-api:8586`

**File System Configuration:**
- **DSXCONNECTOR_ASSET**:  Directory path within the container that will be scanned for files. This should correspond to the volume mount point defined in the docker-compose volumes section: `/app/scan_folder`.  Typically leave as is and modify the volume mount point instead.

- **DSXCONNECTOR_FILTER**: File filter patterns based on `rsync` include and exclude patterns. Leave empty (`""`) to scan all files recursively under DSXCONNECTOR_ASSET, 
or specify rsync-based patterns for subdirectory structure and/or file extensions.  General concepts:
  - a '?' matches any single character except a slash (/).
  - a '*' matches zero or more non-slash characters.
  - a '**' matches zero or more characters, including slashes.
  - '-' or '--exclude' means: exclude the following match
  - no prefix, or '+' or '--include' means: include the following match
  - For a comprehensive guide on rsync filters: [rsync filter rules](https://man7.org/linux/man-pages/man1/rsync.1.html#FILTER_RULES)
  - **Examples**:

   | DSXCONNECTOR_FILTER                                     | Description (all filters branch off of DSXCONNECTOR_ASSET)                                                                                                                       |
    |---------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
    | `""`                                                    | All files in tree and subtrees (no filter)                                                                                                                                       |
    | `"*"`                                                   | Only top-level files (no recursion)                                                                                                                                              |
    | `"sub1"`                                                | Files within subtree 'sub1' and recurse into its subtrees                                                                                                                        |
    | `"sub1/*"`                                              | Files within subtree 'sub1', not including subtrees.                                                                                                                             |
    | `"sub1/sub2"`                                           | Files within subtree 'sub1/sub2', recurse into subtrees.                                                                                                                         |
    | `"*.zip,*.docx"`                                        | All files with .zip and .docx extensions anywhere in the tree                                                                                                                    |
    | `"-tmp --exclude cache"`                                | Exclude noisy directories (tmp, cache) but include everything else                                                                                                               |
    | `"sub1 -tmp --exclude sub2"`                            | Combine includes and excludes - scan under 'sub1' subtree, but skip 'tmp' or 'sub2' subtrees (note the mix of exclude prefixes '-' and '--excludes', either are valid)           |
    | `"test/2025*/*"`                                        | All files in subtrees matching 'test/2025*/*'. Example: test/2025-01-15, test/2025-07-30, test/2025-08-12.  Does not recurse.                                                    |
    | `"test/2025*/** -sub2"`                                 | All files in subtrees matching 'test/2025*/*' and recursively down.  Skips any subtree 'sub2'. Example: test/2025-01-15, test/2025-07-30, test/2025-07-30/sub1, test/2025-08-12. |
    | `"'scan here' -'not here' --exclude 'not here either'"` | Quoted tokens (spaces in dir names)                                                                                                                                              |
  

**Post-Scan Actions:**
- **DSXCONNECTOR_ITEM_ACTION**: Action to take on files after scanning. Options include:
    - `nothing`: No action taken (files remain in place)
    - `delete`: Delete malicious files
    - `move`: Move malicious files to `DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO` directory

- **DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO**: Directory path where files will be moved when `DSXCONNECTOR_ITEM_ACTION` is set to `move`. This directory should be created in the container and accessible via volume mount: `/app/quarantine`.  Leave as is and redesfine the host mount volume. 

**Security:**
- **DSXCONNECTOR_API_KEY**: API key for authenticating requests between the connector and DSX-Connect. This is a shared secret that must match the key configured in DSX-Connect. For production deployments, use proper secret management instead of plain text environment variables.



### Network Settings
Typically none of this needs to be changed.  Just note that *dsx-network* needs to exist on the docker 
environment where deployed and should match the network where dsx-connect is deployed.

Note that the alias is mirrored in the DSXCONNECTOR_CONNECTOR_URL setting above.

```yaml
    networks:
      dsx-network:
        aliases:
          - filesystem-connector  # this is how dsx-connect will communicate with this on the network
```


### Deployment
Run docker compose from the same directory as the docker-compose.yaml file using
up command (-d to detach from execution)
```shell
docker-compose -f <docker compose file>.yaml up -d
```
To shut down:
```shell
docker-compose <docker compose file>.yaml down
```

#### Deployment of Two or More Connectors in Same Docker Environment
In the case that you want two or more dsx-connectors running in the same docker host (for example, if each connector
scans different parts of a file repository), you will need to make sure that they are uniquely identifiable as a service
and on the internal docker network.

First, in the docker compose .yaml deployment file:
```yaml
services:
  filesystem_connector: # if deploying two of more of this service within a single docker, this name must be unique for each instance
```
change the service name to something unique like:
```yaml
services:
  filesystem_connector_02: # if deploying two of more of this service within a single docker, this name must be unique for each instance
```
to differentiate it from another service like "filesystem_connector_02"

Next, change "filesystem-connector" in the following:
```yaml
    environment:
      - DSXCONNECTOR_CONNECTOR_URL=http://filesystem-connector:8590 # see aliases below
    networks:
      dsx-network:
        aliases:
          - filesystem-connector
```
to somthing unique and matching like:
```yaml
    environment:
      - DSXCONNECTOR_CONNECTOR_URL=http://filesystem-connector-02:8591 # see aliases below
    networks:
      dsx-network:
        aliases:
          - filesystem-connector-02
```
