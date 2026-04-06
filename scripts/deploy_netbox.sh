#!/usr/bin/env bash
# deploy_netbox.sh — Deploy NetBox for Bare Metal Automation
#
# Usage:
#   sudo ./scripts/deploy_netbox.sh [OPTIONS]
#
# Options:
#   --netbox-version VERSION   NetBox version to deploy (default: 4.2)
#   --domain DOMAIN            FQDN for NetBox (default: netbox.local)
#   --port PORT                HTTP port for NetBox (default: 8080)
#   --superuser USER           Admin username (default: admin)
#   --superuser-email EMAIL    Admin email (default: admin@netbox.local)
#   --data-dir DIR             Persistent data directory (default: /opt/netbox-data)
#   --skip-seed                Skip seeding BMA custom fields and device roles
#   --uninstall                Remove NetBox containers and volumes
#   -h, --help                 Show this help message
#
# Prerequisites: Docker and Docker Compose (v2) must be installed.

set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────

NETBOX_VERSION="4.2.3"
NETBOX_DOMAIN="netbox.local"
NETBOX_PORT="8080"
SUPERUSER="admin"
SUPERUSER_EMAIL="admin@netbox.local"
DATA_DIR="/opt/netbox-data"
SKIP_SEED=false
UNINSTALL=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colours
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Helpers ─────────────────────────────────────────────────────────────────

log()  { echo -e "${GREEN}[BMA]${NC} $*"; }
warn() { echo -e "${YELLOW}[BMA WARN]${NC} $*"; }
err()  { echo -e "${RED}[BMA ERROR]${NC} $*" >&2; }
banner() {
    echo -e "${CYAN}"
    echo "============================================="
    echo "  Bare Metal Automation — NetBox Deployment"
    echo "============================================="
    echo -e "${NC}"
}

usage() {
    head -24 "$0" | tail -19
    exit 0
}

check_prerequisites() {
    local missing=()

    if ! command -v docker &>/dev/null; then
        missing+=("docker")
    fi

    if ! docker compose version &>/dev/null 2>&1; then
        missing+=("docker-compose-v2")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        err "Missing prerequisites: ${missing[*]}"
        err "Install Docker: https://docs.docker.com/engine/install/"
        exit 1
    fi

    if ! docker info &>/dev/null 2>&1; then
        err "Docker daemon is not running or current user lacks permissions."
        err "Try: sudo systemctl start docker  OR  add user to docker group."
        exit 1
    fi

    log "Prerequisites OK (Docker $(docker --version | grep -oP '\d+\.\d+\.\d+'))"
}

generate_secret_key() {
    python3 -c "import secrets; print(secrets.token_urlsafe(50))" 2>/dev/null \
        || openssl rand -base64 50 | tr -d '\n' \
        || head -c 50 /dev/urandom | base64 | tr -d '\n'
}

# ── Parse arguments ────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --netbox-version) NETBOX_VERSION="$2"; shift 2 ;;
        --domain)         NETBOX_DOMAIN="$2"; shift 2 ;;
        --port)           NETBOX_PORT="$2"; shift 2 ;;
        --superuser)      SUPERUSER="$2"; shift 2 ;;
        --superuser-email) SUPERUSER_EMAIL="$2"; shift 2 ;;
        --data-dir)       DATA_DIR="$2"; shift 2 ;;
        --skip-seed)      SKIP_SEED=true; shift ;;
        --uninstall)      UNINSTALL=true; shift ;;
        -h|--help)        usage ;;
        *) err "Unknown option: $1"; usage ;;
    esac
done

COMPOSE_DIR="${DATA_DIR}/compose"
ENV_FILE="${COMPOSE_DIR}/.env"
COMPOSE_FILE="${COMPOSE_DIR}/docker-compose.yml"

# ── Uninstall ───────────────────────────────────────────────────────────────

if [[ "$UNINSTALL" == true ]]; then
    banner
    warn "This will stop and remove all NetBox containers and volumes."
    read -rp "Are you sure? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        if [[ -f "$COMPOSE_FILE" ]]; then
            docker compose -f "$COMPOSE_FILE" down -v --remove-orphans
        fi
        log "NetBox containers and volumes removed."
        log "Data directory preserved at: ${DATA_DIR}"
        log "To fully remove: rm -rf ${DATA_DIR}"
    else
        log "Uninstall cancelled."
    fi
    exit 0
fi

# ── Main deployment ────────────────────────────────────────────────────────

banner
check_prerequisites

# Prompt for superuser password
if [[ -z "${SUPERUSER_PASSWORD:-}" ]]; then
    read -rsp "Enter NetBox superuser password for '${SUPERUSER}': " SUPERUSER_PASSWORD
    echo
    if [[ -z "$SUPERUSER_PASSWORD" ]]; then
        err "Password cannot be empty."
        exit 1
    fi
fi

# Generate secrets
SECRET_KEY=$(generate_secret_key)
DB_PASSWORD=$(generate_secret_key)
REDIS_PASSWORD=$(generate_secret_key)

log "NetBox version:  ${NETBOX_VERSION}"
log "Domain:          ${NETBOX_DOMAIN}"
log "Port:            ${NETBOX_PORT}"
log "Data directory:  ${DATA_DIR}"

# ── Create directory structure ──────────────────────────────────────────────

mkdir -p "${COMPOSE_DIR}"
mkdir -p "${DATA_DIR}/postgres"
mkdir -p "${DATA_DIR}/redis"
mkdir -p "${DATA_DIR}/netbox-media"
mkdir -p "${DATA_DIR}/netbox-reports"
mkdir -p "${DATA_DIR}/netbox-scripts"

# ── Write environment file ──────────────────────────────────────────────────

log "Generating environment configuration..."

cat > "${ENV_FILE}" <<EOF
# NetBox deployment for Bare Metal Automation
# Generated on $(date -u +"%Y-%m-%dT%H:%M:%SZ")

# NetBox
NETBOX_VERSION=${NETBOX_VERSION}
SECRET_KEY=${SECRET_KEY}
ALLOWED_HOSTS="${NETBOX_DOMAIN} localhost 127.0.0.1"
CORS_ORIGIN_ALLOW_ALL=true

# Superuser
SUPERUSER_NAME=${SUPERUSER}
SUPERUSER_EMAIL=${SUPERUSER_EMAIL}
SUPERUSER_PASSWORD=${SUPERUSER_PASSWORD}
SUPERUSER_API_TOKEN=

# PostgreSQL
DB_NAME=netbox
DB_USER=netbox
DB_PASSWORD=${DB_PASSWORD}
DB_HOST=postgres
DB_PORT=5432

# Redis
REDIS_PASSWORD=${REDIS_PASSWORD}
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DATABASE=0
REDIS_CACHE_DATABASE=1
EOF

chmod 600 "${ENV_FILE}"
log "Environment file written to ${ENV_FILE}"

# ── Write Docker Compose file ──────────────────────────────────────────────

log "Generating Docker Compose configuration..."

cat > "${COMPOSE_FILE}" <<'COMPOSE_EOF'
# NetBox deployment for Bare Metal Automation
# Docs: https://netboxlabs.com/docs/netbox/en/stable/

services:
  netbox:
    image: netboxcommunity/netbox:v${NETBOX_VERSION}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      # Database
      DB_HOST: ${DB_HOST}
      DB_NAME: ${DB_NAME}
      DB_PASSWORD: ${DB_PASSWORD}
      DB_PORT: ${DB_PORT}
      DB_USER: ${DB_USER}
      # Redis
      REDIS_CACHE_DATABASE: ${REDIS_CACHE_DATABASE}
      REDIS_CACHE_HOST: ${REDIS_HOST}
      REDIS_CACHE_PASSWORD: ${REDIS_PASSWORD}
      REDIS_CACHE_PORT: ${REDIS_PORT}
      REDIS_DATABASE: ${REDIS_DATABASE}
      REDIS_HOST: ${REDIS_HOST}
      REDIS_PASSWORD: ${REDIS_PASSWORD}
      REDIS_PORT: ${REDIS_PORT}
      # NetBox
      ALLOWED_HOSTS: ${ALLOWED_HOSTS}
      CORS_ORIGIN_ALLOW_ALL: ${CORS_ORIGIN_ALLOW_ALL}
      SECRET_KEY: ${SECRET_KEY}
      # Superuser
      SUPERUSER_API_TOKEN: ${SUPERUSER_API_TOKEN}
      SUPERUSER_EMAIL: ${SUPERUSER_EMAIL}
      SUPERUSER_NAME: ${SUPERUSER_NAME}
      SUPERUSER_PASSWORD: ${SUPERUSER_PASSWORD}
      # Skip automatic startup scripts (managed by BMA)
      SKIP_SUPERUSER: "false"
    ports:
      - "${NETBOX_PORT:-8080}:8080"
    volumes:
      - netbox-media:/opt/netbox/netbox/media
      - netbox-reports:/opt/netbox/netbox/reports
      - netbox-scripts:/opt/netbox/netbox/scripts
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8080/login/ || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 10
      start_period: 120s
    restart: unless-stopped

  netbox-worker:
    image: netboxcommunity/netbox:v${NETBOX_VERSION}
    depends_on:
      netbox:
        condition: service_healthy
    command: /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py rqworker
    environment:
      DB_HOST: ${DB_HOST}
      DB_NAME: ${DB_NAME}
      DB_PASSWORD: ${DB_PASSWORD}
      DB_PORT: ${DB_PORT}
      DB_USER: ${DB_USER}
      REDIS_CACHE_DATABASE: ${REDIS_CACHE_DATABASE}
      REDIS_CACHE_HOST: ${REDIS_HOST}
      REDIS_CACHE_PASSWORD: ${REDIS_PASSWORD}
      REDIS_CACHE_PORT: ${REDIS_PORT}
      REDIS_DATABASE: ${REDIS_DATABASE}
      REDIS_HOST: ${REDIS_HOST}
      REDIS_PASSWORD: ${REDIS_PASSWORD}
      REDIS_PORT: ${REDIS_PORT}
      SECRET_KEY: ${SECRET_KEY}
    volumes:
      - netbox-media:/opt/netbox/netbox/media
      - netbox-reports:/opt/netbox/netbox/reports
      - netbox-scripts:/opt/netbox/netbox/scripts
    restart: unless-stopped

  netbox-housekeeping:
    image: netboxcommunity/netbox:v${NETBOX_VERSION}
    depends_on:
      netbox:
        condition: service_healthy
    command: /opt/netbox/housekeeping.sh
    environment:
      DB_HOST: ${DB_HOST}
      DB_NAME: ${DB_NAME}
      DB_PASSWORD: ${DB_PASSWORD}
      DB_PORT: ${DB_PORT}
      DB_USER: ${DB_USER}
      REDIS_CACHE_DATABASE: ${REDIS_CACHE_DATABASE}
      REDIS_CACHE_HOST: ${REDIS_HOST}
      REDIS_CACHE_PASSWORD: ${REDIS_PASSWORD}
      REDIS_CACHE_PORT: ${REDIS_PORT}
      REDIS_DATABASE: ${REDIS_DATABASE}
      REDIS_HOST: ${REDIS_HOST}
      REDIS_PASSWORD: ${REDIS_PASSWORD}
      REDIS_PORT: ${REDIS_PORT}
      SECRET_KEY: ${SECRET_KEY}
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: ${DB_NAME}
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER} -d ${DB_NAME}"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD} --appendonly yes
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

volumes:
  postgres-data:
  redis-data:
  netbox-media:
  netbox-reports:
  netbox-scripts:
COMPOSE_EOF

# Substitute NETBOX_PORT into the compose file (since it uses shell var in ports)
sed -i "s/\${NETBOX_PORT:-8080}/${NETBOX_PORT}/g" "${COMPOSE_FILE}"

log "Docker Compose file written to ${COMPOSE_FILE}"

# ── Pull images and start ──────────────────────────────────────────────────

log "Pulling Docker images (this may take a few minutes)..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" pull

log "Starting NetBox stack..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d

# ── Wait for NetBox to become healthy ───────────────────────────────────────

log "Waiting for NetBox to become healthy (this can take 2-3 minutes on first run)..."
MAX_WAIT=300
ELAPSED=0
while [[ $ELAPSED -lt $MAX_WAIT ]]; do
    STATUS=$(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
        ps --format json 2>/dev/null | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        svc = json.loads(line)
        if svc.get('Service') == 'netbox' and 'healthy' in svc.get('Health', ''):
            print('healthy')
            sys.exit(0)
    except (json.JSONDecodeError, KeyError):
        pass
print('waiting')
" 2>/dev/null || echo "waiting")

    if [[ "$STATUS" == "healthy" ]]; then
        break
    fi
    sleep 10
    ELAPSED=$((ELAPSED + 10))
    echo -ne "\r  Elapsed: ${ELAPSED}s / ${MAX_WAIT}s"
done
echo

if [[ $ELAPSED -ge $MAX_WAIT ]]; then
    warn "NetBox did not become healthy within ${MAX_WAIT}s."
    warn "Check logs: docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} logs netbox"
    exit 1
fi

log "NetBox is healthy!"

# ── Generate API token ──────────────────────────────────────────────────────

log "Generating API token for BMA integration..."

API_TOKEN=$(docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell -c "
from users.models import Token
from django.contrib.auth import get_user_model
User = get_user_model()
try:
    user = User.objects.get(username='${SUPERUSER}')
except User.DoesNotExist:
    print('ERROR: superuser not found')
    exit(1)
# Reuse existing token or create a new one
token, created = Token.objects.get_or_create(user=user)
print(token.key)
" 2>/dev/null)

if [[ -z "$API_TOKEN" || "$API_TOKEN" == *"ERROR"* ]]; then
    warn "Could not generate API token automatically."
    warn "Create one manually at http://${NETBOX_DOMAIN}:${NETBOX_PORT}/user/api-tokens/"
    API_TOKEN="<generate-manually>"
fi

# Update .env with the API token
python3 -c "
import re, sys
token = sys.argv[1]
path = sys.argv[2]
with open(path) as f:
    content = f.read()
content = re.sub(r'^SUPERUSER_API_TOKEN=.*', 'SUPERUSER_API_TOKEN=' + token, content, flags=re.MULTILINE)
with open(path, 'w') as f:
    f.write(content)
" "$API_TOKEN" "$ENV_FILE"

# ── Seed BMA data ──────────────────────────────────────────────────────────

if [[ "$SKIP_SEED" == false ]]; then
    log "Seeding NetBox with BMA device roles and custom fields..."

    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
        exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell -c "
from dcim.models import DeviceRole, Manufacturer, DeviceType, Platform
from extras.models import CustomField
from django.contrib.contenttypes.models import ContentType

# ── Manufacturers ──
manufacturers = {
    'Cisco':    'cisco',
    'HPE':      'hpe',
    'Meinberg': 'meinberg',
}
mfr_objs = {}
for name, slug in manufacturers.items():
    obj, created = Manufacturer.objects.get_or_create(
        slug=slug,
        defaults={'name': name},
    )
    mfr_objs[slug] = obj
    status = 'created' if created else 'exists'
    print(f'  Manufacturer: {name} ({status})')

# ── Device Roles ──
roles = {
    'core-switch':          {'color': '2196f3'},
    'distribution-switch':  {'color': '4caf50'},
    'access-switch':        {'color': '8bc34a'},
    'border-router':        {'color': 'ff9800'},
    'distribution-router':  {'color': 'ffc107'},
    'perimeter-firewall':   {'color': 'f44336'},
    'compute-node':         {'color': '9c27b0'},
    'backup-server':        {'color': '795548'},
    'management-server':    {'color': '673ab7'},
    'ntp-server':           {'color': '607d8b'},
}
for slug, attrs in roles.items():
    name = slug.replace('-', ' ').title()
    obj, created = DeviceRole.objects.get_or_create(
        slug=slug,
        defaults={'name': name, 'color': attrs['color']},
    )
    status = 'created' if created else 'exists'
    print(f'  Device Role: {name} ({status})')

# ── Platforms (maps to BMA platform identifiers) ──
platforms = {
    'cisco-ios':    {'name': 'Cisco IOS',         'manufacturer': 'cisco',    'napalm_driver': 'ios'},
    'cisco-iosxe':  {'name': 'Cisco IOS-XE',      'manufacturer': 'cisco',    'napalm_driver': 'ios'},
    'cisco-nxos':   {'name': 'Cisco NX-OS',        'manufacturer': 'cisco',    'napalm_driver': 'nxos'},
    'cisco-ftd':    {'name': 'Cisco FTD',          'manufacturer': 'cisco',    'napalm_driver': ''},
    'hpe-ilo':      {'name': 'HPE iLO',            'manufacturer': 'hpe',      'napalm_driver': ''},
    'meinberg-ntp': {'name': 'Meinberg LANTIME',   'manufacturer': 'meinberg', 'napalm_driver': ''},
}
for slug, attrs in platforms.items():
    mfr = mfr_objs.get(attrs['manufacturer'])
    defaults = {'name': attrs['name']}
    if mfr:
        defaults['manufacturer'] = mfr
    if attrs['napalm_driver']:
        defaults['napalm_driver'] = attrs['napalm_driver']
    obj, created = Platform.objects.get_or_create(slug=slug, defaults=defaults)
    status = 'created' if created else 'exists'
    print(f'  Platform: {attrs[\"name\"]} ({status})')

# ── Device Types ──
# (model_slug, full_name, manufacturer_slug, u_height, is_full_depth)
device_types = [
    # Cisco switches
    ('c9500-48y4c',   'Catalyst 9500-48Y4C',   'cisco',    1, True),
    ('c9500-24y4c',   'Catalyst 9500-24Y4C',   'cisco',    1, True),
    ('c9300-48p',     'Catalyst 9300-48P',      'cisco',    1, True),
    ('c9300-24p',     'Catalyst 9300-24P',      'cisco',    1, True),
    ('c9200-48p',     'Catalyst 9200-48P',      'cisco',    1, True),
    ('c9200-24p',     'Catalyst 9200-24P',      'cisco',    1, True),
    # Cisco routers
    ('isr4331',       'ISR 4331',               'cisco',    1, True),
    ('isr4351',       'ISR 4351',               'cisco',    2, True),
    # Cisco firewalls
    ('fp1150',        'Firepower 1150',         'cisco',    1, True),
    ('fp2110',        'Firepower 2110',         'cisco',    1, True),
    # HPE servers
    ('dl360-gen10',       'ProLiant DL360 Gen10',      'hpe', 1, True),
    ('dl360-gen10-plus',  'ProLiant DL360 Gen10 Plus', 'hpe', 1, True),
    ('dl380-gen10',       'ProLiant DL380 Gen10',      'hpe', 2, True),
    ('dl380-gen10-plus',  'ProLiant DL380 Gen10 Plus', 'hpe', 2, True),
    ('dl325-gen10',       'ProLiant DL325 Gen10',      'hpe', 1, True),
    # Meinberg NTP
    ('m300',          'LANTIME M300',           'meinberg', 1, True),
    ('m320',          'LANTIME M320',           'meinberg', 1, True),
]
for slug, full_name, mfr_slug, u_height, full_depth in device_types:
    mfr = mfr_objs.get(mfr_slug)
    if mfr is None:
        print(f'  Device Type: {full_name} SKIPPED (manufacturer not found)')
        continue
    obj, created = DeviceType.objects.get_or_create(
        slug=slug,
        manufacturer=mfr,
        defaults={
            'model': full_name,
            'u_height': u_height,
            'is_full_depth': full_depth,
        },
    )
    status = 'created' if created else 'exists'
    print(f'  Device Type: {full_name} ({status})')

# ── Custom Fields (BMA-specific) ──
device_ct = ContentType.objects.get_for_model(
    __import__('dcim.models', fromlist=['Device']).Device
)
custom_fields = {
    'bma_serial': {
        'type': 'text',
        'label': 'BMA Serial Number',
        'description': 'Factory serial number used for BMA identification',
    },
    'bma_firmware_version': {
        'type': 'text',
        'label': 'BMA Firmware Version',
        'description': 'Current firmware version tracked by BMA',
    },
    'bma_provisioning_status': {
        'type': 'text',
        'label': 'BMA Provisioning Status',
        'description': 'Last known provisioning status from BMA',
    },
    'bma_last_deployed': {
        'type': 'text',
        'label': 'BMA Last Deployed',
        'description': 'ISO-8601 timestamp of last successful BMA deployment',
    },
    'bma_platform': {
        'type': 'text',
        'label': 'BMA Platform',
        'description': 'BMA platform identifier (e.g. cisco_iosxe, hpe_ilo)',
    },
    'bma_bfs_depth': {
        'type': 'integer',
        'label': 'BMA BFS Depth',
        'description': 'BFS topology depth (0 = directly connected to management laptop)',
    },
    'bma_reset_certificate': {
        'type': 'text',
        'label': 'BMA Reset Certificate ID',
        'description': 'UUID of the most recent factory reset sanitisation certificate',
    },
}
for name, attrs in custom_fields.items():
    obj, created = CustomField.objects.get_or_create(
        name=name,
        defaults={
            'type': attrs['type'],
            'label': attrs['label'],
            'description': attrs['description'],
        },
    )
    if created:
        obj.object_types.add(device_ct)
    status = 'created' if created else 'exists'
    print(f'  Custom Field: {attrs[\"label\"]} ({status})')

print()
print('BMA seed data complete.')
" 2>/dev/null || warn "Seeding failed — you can re-run the script or seed manually."
fi

# ── Write BMA integration config ───────────────────────────────────────────

BMA_ENV_SNIPPET="${PROJECT_ROOT}/.env.netbox"
cat > "${BMA_ENV_SNIPPET}" <<EOF
# NetBox integration for Bare Metal Automation
# Source this file or add to your environment:
#   export \$(grep -v '^#' .env.netbox | xargs)
BMA_NETBOX_URL=http://localhost:${NETBOX_PORT}
BMA_NETBOX_TOKEN=${API_TOKEN}
EOF

chmod 600 "${BMA_ENV_SNIPPET}"

# ── Summary ─────────────────────────────────────────────────────────────────

echo
echo -e "${GREEN}=============================================${NC}"
echo -e "${GREEN}  NetBox deployment complete!${NC}"
echo -e "${GREEN}=============================================${NC}"
echo
echo -e "  URL:        ${CYAN}http://localhost:${NETBOX_PORT}${NC}"
echo -e "  Username:   ${CYAN}${SUPERUSER}${NC}"
echo -e "  API Token:  ${CYAN}${API_TOKEN}${NC}"
echo
echo -e "  BMA config: ${CYAN}${BMA_ENV_SNIPPET}${NC}"
echo -e "  Compose:    ${CYAN}${COMPOSE_FILE}${NC}"
echo -e "  Data:       ${CYAN}${DATA_DIR}${NC}"
echo
echo -e "  ${YELLOW}To use with BMA:${NC}"
echo -e "    export \$(grep -v '^#' .env.netbox | xargs)"
echo -e "    bare-metal-automation discover"
echo
echo -e "  ${YELLOW}Management:${NC}"
echo -e "    Logs:      docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} logs -f"
echo -e "    Stop:      docker compose -f ${COMPOSE_FILE} --env-file ${ENV_FILE} down"
echo -e "    Uninstall: $0 --uninstall"
echo
