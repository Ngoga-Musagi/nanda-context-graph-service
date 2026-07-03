#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# deploy-netlify.sh — Deploy the dashboard to Netlify (free)
#
# The dashboard is a static React app. This script deploys it
# to Netlify and proxies API calls to your backend VM.
#
# Prerequisites:
#   - npm install -g netlify-cli
#   - netlify login
#   - Backend running on AWS/GCP (see deploy-aws.sh or deploy-gcp.sh)
#
# Usage:
#   ./scripts/deploy-netlify.sh YOUR_BACKEND_IP
#
# Example:
#   ./scripts/deploy-netlify.sh 34.123.45.67
# ──────────────────────────────────────────────────────────────
set -euo pipefail

BACKEND_IP="${1:-}"

if [ -z "$BACKEND_IP" ]; then
  echo "Usage: $0 <backend-server-ip>"
  echo ""
  echo "Example: $0 34.123.45.67"
  echo ""
  echo "The backend IP is your AWS/GCP instance running docker-compose."
  echo "Deploy the backend first with deploy-aws.sh or deploy-gcp.sh."
  exit 1
fi

DASHBOARD_DIR="$(cd "$(dirname "$0")/../dashboard" && pwd)"
cd "$DASHBOARD_DIR"

# Update netlify.toml with the real backend IP
echo ">> Configuring API proxy to $BACKEND_IP..."
sed -i.bak "s|YOUR_SERVER_IP|$BACKEND_IP|g" netlify.toml
rm -f netlify.toml.bak

# Build the dashboard
echo ">> Building dashboard..."
npm run build

# Deploy to Netlify
echo ">> Deploying to Netlify..."
if command -v netlify &>/dev/null; then
  netlify deploy --prod --dir=dist
else
  echo ""
  echo "netlify-cli not found. Install it:"
  echo "  npm install -g netlify-cli"
  echo "  netlify login"
  echo "  cd dashboard && netlify deploy --prod --dir=dist"
  echo ""
  echo "Or connect via Netlify web UI:"
  echo "  1. Go to https://app.netlify.com"
  echo "  2. Drag and drop the dashboard/dist folder"
  echo "  Done!"
fi

echo ""
echo "========================================"
echo "  Dashboard deployment complete!"
echo "========================================"
echo ""
echo "  Backend API: http://$BACKEND_IP:7201"
echo "  Netlify proxies /api/* to the backend automatically."
echo ""
echo "  To update: push to GitHub and Netlify auto-deploys,"
echo "  or re-run this script."
echo "========================================"
