"""Shared configuration for temporary customer-evidence access."""

# Original evidence remains private in S3. Make receives this temporary link
# only long enough to copy the file to the corresponding Jira issue.
EVIDENCE_DOWNLOAD_EXPIRY_SECONDS = 60 * 60
