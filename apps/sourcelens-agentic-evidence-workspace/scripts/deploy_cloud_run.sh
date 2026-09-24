#!/usr/bin/env bash
set -euo pipefail

EXPECTED_ACCOUNT="abanerje.08@gmail.com"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-gemini-enterprise-learning}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE="${SOURCELENS_CLOUD_RUN_SERVICE:-sourcelens}"
RUNTIME_SA_NAME="${SOURCELENS_RUNTIME_SA:-sourcelens-runtime}"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)')"
if [[ "${ACTIVE_ACCOUNT}" != "${EXPECTED_ACCOUNT}" ]]; then
  echo "Expected ${EXPECTED_ACCOUNT}; active account is ${ACTIVE_ACCOUNT:-none}." >&2
  exit 1
fi

gcloud services enable \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  secretmanager.googleapis.com \
  --project="${PROJECT_ID}"

gcloud iam service-accounts describe "${RUNTIME_SA}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${RUNTIME_SA_NAME}" \
    --display-name="SourceLens Cloud Run runtime" \
    --project="${PROJECT_ID}"

for role in roles/bigquery.jobUser roles/bigquery.dataViewer roles/storage.objectViewer roles/storage.objectCreator; do
  for attempt in 1 2 3 4 5; do
    if gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${RUNTIME_SA}" \
      --role="${role}" \
      --condition=None \
      --quiet >/dev/null; then
      break
    fi
    if [[ "${attempt}" == "5" ]]; then
      echo "Could not grant ${role} after ${attempt} attempts." >&2
      exit 1
    fi
    sleep 10
  done
done

SECRET_ARGS=()
for secret_binding in \
  "QDRANT_URL=sourcelens-qdrant-url" \
  "QDRANT_API_KEY=sourcelens-qdrant-api-key"; do
  env_name="${secret_binding%%=*}"
  secret_name="${secret_binding#*=}"
  if gcloud secrets describe "${secret_name}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud secrets add-iam-policy-binding "${secret_name}" \
      --project="${PROJECT_ID}" \
      --member="serviceAccount:${RUNTIME_SA}" \
      --role="roles/secretmanager.secretAccessor" \
      --quiet >/dev/null
    SECRET_ARGS+=("${env_name}=${secret_name}:latest")
  fi
done

DEPLOY_SECRETS=()
if ((${#SECRET_ARGS[@]})); then
  DEPLOY_SECRETS=(--set-secrets="$(IFS=,; echo "${SECRET_ARGS[*]}")")
fi

gcloud run deploy "${SERVICE}" \
  --source=. \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --service-account="${RUNTIME_SA}" \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=1 \
  --concurrency=8 \
  --cpu=1 \
  --memory=1Gi \
  --set-env-vars="SOURCELENS_WAREHOUSE=bigquery,SOURCELENS_LIVE_AGENT=0,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},SOURCELENS_BIGQUERY_DATASET=sourcelens_demo,SOURCELENS_GCS_BUCKET=${PROJECT_ID}-sourcelens-raw" \
  "${DEPLOY_SECRETS[@]}" \
  --quiet

gcloud run services describe "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(status.url)'
