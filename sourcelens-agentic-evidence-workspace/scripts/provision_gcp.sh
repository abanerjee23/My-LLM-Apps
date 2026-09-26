#!/usr/bin/env bash
set -euo pipefail

EXPECTED_ACCOUNT="abanerje.08@gmail.com"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-gemini-enterprise-learning}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
DATASET="${SOURCELENS_BIGQUERY_DATASET:-sourcelens_demo}"
BUCKET="${SOURCELENS_GCS_BUCKET:-${PROJECT_ID}-sourcelens-raw}"

ACTIVE_ACCOUNT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)')"
if [[ "${ACTIVE_ACCOUNT}" != "${EXPECTED_ACCOUNT}" ]]; then
  echo "Expected ${EXPECTED_ACCOUNT}; active account is ${ACTIVE_ACCOUNT:-none}." >&2
  exit 1
fi

gcloud services enable bigquery.googleapis.com storage.googleapis.com --project="${PROJECT_ID}"
bq --project_id="${PROJECT_ID}" show --dataset "${PROJECT_ID}:${DATASET}" >/dev/null 2>&1 || \
  bq --project_id="${PROJECT_ID}" --location="${REGION}" mk --dataset "${PROJECT_ID}:${DATASET}"
gcloud storage buckets describe "gs://${BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://${BUCKET}" --project="${PROJECT_ID}" --location="${REGION}" --uniform-bucket-level-access

echo "Project: ${PROJECT_ID}"
echo "Dataset: ${PROJECT_ID}.${DATASET}"
echo "Raw bucket: gs://${BUCKET}"
