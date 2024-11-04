# s3_handler.py
import boto3
import os
from dotenv import load_dotenv
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
import logging

# Load environment variables from .env file
load_dotenv()

# Configure logging
logger = logging.getLogger("voice-assistant-app")

# Configuration from .env file
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
BUCKET_NAME = "angelsbot-voice-assistant"

# Initialize S3 client with credentials
s3_client = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)

def fetch_file_from_s3(object_key):
    """
    Fetches a file from S3 and returns its content as a string.
    
    Parameters:
        object_key (str): The key (path) of the file in the bucket.

    Returns:
        str: The content of the file.
    """
    try:
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=object_key)
        content = response['Body'].read().decode('utf-8')
        logger.info("Successfully fetched file from S3.")
        return content
    
    except NoCredentialsError:
        logger.error("No AWS credentials found.")
        raise
    except PartialCredentialsError:
        logger.error("Incomplete AWS credentials found.")
        raise
    except s3_client.exceptions.NoSuchBucket:
        logger.error("The specified bucket does not exist.")
        raise
    except s3_client.exceptions.NoSuchKey:
        logger.error("The specified object key does not exist in the bucket.")
        raise
    except Exception as e:
        logger.error(f"An error occurred while fetching file from S3: {e}")
        raise