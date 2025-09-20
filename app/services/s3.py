"""
S3 Service for file upload operations
Simplified for render server use
"""

import logging
from pathlib import Path
from typing import Optional
import boto3
from botocore.exceptions import ClientError
import asyncio
from app.core.config import settings

logger = logging.getLogger(__name__)


class S3Service:
    """Simplified S3 service for render server"""

    def __init__(self, bucket_name: Optional[str] = None):
        """Initialize S3 service"""
        self.bucket_name = bucket_name or settings.S3_BUCKET
        self.region = settings.AWS_REGION

        # Initialize S3 client
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=self.region
        )

        logger.info(f"S3 Service initialized: bucket={self.bucket_name}")

    async def upload_file(self, file_path: Path, s3_key: str) -> str:
        """Upload file to S3 and return URL"""
        try:
            # Upload file
            await asyncio.get_event_loop().run_in_executor(
                None,
                self.s3_client.upload_file,
                str(file_path),
                self.bucket_name,
                s3_key
            )

            # Generate URL
            url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{s3_key}"

            logger.info(f"File uploaded to S3: {s3_key}")
            return url

        except ClientError as e:
            logger.error(f"Failed to upload file to S3: {e}")
            raise