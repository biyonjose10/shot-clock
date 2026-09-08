#!/bin/sh
# Deploy to Cloud Run, with every path that can spend money bounded.
#
# min-instances stays at 0 on purpose: an always-warm instance bills
# continuously through the judging period to save a few seconds of cold start,
# which is not a trade worth making.
#
# max-instances is the one that matters. Cloud Run bills CPU while a request is
# active, and the SSE stream keeps one active for the whole replay, so the
# instance ceiling is also the ceiling on how fast this can spend. It doubles as
# the bound on the live tech check: that counter lives in a file on the
# container filesystem, which is per-instance and resets on every cold start,
# so its "12 a day" is really 12 per instance lifetime. Few instances, small
# multiplier.
set -e
: "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"

# The CLI is installed locally on the dev machine and is not on PATH, which is
# what ./gcloud.sh exists for. Prefer it when `gcloud` is absent so this script
# works in both places rather than failing on line 20 with "command not found".
if command -v gcloud >/dev/null 2>&1; then
  gcloud() { command gcloud "$@"; }
else
  gcloud() { "$(dirname "$0")/gcloud.sh" "$@"; }
fi
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
MAX_INSTANCES="${SHOT_CLOCK_MAX_INSTANCES:-2}"

gcloud run deploy shot-clock \
  --source . \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --region "$REGION" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances "$MAX_INSTANCES" \
  --memory 1Gi \
  --timeout 600 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT,GOOGLE_CLOUD_LOCATION=$REGION" \
  --set-secrets "GRAFANA_URL=grafana-url:latest,GRAFANA_SERVICE_ACCOUNT_TOKEN=grafana-sa-token:latest"

# Every deploy pushes a new image and nothing reclaims the old ones. The free
# Artifact Registry tier is 500MB and one image is about 250MB, so two deploys
# without pruning already bills. Keep only what the live revision runs.
LIVE=$(gcloud run services describe shot-clock \
  --region "$REGION" --project "$GOOGLE_CLOUD_PROJECT" \
  --format="value(spec.template.spec.containers[0].image)")
REPO="$REGION-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/cloud-run-source-deploy/shot-clock"
echo "pruning images superseded by ${LIVE##*@}"
gcloud artifacts docker images list "$REPO" --format="value(version)" | while read -r digest; do
  case "$LIVE" in
    *"$digest") continue ;;
  esac
  gcloud artifacts docker images delete "$REPO@$digest" --delete-tags --quiet || true
done
