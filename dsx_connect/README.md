# dsx-connect Installation Quick Start

## Distribution Structure
The distribution (dsx-connect-<version>/) contains:

dsx_connect/: Core application code (FastAPI app, Celery workers, etc.).
utils/: Shared modules.
Dockerfile, docker-compose.yaml: Docker deployment files.
dsx-connect-start.py: Helper script to run the FastAPI app locally.
requirements.txt: Python dependencies.
README.md: This file.


## dsx-connect Components

![img.png](diagrams/img.png)
* dsx-connect app: 
  * a FastAPI app, that provides the API endpoints that Connectors talk to.  
  * Manages configuration of architecture
* Scan Request Queue and Verdict Queue - implemented as a Redis queues.  
* Scan Request Worker and Verdict Worker - Celery apps which can be deployed individually or together

## Deployment
There are two modes for deploying dsx-connect.  One mode is a testing mode in which only the dsx-connect app 
needs to be running.  The other is a full deployment mode, which is what you should opt for in environments 
where a lot of files need processing.  

## Test Deployment (Quick Start)

In this type of deployment, only dsx-connect needs to be running, along with 
whatever connector you are using and DSXA.

![img_2.png](diagrams/img_2.png)

When a Connector is run in Test mode (more on this later), it will call the dsx-connect API endpoint: test/scan_request 
on the dsx-connect app.  The dsx-connect app will then request read_file from the Connector, 
and then scan the file, and finally invoke item_action if necessary.   

The easiest way to start this workflow, is to spawn a Connector and invoke a full_scan on the Connector
by calling its full_scan API.

### Running on the Command Line
The simplest way to start is to simply run a connector and dsx-connect from the command line.  
The following assumes a running publicly accessible DSXA instance.

Grab a release of dsx-connect, and navigate to the root directory.  Start by installing all 
necessary modules.

(optional) Start by creating a new python virtual environment within the dsx-connect to avoid 
conflicts with existing python installations of modules:   
```commandline
python -m venv venv  
```
and then activate the venv:
```commandline
./venv/Scripts/activate (Mac / Linux) or .\venv\Scripts\activate (Windows)
```
Install requirements:
```
pip install -r requirements.txt
```
Next, we will likely want to change some configurations in dsx-connect, starting with the DSXA scanner 
it's connected.  The easiest way to do this is via changing environment settings to override defaults.

The dsx_config/config.py file defines all fo the defaults (using Pydantic's BaseSettings), however, you 
probably don't want to edit these directly in the python script unless you want to permanently change
the defaults (for example, to set the scan_binary_url to a known, always on, DSXA instance.)

#### Option 1: Export Environment Settings  
```shell
export LOG_LEVEL=debug
export DSXCONNECT__SCANNER__SCAN_BINARY_URL=http://new-url.com/scan/binary/v2
```

#### Option 2: Pass Environment Settings on Command Line (recommended)
You can also just make these settings before launching dsx-connect on the same command line.  
For example, if you wanted to set the LOG_LEVEL to debug, you would simply do something like this:
```shell
LOG_LEVEL=debug python dsx-connect-api-start.py
```

#### Starting the dsx-connect app

There is a helper script called dsx-connect-start.py in the root directory.  It simply encapsulates
the call to launch the uvicorn ASGI and host the dsx-connect app.

Here's how to launch with overrides to LOG_LEVEL and the SCAN_BINARY_URL
```shell
LOG_LEVEL=debug \
DSXCONNECT__SCANNER__SCAN_BINARY_URL=http://a0c8b85f8a14743c6b3de320b780a359-1883598926.us-west-1.elb.amazonaws.com/scan/binary/v2 \
python dsx-connect-api-start.py
```

You should see output like this:
```shell
2025-04-25 13:00:19,487 INFO     logging.py          : Log level set to INFO
INFO:     Started server process [75934]
INFO:     Waiting for application startup.
2025-04-25 13:00:19,982 INFO     dsx_connect_api.py  : dsx-connect version: <module 'dsx_connect.version' from '/Users/logangilbert/PycharmProjects/SEScripts/build/dist/dsx-connect-0.0.19/dsx_connect/version.py'>
2025-04-25 13:00:19,982 INFO     dsx_connect_api.py  : dsx-connect configuration: results_database=DatabaseConfig(type='tinydb', loc='data/dsx-connect.db.json', retain=1000) scanner=ScannerConfig(scan_binary_url='http://a0c8b85f8a14743c6b3de320b780a359-1883598926.us-west-1.elb.amazonaws.com/scan/binary/v2')
2025-04-25 13:00:19,982 INFO     dsx_connect_api.py  : dsx-connect startup completed.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8586 (Press CTRL+C to quit)
```

You can now open a browser to view dsx-connect's APIs:
http://0.0.0.0:8586

And should see a page like this:

![img_1.png](diagrams/img_5.png)

Note the scanner -> scan_binary_url should show the URL you overrode in the environment setting. 

# dsx-connect Workflow

![img_3.png](diagrams/img_3.png)
Complete description of workflow here:
https://di-jira.atlassian.net/wiki/x/JoDJOQE

# Creating New Releases 

To create a new release of DSX-Connect using invoke tasks:

1. Prerequisites:

* Install invoke and dependencies:cd dsx-connect/dsx_connect
```pip install invoke -r requirements.txt```

* Ensure Docker is installed and you’re authenticated with Docker Hub (if using the push task):
```docker login```

2. Run the Release Task:

From the dsx_connect/ directory, execute:
```inv release```

* This performs the following:
  * Bumps the patch version in version.py (e.g., 0.1.0 to 0.1.1). 
  * Cleans previous build artifacts (dist/). 
  * Prepares the distribution (dsx-connect-<version>/) with application code, Dockerfile, docker-compose.yaml, and helper files. 
  * Zips the distribution into dsx-connect-<version>.zip. 
  * Builds the Docker image (dsx-connect:<version>). 
  * Pushes the image to Docker Hub (logangilbert/dsx-connect:<version>).


3. Verify the Release:

Check the new distribution in dist/dsx-connect-<version>/.
Test the Docker image:
```cd dist/dsx-connect-<version>```
```docker-compose up -d```

Access the the app home page at http://localhost:8586.


Individual Tasks (Optional):

Run specific tasks if needed:
```
inv bump  # Increment version
inv clean  # Remove build artifacts
inv prepare  # Prepare distribution files
inv zip  # Create zip archive
inv build  # Build Docker image
inv push  # Push to Docker Hub
```

# Deployment via Docker Compose

Deployment via Docker Compose
To deploy DSX-Connect using Docker Compose:

1. Navigate to the Distribution:
```cd dsx-connect-<version>```

2. Build the Docker Image (if not already built):
Use invoke release (see above) or
```docker build -t dsx-connect:<version> .```

3. Start the Services:
```docker-compose up -d```

Should result in this (replacing 0110 with the version of the image used):
```
[+] Running 4/4
✔ Network dsx-connect-0110_dsx-network              Created                                                                                                                                                                               0.1s
✔ Container dsx-connect-0110-redis-1                Started                                                                                                                                                                               0.3s
✔ Container dsx-connect-0110-dsx_connect_workers-1  Started                                                                                                                                                                               0.4s
✔ Container dsx-connect-0110-dsx_connect_app-1      Started
```                                                                                                                                                                               


4. Verify Deployment:


* The FastAPI app runs on http://localhost:8586/docs.
* Celery workers and Redis run in the background, connected via the dsx-network bridge.
* Check container status: 
  * ```docker-compose ps```
```                                                                                                                                                                               0.4s
NAME                                     IMAGE                                  COMMAND                  SERVICE               CREATED         STATUS         PORTS
dsx-connect-0110-dsx_connect_app-1       dsx-connect-0110-dsx_connect_app       "uvicorn dsx_connect…"   dsx_connect_app       5 seconds ago   Up 5 seconds   0.0.0.0:8586->8586/tcp
dsx-connect-0110-dsx_connect_workers-1   dsx-connect-0110-dsx_connect_workers   "celery -A dsx_conne…"   dsx_connect_workers   5 seconds ago   Up 5 seconds   
dsx-connect-0110-redis-1                 redis:6                                "docker-entrypoint.s…"   redis                 6 seconds ago   Up 5 seconds   6379/tcp
```

5. Stop the Services:
```docker-compose down```

Note: The docker-compose.yaml is at the distribution root, following standard Docker conventions. Ensure port 8586 is free or edit docker-compose.yaml to use a different port.


# Testing with a Connector

## TODO

DSX-Connect integrates with connectors (e.g., filesystem-connector) for extended functionality. To test with a connector in the monorepo:

Prerequisites:

Ensure the monorepo (dsx-connect/) is cloned, with dsx_connect/ and connectors/ (e.g., connectors/filesystem/).
Install invoke and dependencies in both dsx_connect/ and the connector directory:cd dsx-connect/dsx_connect
pip install invoke -r requirements.txt
cd ../connectors/filesystem
pip install invoke -r requirements.txt

Run DSX-Connect:

Start the DSX-Connect services:cd dsx-connect/dsx_connect
inv build
inv run


This starts the FastAPI app (http://localhost:8586/docs), Celery workers, and Redis.


Build and Run the Connector:

Navigate to the connector directory:cd dsx-connect/connectors/filesystem


Build the connector’s Docker image:inv build


Run the connector:inv run


The connector typically exposes its API (e.g., http://localhost:8587/docs for filesystem).


Test Integration:

Use the DSX-Connect Swagger UI (http://localhost:8586/docs) to submit scan requests.
Verify the connector processes requests via its API (http://localhost:8587/docs).
Check Celery worker logs for task processing:docker logs dsx_connect_workers




Stop Services:

Stop the connector:cd dsx-connect/connectors/filesystem
docker-compose down


Stop DSX-Connect:cd dsx-connect/dsx_connect
docker-compose down





Note: Each connector may have specific configuration (e.g., environment variables, ports). Refer to the connector’s README.md or tasks.py for details.
Troubleshooting

ModuleNotFoundError: No module named 'dsx_connect':Use dsx-connect-start.py or set PYTHONPATH=. when running Uvicorn manually.
Port conflicts:Edit dsx-connect-start.py or docker-compose.yaml to change the port (e.g., 8000).
Docker build issues:Ensure Dockerfile and requirements.txt are in the distribution root.

Development
For the full monorepo, see the source repository. The monorepo uses dsx_connect/deploy/ for Dockerfile and docker-compose.yaml, but the distribution places them at the root for simplicity.
