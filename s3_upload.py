import boto3
from io import BytesIO
import os
from dotenv import load_dotenv
import streamlit as st

# Load AWS credentials
try:
    aws_access_key = st.secrets["aws"]["AWS_ACCESS_KEY_ID"]
    aws_secret_key = st.secrets["aws"]["AWS_SECRET_ACCESS_KEY"]
except Exception:
    load_dotenv()
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

# S3 Config
S3_BUCKET = "smart-doc-assistant-saidia"
REGION = "eu-north-1"

# Connect to S3
s3 = boto3.client(
    's3',
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key,
    region_name=REGION
)

# Upload using in-memory file data
def upload_to_s3(file_data, file_name):
    try:
        file_buffer = BytesIO(file_data)
        file_buffer.seek(0)
        s3.upload_fileobj(file_buffer, S3_BUCKET, file_name)
        print(f"✅ Uploaded {file_name} to {S3_BUCKET}")
        return True
    except Exception as e:
        print("❌ S3 Upload Error:", e)
        return False
