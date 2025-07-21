# {{ cookiecutter.project_name }}

{{ cookiecutter.project_short_description }}

## Overview

This connector provides and integration for DSX-Connect with a {{ cookiecutter.repository }}.  When this
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

#### Config via docker-compose

The first part that should be changed, the ports this service listens on (optional), and a
volume definition (if necessary).  For the Connector you are mounting the folder that you want to
scan external to the docker environment, and what that maps to within the connector.

##### Port and Volume Maps

```yaml
      ports:
        - "{{ cookiecutter.connector_port }}:{{ cookiecutter.connector_port }}"
```

##### Connector service configuration
This connector's configuration has defaults defined in the config.py file in this same directory, a Pydantic
BaseSettings class.  Pydantic is used because it provides data validation and type safety, and a class structure for easy
and IDE friendly development.  Pydantic also has convenient built-in functions so that users
can override default settings with .env files or environment settings (among other mechanisms), which is a preferred
method to configure docker containers deployed in dockers or kubernetes.

While the config.py file defines all fo the defaults, you probably don't want to edit these directly in the
python script unless you want to permanently change the defaults settings.

To configure this connector (and override config.py defaults), you simply set name=value environment settings by
specifying DSXCONNECTOR_<NAME_OF_SETTING>=<value> (note all CAPS)

```yaml
      environment:
        - PYTHONUNBUFFERED=1
        - LOG_LEVEL=debug
        - DSXCONNECTOR_CONNECTOR_URL=http://{{ cookiecutter.__release_name }}:{{ cookiecutter.connector_port }} # see aliases below
        - DSXCONNECTOR_DSX_CONNECT_URL=http://dsx-connect-api:8586 # note, this works if running on the same internal network on Docker as the dsx_connect_core...
        - DSXCONNECTOR_ITEM_ACTION=nothing # defines what action, if any, for a connector to take on malicious files (nothing, delete, tag, move, move_tag)
        - DSXCONNECTOR_ITEM_ACTION_MOVE_METAINFO=dsxconnect-quarantine # if item action is move or move_tag, specify where to move (to be interpreted by the connector).
          # This could be a folder on storage, a quarantine bucket, or other instructions, again, to be interpreted by the connector
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
#### Deployment
Run docker compose from the same directory as the docker-compose.yaml file using
up command (-d to detach from execution)
```shell
docker-compose up -d
```
To shut down:
```shell
docker-compose down
```

