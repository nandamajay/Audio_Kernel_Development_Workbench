#!/usr/bin/env bash
# REUSED FROM (PATTERN): Q-Build-Manager/run.sh
set -euo pipefail

APP_NAME="AKDW"
SERVICE_NAME="akdw"
ENV_FILE=".env"
ENV_EXAMPLE=".env.example"
HEALTH_URL="http://localhost:5001/health"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

compose_cmd() {
  if command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
    return
  fi
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
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
    echo -e "${YELLOW}Please fill in QGENIE_API_KEY in .env and re-run.${NC}"
    exit 1
  fi
}

ensure_db_file() {
  [[ -f akdw.db ]] || touch akdw.db
}

start_stack() {
  local compose
  compose="$(compose_cmd)"

  if [[ -z "$compose" ]]; then
    echo -e "${RED}docker-compose is not available. Install docker-compose plugin/binary.${NC}"
    exit 1
  fi

  $compose pull >/dev/null 2>&1 || true

  local existing_image
  existing_image="$($compose images -q "$SERVICE_NAME" 2>/dev/null || true)"

  if [[ -z "$existing_image" ]]; then
    echo -e "${BLUE}No existing image found. Running clean build...${NC}"
    $compose build --no-cache
  else
    echo -e "${BLUE}Existing image found. Running incremental build...${NC}"
    $compose build
  fi

  $compose up -d

  echo -e "${BLUE}Waiting for health endpoint...${NC}"
  local attempt
  for attempt in $(seq 1 10); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      echo -e "${GREEN}AKDW is running at http://localhost:5001${NC}"
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
  $compose exec -T "$SERVICE_NAME" pytest tests/ -v
}

main() {
  banner

  case "${1:-start}" in
    start)
      check_docker
      ensure_env
      ensure_db_file
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
