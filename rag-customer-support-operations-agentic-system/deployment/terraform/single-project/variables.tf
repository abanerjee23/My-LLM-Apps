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

variable "project_name" {
  type        = string
  description = "Project name used as a base for resource naming"
  default     = "customer-chatbot-rag"
}

variable "project_id" {
  type        = string
  description = "Google Cloud Project ID for resource deployment."
}

variable "region" {
  type        = string
  description = "Google Cloud region for resource deployment."
  default     = "us-central1"
}

variable "firestore_location" {
  type        = string
  description = "Firestore location for durable support actions. This cannot be changed after creation."
  default     = "us-central1"
}

variable "dashboard_image" {
  type        = string
  description = "Versioned Artifact Registry image for the Support Operations dashboard."
}

variable "dashboard_reviewer_email" {
  type        = string
  description = "Google account allowed to invoke and act as the single reviewer in the initial MVP."
}

variable "telemetry_logs_filter" {
  type        = string
  description = "Log Sink filter for capturing telemetry data. Captures logs with the `traceloop.association.properties.log_type` attribute set to `tracing`."
  default     = "labels.service_name=\"customer-chatbot-rag\" labels.type=\"agent_telemetry\""
}

variable "app_sa_roles" {
  description = "List of roles to assign to the application service account"
  type        = list(string)
  default = [

    "roles/aiplatform.user",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
    "roles/storage.admin",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/datastore.user",
  ]
}

variable "reasoning_engine_sa_roles" {
  description = <<-EOT
    Roles for the Agent Runtime (Reasoning Engine) service agent, which executes
    the deployed agent. aiplatform.user is what lets it query the RAG corpus;
    without it retrieval silently returns nothing. See iam.tf.
  EOT
  type        = list(string)
  default = [
    "roles/aiplatform.user",
    "roles/datastore.user",
  ]
}
