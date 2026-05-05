  export GIT_SHA=$(git rev-parse --short HEAD)
  docker compose -f automation/docker/docker-compose.yml build --no-cache
  docker compose -f automation/docker/docker-compose.yml push   # pushes :<sha>

  # push :latest for each service
  for repo in \
    sanketikahub/dpg-action-gateway \
    sanketikahub/dpg-agent-core \
    sanketikahub/dpg-dev-kit \
    sanketikahub/dpg-knowledge-engine \
    sanketikahub/dpg-memory-layer \
    sanketikahub/dpg-observability-layer \
    sanketikahub/dpg-reach-layer-voice \
    sanketikahub/dpg-reach-layer-web \
    sanketikahub/dpg-trust-layer; do
    docker buildx imagetools create -t ${repo}:latest ${repo}:${GIT_SHA}
  done