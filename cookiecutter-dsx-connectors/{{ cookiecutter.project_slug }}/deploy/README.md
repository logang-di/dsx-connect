# {{ cookiecutter.project_name }}

{{ cookiecutter.project_short_description }}

## Overview

This connector provides and integration for dsx-connect with a {{ cookiecutter.repository }}.  When this
connector is running, you can get the status of the connector from its home page, typically:
```http request
{{ cookiecutter.__base_connector_url }}
```

and the API that it serves can be accessed via:

```http request
{{ cookiecutter.__base_connector_url }}/docs
```

## Deploying Connector
### Docker Compose
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

This package contains an easy to use docker-compose.yaml file for configuration and deployment of the Connector in a docker environment.

### TLS/SSL (HTTPS)

- Dev certs are packaged into the image at `/app/certs` (see `connectors/framework/deploy/certs`). To enable HTTPS:
  - `DSXCONNECTOR_USE_TLS=true`
  - `DSXCONNECTOR_TLS_CERTFILE=/app/certs/dev.localhost.crt`
  - `DSXCONNECTOR_TLS_KEYFILE=/app/certs/dev.localhost.key`
- Outbound verification to dsx_connect (when DSX Connect runs HTTPS):
  - `DSXCONNECTOR_VERIFY_TLS=true|false`
  - `DSXCONNECTOR_CA_BUNDLE=/app/certs/ca.pem` (optional private CA)
- For staging/production, replace certs via bind mounts or bake your own into the image.

Dev builds: you can optionally auto-generate the dev certs during export by setting `GEN_DEV_CERTS=1` when running the invoke tasks (e.g., `GEN_DEV_CERTS=1 invoke release`).

#### Using your own TLS certificates (production)
- Option A: Volume‑mount certs (no image rebuild)
  ```yaml
  services:
    sharepoint_connector:
      volumes:
        - ./certs:/app/certs:ro
      environment:
        DSXCONNECTOR_USE_TLS: "true"
        DSXCONNECTOR_TLS_CERTFILE: "/app/certs/server.crt"
        DSXCONNECTOR_TLS_KEYFILE: "/app/certs/server.key"
  ```
  Ensure files are readable by the container user (e.g., 0644), or rebuild the image to set ownership.
- Option B: Bake certs into the image and set 0644/0600 permissions.
- For staging/production, replace certs via bind mounts or bake your own into the image.

### Config via docker-compose

#### Connector service configuration
This connector's configuration has defaults defined in the config.py file in this same directory, a Pydantic
BaseSettings class.  Pydantic is used because it provides data validation and type safety, and a class structure for easy
and IDE friendly development.  Pydantic also has convenient built-in functions so that users
can override default settings with .env files or environment settings (among other mechanisms), which is a preferred
method to configure docker containers deployed in dockers or kubernetes.

While the config.py file defines all of the defaults, you probably don't want to edit these directly in the
python script unless you want to permanently change the defaults settings.

To configure this connector (and override config.py defaults), you simply set name=value environment settings by
specifying DSXCONNECTOR_<NAME_OF_SETTING>=<value> (note all CAPS)

```yaml
      environment:
        - PYTHONUNBUFFERED=1
        - LOG_LEVEL=debug
        - DSXCONNECTOR_CONNECTOR_URL=http://{{ cookiecutter.__release_name }}:{{ cookiecutter.connector_port }} # this should match the aliases below
        - DSXCONNECTOR_DSX_CONNECT_URL=http://dsx-connect-api:8586 # note, this works if running on the same internal network on Docker as the dsx_connect_api...
        - DSXCONNECTOR_ITEM_ACTION=nothing # defines what action, if any, for a connector to take on malicious files (nothing, delete, tag, move, move_tag)
        - DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO=dsxconnect-quarantine # if item action is move or move_tag, specify where to move (to be interpreted by the connector).
          # This could be a folder on storage, a quarantine bucket, or other instructions, again, to be interpreted by the connector
        - DSXCONNECTOR_ASSET=lg-test-01 # identifies the asset this Connector can on demand full scan  - i.e., a bucket, blob container, etc.... To be interpreted by the Connector
        - DSXCONNECTOR_FILTER=  # rsync-like include/exclude patterns; leave empty to scan all

### DSXCONNECTOR_FILTER (rsync-like)

Use rsync-like include and exclude rules to control which items are scanned under DSXCONNECTOR_ASSET. Leave empty ("") to scan everything.

General concepts:
- a '?' matches any single character except a slash (/).
- a '*' matches zero or more non-slash characters.
- a '**' matches zero or more characters, including slashes.
- '-' or '--exclude' means: exclude the following match
- no prefix, or '+' or '--include' means: include the following match
- For a comprehensive guide on rsync filters: rsync filter rules

Examples (all filters branch off of DSXCONNECTOR_ASSET):

| DSXCONNECTOR_FILTER                                     | Description                                                                                                                             |
|---------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------|
| ""                                                      | All files in tree and subtrees (no filter)                                                                                              |
| "*"                                                     | Only top-level files (no recursion)                                                                                                     |
| "sub1"                                                  | Files within subtree 'sub1' and recurse into its subtrees                                                                               |
| "sub1/*"                                                | Files within subtree 'sub1', not including subtrees.                                                                                    |
| "sub1/sub2"                                             | Files within subtree 'sub1/sub2', recurse into subtrees.                                                                                |
| "*.zip,*.docx"                                          | All files with .zip and .docx extensions anywhere in the tree                                                                           |
| "-tmp --exclude cache"                                  | Exclude noisy directories (tmp, cache) but include everything else                                                                      |
| "sub1 -tmp --exclude sub2"                              | Combine includes and excludes - scan under 'sub1' subtree, but skip 'tmp' or 'sub2' subtrees                                           |
| "test/2025*/*"                                          | All files in subtrees matching 'test/2025*/*'. Does not recurse.                                                                        |
| "test/2025*/** -sub2"                                   | All files in subtrees matching 'test/2025*/*' and recursively down. Skips any subtree 'sub2'.                                          |
| "'scan here' -'not here' --exclude 'not here either'"   | Quoted tokens (spaces in dir names)                                                                                                     |


        <connector specific configuration>
```

##### Networking
The remainder is configuration of this service, and the docker network this connector shares with
DSX Connect.  The external name of the dsx-network below should be the same as the network
DSX Connect uses, if deployed within the same docker environment.

```yaml
      networks:
        dsx-network:
        aliases:
          - {{ cookiecutter.__release_name }}  # this is how dsx-connect will communicate with this on the network
      command:
        python connectors/{{ cookiecutter.repository }}/{{ cookiecutter.project_slug }}_connector.py
```

```yaml
# The following assumes an already created docker network like this:
# docker network create dsx-connect-network --driver bridge
networks:
  dsx-network:
    external: true
    name: dsx-connect-network  # change this to an existing docker network
```
#### Deployment of a Connector
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
  {{ cookiecutter.project_slug }}_connector: # if deploying two of more of this service within a single docker, this name must be unique for each instance
```
change the service name to something unique like:
```yaml
services:
  {{ cookiecutter.project_slug }}_connector_02: # if deploying two of more of this service within a single docker, this name must be unique for each instance
```
to differentiate it from another service like "{{ cookiecutter.project_slug }}_connector_01"

Next, change "{{ cookiecutter.__release_name }}" in the following:
```yaml
    environment:
      - DSXCONNECTOR_CONNECTOR_URL=http://{{ cookiecutter.__release_name }}:{{ cookiecutter.connector_port }} # see aliases below
    networks:
      dsx-network:
        aliases:
          - {{ cookiecutter.__release_name }}
```
to somthing unique and matching like:
```yaml
    environment:
      - DSXCONNECTOR_CONNECTOR_URL=http://{{ cookiecutter.__release_name }}-02:{{ cookiecutter.connector_port }} # see aliases below
    networks:
      dsx-network:
        aliases:
          - {{ cookiecutter.__release_name }}-02
```
