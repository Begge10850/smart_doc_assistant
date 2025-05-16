import boto3
import os
import streamlit as st

# Load from Streamlit secrets or .env
try:
    aws_access_key = st.secrets["aws"]["AWS_ACCESS_KEY_ID"]
    aws_secret_key = st.secrets["aws"]["AWS_SECRET_ACCESS_KEY"]
except Exception:
    from dotenv import load_dotenv
    load_dotenv()
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

# Connect to S3
s3 = boto3.client(
    "s3",
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key,
    region_name="eu-north-1"
)

# Upload file to S3
def upload_to_s3(uploaded_file):
    try:
        s3.upload_fileobj(
            uploaded_file,
            "smart-doc-assistant-saidia",
            uploaded_file.name
        )
        st.success("✅ File uploaded to S3 successfully!")
        return True
    except Exception as e:
        st.error(f"❌ S3 Upload Error:\n{e}")
        return False
