import { S3Client, PutObjectCommand, GetObjectCommand } from '@aws-sdk/client-s3';
import { Upload } from '@aws-sdk/lib-storage';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { logger } from '../utils/logger';
import fs from 'fs';
import { createReadStream, createWriteStream } from 'fs';
import { pipeline } from 'stream/promises';
import path from 'path';

export class S3Service {
  private client: S3Client;
  private bucketName: string;

  constructor() {
    this.client = new S3Client({
      region: process.env.AWS_REGION || 'ap-northeast-2',
      credentials: {
        accessKeyId: process.env.AWS_ACCESS_KEY_ID || '',
        secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY || ''
      }
    });

    this.bucketName = process.env.S3_BUCKET_NAME || '';

    if (!this.bucketName) {
      logger.warn('S3_BUCKET_NAME not configured');
    }
  }

  // Parse S3 URL to get bucket and key
  private parseS3Url(s3Url: string): { bucket: string; key: string } {
    // Handle both s3:// and https:// URLs
    if (s3Url.startsWith('s3://')) {
      const parts = s3Url.slice(5).split('/');
      const bucket = parts[0];
      const key = parts.slice(1).join('/');
      return { bucket, key };
    } else if (s3Url.includes('.s3.')) {
      // Handle https://bucket.s3.region.amazonaws.com/key format
      const url = new URL(s3Url);
      const pathParts = url.pathname.slice(1).split('/');
      const bucket = url.hostname.split('.')[0];
      const key = pathParts.join('/');
      return { bucket, key };
    } else {
      throw new Error(`Invalid S3 URL format: ${s3Url}`);
    }
  }

  // Download video from S3
  async downloadVideo(s3Url: string, jobId: string): Promise<string> {
    try {
      const { bucket, key } = this.parseS3Url(s3Url);
      const localPath = path.join('/tmp', `${jobId}_source.mp4`);

      logger.info(`Downloading from S3: ${bucket}/${key}`);

      const command = new GetObjectCommand({
        Bucket: bucket,
        Key: key
      });

      const response = await this.client.send(command);

      if (!response.Body) {
        throw new Error('No data received from S3');
      }

      // Stream to local file
      const writeStream = createWriteStream(localPath);
      await pipeline(response.Body as any, writeStream);

      logger.info(`Downloaded to: ${localPath}`);
      return localPath;

    } catch (error) {
      logger.error('Failed to download from S3:', error);
      throw error;
    }
  }

  // Upload video to S3
  async uploadVideo(localPath: string, jobId: string): Promise<string> {
    try {
      const key = `renders/${jobId}/final_${Date.now()}.mp4`;
      const fileStream = createReadStream(localPath);
      const fileStats = await fs.promises.stat(localPath);

      logger.info(`Uploading to S3: ${this.bucketName}/${key}`);

      const upload = new Upload({
        client: this.client,
        params: {
          Bucket: this.bucketName,
          Key: key,
          Body: fileStream,
          ContentType: 'video/mp4',
          ContentLength: fileStats.size
        }
      });

      // Track upload progress
      upload.on('httpUploadProgress', (progress) => {
        if (progress.total) {
          const percentage = ((progress.loaded || 0) / progress.total) * 100;
          logger.debug(`Upload progress: ${percentage.toFixed(2)}%`);
        }
      });

      await upload.done();

      // Generate public URL or signed URL
      const url = await this.getFileUrl(key);

      logger.info(`Uploaded successfully: ${url}`);
      return url;

    } catch (error) {
      logger.error('Failed to upload to S3:', error);
      throw error;
    }
  }

  // Get file URL (public or presigned)
  async getFileUrl(key: string, expiresIn: number = 3600): Promise<string> {
    try {
      // Generate presigned URL for secure access
      const command = new GetObjectCommand({
        Bucket: this.bucketName,
        Key: key
      });

      const url = await getSignedUrl(this.client, command, {
        expiresIn // URL expires in 1 hour by default
      });

      return url;

    } catch (error) {
      logger.error('Failed to generate S3 URL:', error);
      // Fallback to public URL format
      return `https://${this.bucketName}.s3.${process.env.AWS_REGION}.amazonaws.com/${key}`;
    }
  }

  // Upload JSON data
  async uploadJson(data: any, key: string): Promise<string> {
    try {
      const command = new PutObjectCommand({
        Bucket: this.bucketName,
        Key: key,
        Body: JSON.stringify(data),
        ContentType: 'application/json'
      });

      await this.client.send(command);

      const url = await this.getFileUrl(key);
      logger.info(`JSON uploaded: ${url}`);
      return url;

    } catch (error) {
      logger.error('Failed to upload JSON to S3:', error);
      throw error;
    }
  }

  // Download JSON data
  async downloadJson(s3Url: string): Promise<any> {
    try {
      const { bucket, key } = this.parseS3Url(s3Url);

      const command = new GetObjectCommand({
        Bucket: bucket,
        Key: key
      });

      const response = await this.client.send(command);

      if (!response.Body) {
        throw new Error('No data received from S3');
      }

      const bodyString = await response.Body.transformToString();
      return JSON.parse(bodyString);

    } catch (error) {
      logger.error('Failed to download JSON from S3:', error);
      throw error;
    }
  }

  // Check if file exists
  async fileExists(key: string): Promise<boolean> {
    try {
      const command = new GetObjectCommand({
        Bucket: this.bucketName,
        Key: key
      });

      await this.client.send(command);
      return true;

    } catch (error: any) {
      if (error.name === 'NoSuchKey') {
        return false;
      }
      throw error;
    }
  }

  // Delete file
  async deleteFile(key: string): Promise<void> {
    try {
      const command = new GetObjectCommand({
        Bucket: this.bucketName,
        Key: key
      });

      await this.client.send(command);
      logger.info(`Deleted from S3: ${key}`);

    } catch (error) {
      logger.error('Failed to delete from S3:', error);
      throw error;
    }
  }
}