# AWS S3 Connector

AWS S3 Connector <-*-> DSX-Connect integration.

## Overview

This connector provides and integration for dsx-connect with a AWS S3.  When this
connector is running, you can get the status of the connector from its home page, typically:
```http request
http://0.0.0.0:8591
```

and the API that it serves can be accessed via:

```http request
http://0.0.0.0:8591/docs
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

#### Config via docker-compose

##### Connector service configuration
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
        - DSXCONNECTOR_CONNECTOR_URL=http://aws-s3-connector:8591 # this should match the aliases below
        - DSXCONNECTOR_DSX_CONNECT_URL=http://dsx-connect-api:8586 # note, this works if running on the same internal network on Docker as the dsx_connect_api...
        - DSXCONNECTOR_ITEM_ACTION=nothing # defines what action, if any, for a connector to take on malicious files (nothing, delete, tag, move, move_tag)
        - DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO=dsxconnect-quarantine # if item action is move or move_tag, specify where to move (to be interpreted by the connector).
          # This could be a folder on storage, a quarantine bucket, or other instructions, again, to be interpreted by the connector
        - DSXCONNECTOR_ASSET=lg-test-01 # identifies the asset this Connector can on demand full scan  - i.e., a bucket, blob container, etc.... To be interpreted by the Connector
        - DSXCONNECTOR_FILTER=  # define filters on the asset, such as sub folders, prefixes, etc.... To be interpreted by the Connector
        - DSXCONNECTOR_RECURSIVE=True
        - TEST_MODE=False

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
          - aws-s3-connector  # this is how dsx-connect will communicate with this on the network
      command:
        python connectors/AWS S3/aws_s3_connector.py
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
  aws_s3_connector: # if deploying two of more of this service within a single docker, this name must be unique for each instance
```
change the service name to something unique like:
```yaml
services:
  aws_s3_connector_02: # if deploying two of more of this service within a single docker, this name must be unique for each instance
```
to differentiate it from another service like "aws_s3_connector_01"

Next, change "aws-s3-connector" in the following:
```yaml
    environment:
      - DSXCONNECTOR_CONNECTOR_URL=http://aws-s3-connector:8591 # see aliases below
    networks:
      dsx-network:
        aliases:
          - aws-s3-connector
```
to somthing unique and matching like:
```yaml
    environment:
      - DSXCONNECTOR_CONNECTOR_URL=http://aws-s3-connector-02:8591 # see aliases below
    networks:
      dsx-network:
        aliases:
          - aws-s3-connector-02
```
