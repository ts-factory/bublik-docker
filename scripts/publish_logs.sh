#!/bin/bash

COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-bublik}
BUBLIK_DOCKER_DATA_DIR=${BUBLIK_DOCKER_DATA_DIR:-./data}

CONTAINER_NAME=$(docker ps --format '{{.Names}}' | grep "${COMPOSE_PROJECT_NAME}-te-log-server")
docker exec -it "${CONTAINER_NAME}" /bin/bash -c "/home/te-logs/bin/publish-incoming-logs"
