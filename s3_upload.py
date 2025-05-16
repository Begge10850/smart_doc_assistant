import boto3
import os
from dotenv import load_dotenv

# Loading AWS credentials from the .env file
load_dotenv()

# Connect to S3
s3 = boto3.client('s3',
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
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
        print("Error uploading to S3:", e)
        return False
