#!/usr/bin/env bash
set -euo pipefail

EXPECTED_ACCOUNT="abanerje.08@gmail.com"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-gemini-enterprise-learning}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE="${SOURCELENS_CLOUD_RUN_SERVICE:-sourcelens}"
RUNTIME_SA_NAME="${SOURCELENS_RUNTIME_SA:-sourcelens-runtime}"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SQL_INSTANCE="${SOURCELENS_SQL_INSTANCE:-sourcelens-db}"
SQL_CONNECTION="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"
AUTH_REQUIRED="${AUTH_REQUIRED:-true}"
GOOGLE_OAUTH_CLIENT_ID="${GOOGLE_OAUTH_CLIENT_ID:-823305428259-avl5lht0ema1r6ofm3oi1sn89kad8to5.apps.googleusercontent.com}"

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
  sqladmin.googleapis.com \
  identitytoolkit.googleapis.com \
  --project="${PROJECT_ID}"

gcloud iam service-accounts describe "${RUNTIME_SA}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${RUNTIME_SA_NAME}" \
    --display-name="SourceLens Cloud Run runtime" \
    --project="${PROJECT_ID}"

# cloudsql.client: connect to Cloud SQL. firebaseauth.viewer: token revocation checks.
for role in roles/bigquery.jobUser roles/bigquery.dataViewer roles/storage.objectViewer roles/storage.objectCreator roles/cloudsql.client roles/firebaseauth.viewer; do
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

# The app keeps its state in Cloud SQL; it must be running and migrated before release.
SQL_STATE="$(gcloud sql instances describe "${SQL_INSTANCE}" --project="${PROJECT_ID}" --format='value(state)')"
if [[ "${SQL_STATE}" != "RUNNABLE" ]]; then
  echo "Cloud SQL instance ${SQL_INSTANCE} is ${SQL_STATE:-missing}. Run 'make db-start' first." >&2
  exit 1
fi
SOURCELENS_DB_PASSWORD="$(gcloud secrets versions access latest --secret=sourcelens-db-password --project="${PROJECT_ID}")" \
  uv run alembic upgrade head

SECRET_ARGS=()
for secret_binding in \
  "SOURCELENS_DB_PASSWORD=sourcelens-db-password" \
  "OPENAI_API_KEY=sourcelens-openai-api-key" \
  "QDRANT_URL=sourcelens-qdrant-url" \
  "QDRANT_API_KEY=sourcelens-qdrant-api-key" \
  "GALILEO_API_KEY=sourcelens-galileo-api-key"; do
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
  --network=default \
  --subnet=default \
  --vpc-egress=private-ranges-only \
  --set-env-vars="SOURCELENS_WAREHOUSE=bigquery,SOURCELENS_LIVE_AGENT=1,SOURCELENS_COMPLEX_MODEL=gpt-5.6-sol,SOURCELENS_SIMPLE_MODEL=gpt-5.6-sol,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},SOURCELENS_BIGQUERY_DATASET=sourcelens_demo,SOURCELENS_GCS_BUCKET=${PROJECT_ID}-sourcelens-raw,AUTH_REQUIRED=${AUTH_REQUIRED},FIREBASE_PROJECT_ID=${PROJECT_ID},GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID},SOURCELENS_ALLOWED_EMAIL=${EXPECTED_ACCOUNT},SOURCELENS_SECURE_COOKIES=true,SOURCELENS_CLOUD_SQL_INSTANCE=${SQL_CONNECTION},SOURCELENS_DB_IP_TYPE=private,GALILEO_PROJECT=sourcelens,GALILEO_LOG_STREAM=log-stream-sourcelens" \
  "${DEPLOY_SECRETS[@]}" \
  --quiet

gcloud run services describe "${SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --format='value(status.url)'
