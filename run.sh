#!/usr/bin/env bash
# REUSED FROM (PATTERN): Q-Build-Manager/run.sh
set -euo pipefail

APP_NAME="AKDW"
SERVICE_NAME="akdw"
ENV_FILE=".env"
ENV_EXAMPLE=".env.example"
DEFAULT_PORT="5001"
LAST_PORT_FILE=".akdw_port"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
    return
  fi
  echo ""
}

banner() {
  printf "${BLUE}===============================================================${NC}\n"
  printf "${GREEN} AKDW - Audio Kernel Driver Workbench${NC}\n"
  printf "${BLUE}===============================================================${NC}\n"
}

check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo -e "${RED}Docker is not installed. Please install Docker and retry.${NC}"
    exit 1
  fi

  if ! docker info >/dev/null 2>&1; then
    echo -e "${RED}Docker daemon is not running. Start Docker and retry.${NC}"
    exit 1
  fi
}

ensure_env() {
  if [[ ! -f "$ENV_FILE" ]]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo -e "${YELLOW}Created .env from .env.example${NC}"
    echo -e "${YELLOW}QGENIE_API_KEY is empty. Complete setup in http://localhost:<port>/setup after startup.${NC}"
  fi
}

read_env_value() {
  local key="$1"
  local default_value="$2"
  local raw
  raw="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d'=' -f2- || true)"
  raw="${raw%\"}"
  raw="${raw#\"}"
  raw="${raw%\'}"
  raw="${raw#\'}"
  if [[ -z "$raw" ]]; then
    echo "$default_value"
    return
  fi
  echo "$raw"
}

load_env() {
  HOST_WORKSPACE_PATH="$(read_env_value HOST_WORKSPACE_PATH "/local/mnt/workspace/AUDIO_KERNEL_DEVELOPMENT_WORKBENCH(AKDW)")"
  FLASK_PORT="$(read_env_value FLASK_PORT "5000")"
  AKDW_SSH_USER="$(read_env_value AKDW_SSH_USER "${USER}@$(hostname)")"
}

ensure_workspace() {
  mkdir -p "$HOST_WORKSPACE_PATH"
  mkdir -p \
    "$HOST_WORKSPACE_PATH/kernel" \
    "$HOST_WORKSPACE_PATH/patches" \
    "$HOST_WORKSPACE_PATH/sessions" \
    "$HOST_WORKSPACE_PATH/logs" \
    "$HOST_WORKSPACE_PATH/workspace"
}

port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :${port} )" | grep -q ":${port}"
    return $?
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP -sTCP:LISTEN -n -P | grep -q ":${port} "
    return $?
  fi
  return 1
}

choose_port() {
  local preferred="${AKDW_PORT:-$DEFAULT_PORT}"
  if ! port_in_use "$preferred"; then
    echo "$preferred"
    return
  fi

  for candidate in $(seq 5002 5099); do
    if ! port_in_use "$candidate"; then
      echo "$candidate"
      return
    fi
  done

  echo ""
}

show_tunnel_help() {
  local selected_port="$1"
  local tunnel_user="${AKDW_SSH_USER:-${USER}@$(hostname)}"

  echo -e "${BLUE}Port forwarding helper:${NC}"
  echo "  ssh -L ${selected_port}:localhost:${selected_port} ${tunnel_user}"
  echo "Open in browser: http://localhost:${selected_port}"
}

start_stack() {
  local compose
  compose="$(compose_cmd)"

  if [[ -z "$compose" ]]; then
    echo -e "${RED}docker-compose is not available. Install docker-compose plugin/binary.${NC}"
    exit 1
  fi

  local selected_port
  selected_port="$(choose_port)"
  if [[ -z "$selected_port" ]]; then
    read -r -p "No free port found automatically. Enter port to use: " selected_port
  fi

  echo "$selected_port" > "$LAST_PORT_FILE"

  $compose pull >/dev/null 2>&1 || true

  local existing_image
  existing_image="$($compose images -q "$SERVICE_NAME" 2>/dev/null || true)"

  if [[ -z "$existing_image" ]]; then
    echo -e "${BLUE}No existing image found. Running clean build...${NC}"
    AKDW_PORT="$selected_port" $compose build --no-cache
  else
    echo -e "${BLUE}Existing image found. Running incremental build...${NC}"
    AKDW_PORT="$selected_port" $compose build
  fi

  AKDW_PORT="$selected_port" FLASK_PORT="$FLASK_PORT" $compose up -d

  local health_url
  health_url="http://localhost:${selected_port}/health"

  echo -e "${BLUE}Waiting for health endpoint ${health_url} ...${NC}"
  for attempt in $(seq 1 10); do
    if curl -fsS "$health_url" >/dev/null 2>&1; then
      echo -e "${GREEN}AKDW is running at http://localhost:${selected_port}${NC}"
      show_tunnel_help "$selected_port"
      $compose logs -f "$SERVICE_NAME"
      return
    fi
    sleep 2
  done

  echo -e "${RED}Health check failed after 10 attempts.${NC}"
  $compose logs "$SERVICE_NAME"
  exit 1
}

stop_stack() {
  local compose
  compose="$(compose_cmd)"
  [[ -z "$compose" ]] && exit 1
  $compose down
}

restart_stack() {
  local compose
  compose="$(compose_cmd)"
  [[ -z "$compose" ]] && exit 1
  $compose restart
  if [[ -f "$LAST_PORT_FILE" ]]; then
    show_tunnel_help "$(cat "$LAST_PORT_FILE")"
  fi
}

logs_stack() {
  local compose
  compose="$(compose_cmd)"
  [[ -z "$compose" ]] && exit 1
  $compose logs -f "$SERVICE_NAME"
}

test_stack() {
  local compose
  compose="$(compose_cmd)"
  [[ -z "$compose" ]] && exit 1
  $compose run --rm "$SERVICE_NAME" pytest tests/ -v --cov=app
}

main() {
  banner

  case "${1:-start}" in
    start)
      check_docker
      ensure_env
      load_env
      ensure_workspace
      start_stack
      ;;
    stop)
      stop_stack
      ;;
    restart)
      restart_stack
      ;;
    logs)
      logs_stack
      ;;
    test)
      test_stack
      ;;
    *)
      echo "Usage: $0 [start|stop|restart|logs|test]"
      exit 1
      ;;
  esac
}

main "${1:-start}"
