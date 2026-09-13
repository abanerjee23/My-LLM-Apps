# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

locals {
  project_ids = {
    default = var.project_id
  }
}


# Get the project number
data "google_project" "project" {
  project_id = var.project_id
}

# Grant Storage Object Creator role to default compute service account
resource "google_project_iam_member" "default_compute_sa_storage_object_creator" {
  project    = var.project_id
  role       = "roles/cloudbuild.builds.builder"
  member     = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
  depends_on = [resource.google_project_service.services]
}

# Agent service account
resource "google_service_account" "app_sa" {
  account_id   = "${var.project_name}-app"
  display_name = "${var.project_name} Agent Service Account"
  project      = var.project_id
  depends_on   = [resource.google_project_service.services]
}

# Grant application SA the required permissions to run the application
resource "google_project_iam_member" "app_sa_roles" {
  for_each = {
    for pair in setproduct(keys(local.project_ids), var.app_sa_roles) :
    join(",", pair) => {
      project = local.project_ids[pair[0]]
      role    = pair[1]
    }
  }

  project    = each.value.project
  role       = each.value.role
  member     = "serviceAccount:${google_service_account.app_sa.email}"
  depends_on = [resource.google_project_service.services]
}


# Grant required permissions to Vertex AI service account for Agent Runtime
resource "google_project_iam_member" "vertex_ai_sa_permissions" {
  for_each = {
    for pair in setproduct(keys(local.project_ids), var.app_sa_roles) :
    join(",", pair) => pair[1]
  }

  project = var.project_id
  role    = each.value
  member  = google_project_service_identity.vertex_sa.member
  depends_on = [resource.google_project_service.services]
}



# Grant the Agent Runtime (Reasoning Engine) service agent the roles the deployed
# agent needs at run time.
#
# This is a DIFFERENT identity to google_project_service_identity.vertex_sa above.
# That one is the general Vertex AI service agent (gcp-sa-aiplatform); the account
# that actually executes a deployed agent is the Reasoning Engine service agent
# (gcp-sa-aiplatform-re). Google creates it with roles/aiplatform.reasoningEngineServiceAgent,
# which carries NO RAG permissions -- so the deployed agent authenticated fine,
# ran fine, and every policy lookup came back empty. It was the last of four
# deploy failures, and it was granted by hand rather than declared here.
#
# There is no service-identity resource for this agent, so the member is built
# from the project number directly.
resource "google_project_iam_member" "reasoning_engine_sa_roles" {
  for_each = toset(var.reasoning_engine_sa_roles)

  project    = var.project_id
  role       = each.value
  member     = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
  depends_on = [resource.google_project_service.services]
}
