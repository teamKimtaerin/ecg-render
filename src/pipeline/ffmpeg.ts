import ffmpeg from 'fluent-ffmpeg';
import { PassThrough } from 'stream';
import { logger } from '../utils/logger';
import path from 'path';
import fs from 'fs/promises';

export interface TransparentVideoOptions {
  frames: Buffer[];
  fps: number;
  outputPath: string;
}

export interface CompositeOptions {
  sourceVideo: string;
  overlayVideo: string;
  outputPath: string;
  format: 'mp4' | 'webm';
}

export class FFmpegPipeline {
  private ffmpegPath: string;

  constructor() {
    this.ffmpegPath = process.env.FFMPEG_PATH || 'ffmpeg';
    ffmpeg.setFfmpegPath(this.ffmpegPath);
  }

  // Get video duration
  async getVideoDuration(videoPath: string): Promise<number> {
    return new Promise((resolve, reject) => {
      ffmpeg.ffprobe(videoPath, (err, metadata) => {
        if (err) {
          logger.error('Failed to get video duration:', err);
          reject(err);
        } else {
          const duration = metadata.format.duration || 0;
          resolve(duration);
        }
      });
    });
  }

  // Create transparent video from PNG frames
  async createTransparentVideo(options: TransparentVideoOptions): Promise<void> {
    const { frames, fps, outputPath } = options;

    return new Promise((resolve, reject) => {
      // Create a stream from frames
      const inputStream = new PassThrough();

      // Write frames to stream
      (async () => {
        for (const frame of frames) {
          if (!inputStream.write(frame)) {
            await new Promise(resolve => inputStream.once('drain', resolve));
          }
        }
        inputStream.end();
      })();

      // FFmpeg command
      const command = ffmpeg()
        .input(inputStream)
        .inputFormat('image2pipe')
        .inputOptions([
          '-framerate', fps.toString(),
          '-vcodec', 'png'
        ])
        .output(outputPath)
        .outputOptions([
          '-c:v', 'libvpx-vp9',        // VP9 codec for WebM
          '-pix_fmt', 'yuva420p',       // Pixel format with alpha channel
          '-b:v', '2M',                 // Bitrate
          '-auto-alt-ref', '0',         // Required for VP9 with alpha
          '-lag-in-frames', '25',       // Encoding optimization
          '-threads', '0'               // Use all available threads
        ]);

      // Add GPU acceleration if available
      if (process.env.USE_NVENC === 'true') {
        // Note: NVENC doesn't support alpha channel directly
        // We keep VP9 for transparent overlay
        logger.info('Note: Using VP9 for transparency (GPU acceleration not available for alpha channel)');
      }

      // Event handlers
      command
        .on('start', (commandLine) => {
          logger.debug('FFmpeg command:', commandLine);
        })
        .on('progress', (progress) => {
          logger.debug(`Encoding progress: ${progress.percent?.toFixed(2)}%`);
        })
        .on('end', () => {
          logger.info(`Transparent video created: ${outputPath}`);
          resolve();
        })
        .on('error', (err) => {
          logger.error('FFmpeg error:', err);
          reject(err);
        });

      // Run the command
      command.run();
    });
  }

  // Merge multiple video chunks
  async mergeChunks(chunkPaths: string[], outputPath: string): Promise<void> {
    return new Promise(async (resolve, reject) => {
      try {
        // Create concat file
        const concatFilePath = path.join('/tmp', `concat_${Date.now()}.txt`);
        const concatContent = chunkPaths.map(p => `file '${p}'`).join('\n');
        await fs.writeFile(concatFilePath, concatContent);

        // FFmpeg concat command
        const command = ffmpeg()
          .input(concatFilePath)
          .inputOptions(['-f', 'concat', '-safe', '0'])
          .output(outputPath)
          .outputOptions([
            '-c', 'copy'  // Copy codec (no re-encoding)
          ]);

        command
          .on('start', (commandLine) => {
            logger.debug('Merge command:', commandLine);
          })
          .on('end', async () => {
            // Clean up concat file
            await fs.unlink(concatFilePath);
            logger.info(`Chunks merged: ${outputPath}`);
            resolve();
          })
          .on('error', (err) => {
            logger.error('Merge error:', err);
            reject(err);
          });

        command.run();
      } catch (error) {
        reject(error);
      }
    });
  }

  // Composite overlay on source video
  async compositeVideos(options: CompositeOptions): Promise<void> {
    const { sourceVideo, overlayVideo, outputPath, format } = options;

    return new Promise((resolve, reject) => {
      const command = ffmpeg()
        .input(sourceVideo)
        .input(overlayVideo);

      // Build filter complex for overlay
      command.complexFilter([
        {
          filter: 'overlay',
          options: {
            x: 0,
            y: 0
          },
          inputs: ['0:v', '1:v'],
          outputs: 'overlaid'
        }
      ], 'overlaid');

      // Output options based on format
      if (format === 'mp4') {
        const outputOptions = [
          '-map', '[overlaid]',
          '-map', '0:a?',  // Copy audio from source if exists
          '-preset', 'fast'
        ];

        // Add GPU encoding if available
        if (process.env.USE_NVENC === 'true') {
          outputOptions.push('-c:v', 'h264_nvenc');
          outputOptions.push('-b:v', '5M');
          logger.info('Using NVENC GPU encoding for final output');
        } else {
          outputOptions.push('-c:v', 'libx264');
          outputOptions.push('-crf', '23');
        }

        outputOptions.push('-c:a', 'aac');  // Audio codec

        command.outputOptions(outputOptions);
      } else {
        // WebM output
        command.outputOptions([
          '-map', '[overlaid]',
          '-map', '0:a?',
          '-c:v', 'libvpx-vp9',
          '-b:v', '2M',
          '-c:a', 'libvorbis'
        ]);
      }

      command.output(outputPath);

      // Event handlers
      command
        .on('start', (commandLine) => {
          logger.debug('Composite command:', commandLine);
        })
        .on('progress', (progress) => {
          if (progress.percent) {
            logger.debug(`Composite progress: ${progress.percent.toFixed(2)}%`);
          }
        })
        .on('end', () => {
          logger.info(`Video composited: ${outputPath}`);
          resolve();
        })
        .on('error', (err) => {
          logger.error('Composite error:', err);
          reject(err);
        });

      command.run();
    });
  }

  // Extract audio from video
  async extractAudio(videoPath: string, audioPath: string): Promise<void> {
    return new Promise((resolve, reject) => {
      ffmpeg(videoPath)
        .output(audioPath)
        .noVideo()
        .audioCodec('aac')
        .on('end', () => {
          logger.info(`Audio extracted: ${audioPath}`);
          resolve();
        })
        .on('error', (err) => {
          logger.error('Audio extraction error:', err);
          reject(err);
        })
        .run();
    });
  }

  // Get video metadata
  async getVideoMetadata(videoPath: string): Promise<any> {
    return new Promise((resolve, reject) => {
      ffmpeg.ffprobe(videoPath, (err, metadata) => {
        if (err) {
          reject(err);
        } else {
          resolve(metadata);
        }
      });
    });
  }
}