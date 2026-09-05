#!/bin/sh
# One-time Google Cloud setup: enable the APIs and store the Grafana secrets.
# Enabling an API is free; you are billed only for usage.
set -e
: "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"

echo "==> enabling APIs (free)"
gcloud services enable \
  aiplatform.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project "$GOOGLE_CLOUD_PROJECT"

# The Grafana service account token is a credential, so it goes in Secret
# Manager rather than into --set-env-vars, where it would sit in plain text in
# the Cloud Run service description for anyone with console access.
echo "==> storing Grafana secrets"
. ./.env 2>/dev/null || true
for pair in "grafana-url:$GRAFANA_URL" "grafana-sa-token:$GRAFANA_SERVICE_ACCOUNT_TOKEN"; do
  name="${pair%%:*}"
  value="${pair#*:}"
  if [ -z "$value" ]; then echo "missing value for $name; check .env" >&2; exit 1; fi
  if gcloud secrets describe "$name" --project "$GOOGLE_CLOUD_PROJECT" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- --project "$GOOGLE_CLOUD_PROJECT"
  else
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=- --replication-policy=automatic --project "$GOOGLE_CLOUD_PROJECT"
  fi
done

# Cloud Run's runtime service account needs to read those secrets and to call
# Vertex AI. Without these two bindings the deploy succeeds and the app 403s.
PROJECT_NUMBER=$(gcloud projects describe "$GOOGLE_CLOUD_PROJECT" --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
echo "==> granting $SA access to secrets and Vertex AI"
for role in roles/secretmanager.secretAccessor roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" \
    --member "serviceAccount:$SA" --role "$role" --condition=None >/dev/null
done

echo "==> done. now run ./deploy.sh"
