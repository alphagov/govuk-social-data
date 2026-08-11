variable "project_id" {
  description = "The Google Cloud project ID where the alert policy will be created."
  type        = string
}

variable "notification_email_address" {
  description = "Email address that should receive alert notifications."
  type        = string
  sensitive   = true
}