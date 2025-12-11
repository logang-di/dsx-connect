
- [ ] I would separate launching DSX/A and the DSX-Connect core into 2 separate sections as this will make it easier to follow for newbie customers who might not be too familiar with docker/docker compose.
- [ ] Need to add in instructions on where to download the files from.
- [ ] Add in pre reqs to copy the downloaded files to a folder on the Linux box then run the commands from that location e.g. DSX-Connect.
- [ ] Do they need to map/login to the docker repo to get the other images?  Need instructions for that too.
- [ ] After creating the docker network maybe add in a command to verify that it has been created “docker network ls”
- [ ] I think remove option A and B for variables and just go with using the environment file as our recommended approach.
- [ ] Separate out the creation of the env file into a separate task then the “docker compose up” command as another task after.
- [ ] Remove the https:// in the appliance URL for the env file example as it doesn’t work with it.
- [ ] I think we should encourage the customer to get the DSX/A image from the appliance, so most likely they will use the local repo, so I think we change the example to point to that and not the dsxconnect/dpa-rocky **** etc.
- [ ] I got this error below when running the docker compose command:
- [ ] [ryan@rocky9 dsxconnect_files]$ docker compose --env-file dsxa.env -f docker-compose-dsxa.yaml up -d 

  - WARN[0000] Found orphan containers ([dsxconnect_files-filesystem_connector-1 dsxconnect_files-dsx_connect_results_worker-1 dsxconnect_files-dsx_connect_verdict_action_worker-1 dsxconnect_files-dsx_connect_scan_request_worker-1 dsxconnect_files-syslog-1 dsxconnect_files-dsx_connect_notification_worker-1 dsxconnect_files-dsx_connect_api-1 dsxconnect_files-redis-1 dsxconnect_files-rsyslog-1]) for this project. If you removed or renamed this service in your compose file, you can run this command with the --remove-orphans flag to clean it up.
- [ ] Once dsx connect is up I think we need to add some commands to verify like docker ps then docker logs and tell the customer what to look for e.g. Registration succeeded and then Classifier initialized. Result: true.
- [ ] The document mentions “The compose file binds DSXA to the shared dsx-connect-network and exposes port 5000 on the host. Adjust the environment values above as needed; no YAML edits are required.” But there is no option to change/specify the port in the env file can this be added in as an example?
- [ ] Change docker compose to remove/docker at the front as the assumption is they have downloaded the files and copied them to the linux box.
- [ ] Got this error when running the dsx-connect all services command

   - WARN[0010] Found orphan containers ([dsxconnect_files-dsxa_scanner-1 dsxconnect_files-filesystem_connector-1 dsxconnect_files-syslog-1]) for this project. If you removed or renamed this service in your compose file, you can run this command with the --remove-orphans flag to clean it up.
Got this error when running the dsx-connect all services command
- [ ] I think maybe we remove the add file system from quick start as the idea is that we just get the DSX/A and DSX-Connect system up and running.  Then the customer can go to the connectors section to follow the install guide for the connector as needed.  Maybe we can suggest that the file system is the easiest connect to start testing with in quick start?
- [ ] I think the connectors section should be an overview of what they are (connector concepts already created) and how they work then detailed deployment guides for each, what permissions you need and how to setup and test. how to deploy