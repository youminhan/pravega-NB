## Deploy Jupyter notebook in K8S

```
git clone https://github.com/keedya/pravega-nb
cd pravega-nb
./deploy.h
```

## Deploy Pravega grpc gatway server

### Run Gateway in Dell EMC Streaming Data Platform (SDP)

- It is important to deploy GRPC gateway in the same SDP project
```
git clone https://github.com/pravega/pravega-grpc-gateway.git
cd pravega-grpc-gateway
export DOCKER_REPOSITORY=<hostname>:<port>/<namespace>
export IMAGE_TAG=0.6.0
scripts/build-k8s-components.sh
scripts/deploy-k8s-components.sh
```
