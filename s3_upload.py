from io import BytesIO
import mimetypes
import os
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError
from dotenv import load_dotenv
import streamlit as st


S3_BUCKET = "smart-doc-assistant-saidia"
REGION = "eu-north-1"


class S3UploadError(RuntimeError):
    """A safe, user-facing S3 upload error."""


def _read_aws_credentials():
    """Read AWS credentials from Streamlit Cloud or the local environment."""
    load_dotenv()

    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    session_token = os.getenv("AWS_SESSION_TOKEN")

    try:
        aws_secrets = st.secrets.get("aws", {})
        access_key = aws_secrets.get("AWS_ACCESS_KEY_ID", access_key)
        secret_key = aws_secrets.get("AWS_SECRET_ACCESS_KEY", secret_key)
        session_token = aws_secrets.get("AWS_SESSION_TOKEN", session_token)
    except Exception:
        # Local development is allowed to rely on environment variables.
        # Do not include this exception in user-facing output because secret
        # providers can include configuration details in their messages.
        pass

    if not access_key or not secret_key:
        raise S3UploadError(
            "AWS credentials are missing. Configure the aws section in "
            "Streamlit secrets or set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
        )

    return access_key, secret_key, session_token


def _create_s3_client():
    access_key, secret_key, session_token = _read_aws_credentials()
    return boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=session_token,
        region_name=REGION,
    )


def upload_to_s3(file_data, file_name):
    """Upload in-memory file data and return the safe S3 object key."""
    if not file_data:
        raise S3UploadError("The selected file is empty.")

    # Never allow a supplied filename to create an unexpected S3 path.
    object_key = Path(file_name).name
    content_type = mimetypes.guess_type(object_key)[0] or "application/octet-stream"

    try:
        file_buffer = BytesIO(file_data)
        file_buffer.seek(0)
        _create_s3_client().upload_fileobj(
            file_buffer,
            S3_BUCKET,
            object_key,
            ExtraArgs={"ContentType": content_type},
        )
        return object_key
    except S3UploadError:
        raise
    except NoCredentialsError as exc:
        raise S3UploadError("AWS credentials were not found.") from exc
    except PartialCredentialsError as exc:
        raise S3UploadError("AWS credentials are incomplete.") from exc
    except ClientError as exc:
        error = exc.response.get("Error", {})
        code = error.get("Code", "Unknown")
        message = error.get("Message", "AWS rejected the upload.")
        raise S3UploadError(f"AWS {code}: {message}") from exc
    except Exception as exc:
        # Include only the exception type; unexpected exceptions can contain
        # sensitive configuration details in their message.
        raise S3UploadError(
            f"Unexpected upload failure ({type(exc).__name__}). Check the app logs."
        ) from exc


def upload_evidence_to_s3(file_data, file_name, case_reference):
    """Store an original evidence file under a private, case-scoped key."""
    if not file_data:
        raise S3UploadError("The selected evidence file is empty.")

    safe_name = Path(file_name).name
    object_key = (
        f"customer-cases/{case_reference}/evidence/"
        f"{uuid4().hex}-{safe_name}"
    )
    content_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"

    try:
        file_buffer = BytesIO(file_data)
        _create_s3_client().upload_fileobj(
            file_buffer,
            S3_BUCKET,
            object_key,
            ExtraArgs={
                "ContentType": content_type,
                "ServerSideEncryption": "AES256",
            },
        )
        return object_key
    except S3UploadError:
        raise
    except (NoCredentialsError, PartialCredentialsError) as exc:
        raise S3UploadError("AWS credentials are unavailable or incomplete.") from exc
    except ClientError as exc:
        error = exc.response.get("Error", {})
        raise S3UploadError(
            f"AWS {error.get('Code', 'Unknown')}: "
            f"{error.get('Message', 'AWS rejected the upload.')}"
        ) from exc
    except Exception as exc:
        raise S3UploadError(
            f"Unexpected evidence upload failure ({type(exc).__name__})."
        ) from exc
