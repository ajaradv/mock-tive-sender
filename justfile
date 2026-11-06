# Mock Tive Sender tasks. Run `just` to list them.
#
# `.env` (gitignored) is loaded automatically and is read by the app itself:
#   INTEGRATION_API_URL  where to send (local: http://localhost:8000)
#   INTEGRATION_API_KEY  sender key from `just key` / `just neon-key` in integration-api
#   SENDER_URL           public Render URL of this app, for `just prod-health`
#   RENDER_DEPLOY_HOOK   Render deploy hook URL, for `just redeploy`

set dotenv-load
set positional-arguments

port := "8100"

default:
    @just --list --unsorted

[private]
need var:
    @[ -n "$(printenv {{var}})" ] || { echo "{{var}} is not set; add it to .env" >&2; exit 1; }

# Install dependencies into .venv
setup:
    uv sync

# Run the sender with reload on http://localhost:8100
dev: (need "INTEGRATION_API_URL") (need "INTEGRATION_API_KEY")
    uv run uvicorn sender.main:app --reload --port {{port}}

# Run the sender against a given API with a given key, without .env
dev-against url key:
    @INTEGRATION_API_URL="{{url}}" INTEGRATION_API_KEY="{{key}}" uv run uvicorn sender.main:app --reload --port {{port}}

# Lint and format check
lint:
    uv run ruff check .
    uv run ruff format --check .

# Auto-fix lint and formatting
fmt:
    uv run ruff check --fix .
    uv run ruff format .

# Run tests (no network needed)
test *args:
    uv run pytest "$@"

# Everything CI runs
check: lint test

# Build the Docker image
build:
    docker build -t paxafe-mock-tive-sender:local .

# Run the Docker image on :8100 (reaches a local API via host.docker.internal)
run: build (need "INTEGRATION_API_KEY")
    docker run --rm -p {{port}}:8000 \
      -e INTEGRATION_API_URL="${INTEGRATION_API_URL/localhost/host.docker.internal}" \
      -e INTEGRATION_API_KEY="$INTEGRATION_API_KEY" \
      paxafe-mock-tive-sender:local

# Check /healthz and /api-status (defaults to local)
health url=("http://localhost:" + port):
    @curl -fsS {{url}}/healthz; echo
    @curl -fsS {{url}}/api-status; echo

# Check the deployed sender
prod-health: (need "SENDER_URL")
    @just health "$SENDER_URL"

# Run checks and push main; Render auto-deploys the push
deploy: check
    @test -z "$(git status --porcelain)" || { echo "working tree is dirty; commit first" >&2; exit 1; }
    git push origin main

# Redeploy the current commit without pushing (uses the Render deploy hook)
redeploy: (need "RENDER_DEPLOY_HOOK")
    @curl -fsS -X POST "$RENDER_DEPLOY_HOOK" >/dev/null && echo "deploy triggered"
