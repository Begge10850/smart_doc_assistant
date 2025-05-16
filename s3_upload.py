import boto3
import os
import streamlit as st

# Load from .env only if running locally
try:
    aws_access_key = st.secrets["aws"]["AWS_ACCESS_KEY_ID"]
    aws_secret_key = st.secrets["aws"]["AWS_SECRET_ACCESS_KEY"]
except Exception:
    from dotenv import load_dotenv
    load_dotenv()
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

# Initialize S3 client
s3 = boto3.client(
    "s3",
    aws_access_key_id=aws_access_key,
    aws_secret_access_key=aws_secret_key,
    region_name="eu-north-1"
)

# Upload function
def upload_to_s3(uploaded_file):
    try:
        s3.upload_fileobj(
            uploaded_file,
            "smart-doc-assistant-saidia",
            uploaded_file.name
        )
        return True
    except Exception as e:
        st.error(f"❌ S3 Upload Error: {str(e)}")  # shows in Streamlit UI
        print("Error uploading to S3:", e)        # logs for debugging
        return False
