#!/bin/sh
# Deploy to Cloud Run. min-instances stays at 0 on purpose: an always-warm
# instance bills continuously through the judging period to save a few seconds
# of cold start, which is not a trade worth making.
set -e
: "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud run deploy shot-clock \
  --source . \
  --project "$GOOGLE_CLOUD_PROJECT" \
  --region "$REGION" \
  --allow-unauthenticated \
  --min-instances 0 \
  --memory 1Gi \
  --timeout 600 \
  --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT,GOOGLE_CLOUD_LOCATION=$REGION" \
  --set-secrets "GRAFANA_URL=grafana-url:latest,GRAFANA_SERVICE_ACCOUNT_TOKEN=grafana-sa-token:latest"
