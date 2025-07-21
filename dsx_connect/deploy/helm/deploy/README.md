# Ingress Examples

These ingress examples map to different environments. Apply only what you need:

- `ingress-kind.yaml` – for local Kind clusters using nginx
- `ingress-aws-alb.yaml` – for AWS EKS with AWS ALB Ingress Controller (untested)
- `ingress-aks-appgw.yaml` – for Azure AKS using Application Gateway Ingress (untested)
- `ingress-openshift-route.yaml` – for OpenShift Routes (untested)

## Exposing Services via Ingress

Apply after deploying dsx-connect provide ingress controllers relevant to the 
kubernetes environment where deployed, to expose the dsx-connect-api.  

### Alternative Approach - Port Mapping
For development and testing, sometimes it's just easier to use port mapping.  Kubectl has a handy
port-forward command built-in:

```bash
kubectl port-forward svc/dsx-connect-api 8586:8586
```
and then dsx-connect-api can be accessed with: http://localhost:8586
This can easily be used for demos and testing, and potentially POVs, however, just know that the
port-forward runs in the foreground and holds the connection open. When you kill it
(e.g., with Ctrl+C), the port-forwarding tunnel shuts down.  You can run in the background, but
overall that's a bit hokey for something like POV (in my opinion, it "hides" how you are exposing the
internal service, which can lead to confusion).

The more proper, kubernetes way to expose a service is via ingress controllers.  The following is an example of 
deploying ingress in the local kubernetes dev platform KIND.

### Deploy an Ingress Controller for KIND
NOTE: this example just demonstrates how to install and ingress controller in KIND, using nginx ingress controller and
ni.io for the purpose of developing and testing ingress.  You will still need to port forward to the ingress controller,
so, if you are just wanting to use dsx-connect, the following doesn't really gain you anything. 

#### Deploy an Ingress Controller 
1. Make sure that the KIND node is ingress enabled.  Find the name of the node:
```shell
kubectl get nodes
```
and you'll get something like this:
```
NAME                            STATUS   ROLES           AGE   VERSION
dsx-connect-dev-control-plane   Ready    control-plane   15h   v1.33.1
```
2. Then, add a label to the node: 
```shell
kubectl label node <node name> ingress-ready=true
```

3. Install NGINX Ingress:
```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.9.4/deploy/static/provider/kind/deploy.yaml
```

Wait until ready:
```bash
kubectl get pods -n ingress-nginx
```

4. Apply ingress-kind:
```shell
kubectl apply -f deploy/ingress-kind.yaml
```

5. Check status:
```shell
kubectl get ingress
```
Result should show the port and "hostname":
```less
NAME              CLASS    HOSTS                          ADDRESS     PORTS   AGE
dsx-connect-api   <none>   dsx-connect.127.0.0.1.nip.io   localhost   80      23s
```

#### Port Forward to the Ingress Controller

```less
kubectl port-forward svc/ingress-nginx-controller -n ingress-nginx 8080:80
```
Use a browser to use dsx-connect:
http://dsx-connect.127.0.0.1.nip.io:8080