resource "google_firestore_database" "support_actions" {
  project                     = var.project_id
  name                        = "(default)"
  location_id                 = var.firestore_location
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "PESSIMISTIC"
  app_engine_integration_mode = "DISABLED"

  deletion_policy = "ABANDON"

  depends_on = [google_project_service.services]
}

resource "google_service_account" "support_ops_dashboard" {
  account_id   = "customer-support-ops"
  display_name = "Customer Support Operations Dashboard"
  project      = var.project_id
}

resource "google_project_iam_member" "support_ops_dashboard_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.support_ops_dashboard.email}"
}

resource "google_artifact_registry_repository" "cloud_run_source" {
  project       = var.project_id
  location      = var.region
  repository_id = "cloud-run-source-deploy"
  format        = "DOCKER"
  description   = "Source builds for the private Support Operations dashboard"

  depends_on = [google_project_service.services]
}

resource "google_storage_bucket_iam_member" "cloud_build_source_reader" {
  bucket = "${var.project_id}_cloudbuild"
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_artifact_registry_repository_iam_member" "cloud_build_image_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.cloud_run_source.location
  repository = google_artifact_registry_repository.cloud_run_source.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

resource "google_cloud_run_v2_service" "support_ops_dashboard" {
  project             = var.project_id
  location            = var.region
  name                = "support-ops-dashboard"
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account                  = google_service_account.support_ops_dashboard.email
    timeout                          = "300s"
    max_instance_request_concurrency = 20

    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }

    containers {
      image   = var.dashboard_image
      command = [".venv/bin/uvicorn"]
      args    = ["app.dashboard_app:app", "--host", "0.0.0.0", "--port", "8080"]

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      ports {
        container_port = 8080
      }

      env {
        name  = "SUPPORT_ACTION_BACKEND"
        value = "firestore"
      }
      env {
        name  = "FIRESTORE_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "FIRESTORE_DATABASE"
        value = "(default)"
      }
      env {
        name  = "SUPPORT_ACTION_COLLECTION"
        value = "support_actions"
      }
      env {
        name  = "SUPPORT_ACTION_OVERDUE_HOURS"
        value = "24"
      }
      env {
        name  = "DASHBOARD_AUTH_MODE"
        value = "cloud_run_iam"
      }
      env {
        name  = "DASHBOARD_SERVICE_REVIEWER"
        value = var.dashboard_reviewer_email
      }
      env {
        name  = "DASHBOARD_REVIEWERS"
        value = var.dashboard_reviewer_email
      }
      env {
        name  = "DASHBOARD_ADMINS"
        value = var.dashboard_reviewer_email
      }
    }
  }

  depends_on = [google_project_service.services]
}

resource "google_cloud_run_v2_service_iam_member" "dashboard_reviewer_invoker" {
  project  = var.project_id
  location = google_cloud_run_v2_service.support_ops_dashboard.location
  name     = google_cloud_run_v2_service.support_ops_dashboard.name
  role     = "roles/run.invoker"
  member   = "user:${var.dashboard_reviewer_email}"
}
