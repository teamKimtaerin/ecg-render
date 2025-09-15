"""
S3 Service for file upload/download operations
"""

import os
import logging
from pathlib import Path
from typing import Optional
import boto3
from botocore.exceptions import ClientError
import asyncio

logger = logging.getLogger(__name__)


class S3Service:
    """Service for S3 operations"""

    def __init__(self, bucket_name: str, region: str = "us-east-1"):
        """Initialize S3 service"""
        self.bucket_name = bucket_name
        self.region = region
        self.s3_client = boto3.client(
            's3',
            region_name=region
        )

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

    async def download_file(self, s3_key: str, output_path: Path) -> None:
        """Download file from S3"""
        try:
            # Ensure parent directory exists
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Download file
            await asyncio.get_event_loop().run_in_executor(
                None,
                self.s3_client.download_file,
                self.bucket_name,
                s3_key,
                str(output_path)
            )

            logger.info(f"File downloaded from S3: {s3_key}")

        except ClientError as e:
            logger.error(f"Failed to download file from S3: {e}")
            raise

    async def generate_presigned_url(
        self,
        s3_key: str,
        expiration: int = 3600
    ) -> str:
        """Generate a presigned URL for S3 object"""
        try:
            url = await asyncio.get_event_loop().run_in_executor(
                None,
                self.s3_client.generate_presigned_url,
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': s3_key},
                ExpiresIn=expiration
            )
            return url

        except ClientError as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            raise

    async def delete_file(self, s3_key: str) -> None:
        """Delete file from S3"""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                self.s3_client.delete_object,
                Bucket=self.bucket_name,
                Key=s3_key
            )

            logger.info(f"File deleted from S3: {s3_key}")

        except ClientError as e:
            logger.error(f"Failed to delete file from S3: {e}")
            raise

    async def file_exists(self, s3_key: str) -> bool:
        """Check if file exists in S3"""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                self.s3_client.head_object,
                Bucket=self.bucket_name,
                Key=s3_key
            )
            return True

        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            logger.error(f"Failed to check file existence: {e}")
            raise