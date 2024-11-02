import os
import json
import sys
import yt_dlp
from minio import Minio
from minio.error import S3Error
from minio.commonconfig import Tags  # Import the Tags class
import argparse  # For command-line argument parsing
import subprocess  # To call Demucs command-line tool
import shutil  # For file operations
import torch  # To check for GPU availability
from urllib.parse import urlparse, parse_qs  # For URL parsing

# MinIO configuration variables (edit these as needed)
MINIO_ENDPOINT = 'localhost:9000'  # e.g., 'localhost:9000'
MINIO_ACCESS_KEY = 'minioadmin'
MINIO_SECRET_KEY = 'minioadmin'
MINIO_BUCKET = 'vtt-ds'
MINIO_SECURE = False  # Set to True if using HTTPS

# Define the main temporary directory within the project
TEMP_DIR = 'temp_downloads'  # You can set this to any folder name

# Define the cache directory for Hugging Face and PyTorch models
CACHE_DIR = 'models_cache'  # You can set this to any folder name or path

# Default upload folder and tags
UPLOAD_FOLDER = 'ar'  # Default value; can be overridden via command-line argument
DEFAULT_TAGS = {
    'processed': 'false',
    # 'language' will be added dynamically based on UPLOAD_FOLDER or command-line argument
}

def extract_video_id(url):
    """
    Extracts the video ID from a YouTube URL, ignoring any playlist parameters.

    Supports various YouTube URL formats including short URLs and shared URLs.

    Returns the video ID if successful; otherwise returns None.
    """
    parsed_url = urlparse(url)

    # Check if 'v' parameter is present in the query string
    query_params = parse_qs(parsed_url.query)
    if 'v' in query_params and len(query_params['v'][0]) >= 11:
        video_id = query_params['v'][0]
    else:
        # If 'v' parameter is not present, check for other URL formats
        if parsed_url.hostname in ('youtu.be'):
            # Short URL format: youtu.be/VIDEO_ID
            video_id = parsed_url.path[1:]
        elif parsed_url.path.startswith('/embed/'):
            # Embedded URL format: youtube.com/embed/VIDEO_ID
            video_id = parsed_url.path.split('/')[2]
        elif parsed_url.path.startswith('/v/'):
            # Old URL format: youtube.com/v/VIDEO_ID
            video_id = parsed_url.path.split('/')[2]
        elif parsed_url.path.startswith('/shorts/'):
            # Shorts URL format: youtube.com/shorts/VIDEO_ID
            video_id = parsed_url.path.split('/')[2]
        else:
            print("Could not extract video ID from the provided URL.")
            return None

    # Strip any additional parameters that might be appended to the video ID
    video_id = video_id.split('&')[0]
    video_id = video_id.strip()

    # Validate video ID length (YouTube video IDs are typically 11 characters)
    if video_id:
        return video_id
    else:
        print("Invalid video ID extracted.")
        return None

def get_video_info(youtube_url):
    ydl_opts = {
        'noplaylist': True,  # Ensure playlists are not downloaded
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info_dict = ydl.extract_info(youtube_url, download=False)
            return info_dict
        except yt_dlp.utils.DownloadError as e:
            print(f"Error extracting video info: {e}")
            sys.exit(1)

def check_if_video_exists(client, bucket_name, upload_folder, video_id):
    # Build the object prefix with upload_folder and video_id
    object_prefix = f"{upload_folder}/{video_id}/"
    # List objects with this prefix
    objects = client.list_objects(bucket_name, prefix=object_prefix, recursive=True)
    for obj in objects:
        # If any object exists, return True
        return True
    return False

def download_audio_and_metadata(youtube_url, video_id):
    # Ensure the temporary directory exists
    os.makedirs(TEMP_DIR, exist_ok=True)

    # Create a subdirectory inside the temp directory using the video ID
    temp_subdir = os.path.join(TEMP_DIR, 'temp_' + video_id)
    os.makedirs(temp_subdir, exist_ok=True)

    # Output template: saves files in temp_subdir with video id as base filename
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(temp_subdir, '%(id)s.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',  # Change this if you prefer a different format
            'preferredquality': '192',  # Adjust the quality as needed
        }],
        'writeinfojson': True,     # Write video metadata to a .info.json file
        'writethumbnail': False,
        'noplaylist': True,        # Ensure playlists are not downloaded
    }

    # Download audio and metadata
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(youtube_url, download=True)

    # Get file paths
    audio_filename = f"{video_id}.mp3"
    json_filename = f"{video_id}.info.json"

    audio_path = os.path.join(temp_subdir, audio_filename)
    json_path = os.path.join(temp_subdir, json_filename)

    # Load metadata from the .info.json file
    with open(json_path, 'r', encoding='utf-8') as f:
        metadata = json.load(f)

    # Select important metadata fields
    important_metadata = {
        'id': metadata.get('id'),
        'title': metadata.get('title'),
        'uploader': metadata.get('uploader'),
        'upload_date': metadata.get('upload_date'),
        'duration': metadata.get('duration'),
        'view_count': metadata.get('view_count'),
        'like_count': metadata.get('like_count'),
        'channel_id': metadata.get('channel_id'),
        'channel_url': metadata.get('channel_url'),
        'webpage_url': metadata.get('webpage_url'),  # Added this line
        'tags': metadata.get('tags'),
        'categories': metadata.get('categories'),
        # Add any other metadata fields you consider important
    }

    # Save the important metadata to a new JSON file
    important_json_filename = f"{video_id}_metadata.json"
    important_json_path = os.path.join(temp_subdir, important_json_filename)
    with open(important_json_path, 'w', encoding='utf-8') as f:
        json.dump(important_metadata, f, ensure_ascii=False, indent=4)

    return audio_path, important_json_path, temp_subdir

def process_audio_with_demucs(audio_path, output_dir, demucs_device):
    # Output will be stored in DEMUCS_OUTPUT_DIR
    DEMUCS_OUTPUT_DIR = os.path.join(output_dir, 'demucs_output')
    os.makedirs(DEMUCS_OUTPUT_DIR, exist_ok=True)

    # Set environment variable TORCH_HOME to our cache directory
    env = os.environ.copy()
    env['TORCH_HOME'] = CACHE_DIR  # Point to the custom cache directory

    try:
        # Run Demucs and capture the output
        process = subprocess.run(
            ['demucs', '-n', 'mdx_extra_q', '-d', demucs_device, audio_path, '--out', DEMUCS_OUTPUT_DIR],
            check=True,
            env=env  # Pass the modified environment to the subprocess
        )
    except subprocess.CalledProcessError as e:
        print(f"Error during Demucs processing: {e}")
        sys.exit(1)

    # Demucs outputs to DEMUCS_OUTPUT_DIR/mdx_extra_q/{filename}/vocals.wav
    base_filename = os.path.splitext(os.path.basename(audio_path))[0]
    vocals_path = os.path.join(DEMUCS_OUTPUT_DIR, 'mdx_extra_q', base_filename, 'vocals.wav')

    if not os.path.exists(vocals_path):
        print(f"Vocals file not found at {vocals_path}")
        sys.exit(1)

    # Optionally, convert vocals to mp3 using ffmpeg
    vocals_mp3_path = os.path.join(output_dir, f"{base_filename}_vocals.mp3")
    try:
        subprocess.run(
            ['ffmpeg', '-i', vocals_path, '-codec:a', 'libmp3lame', '-b:a', '192k', vocals_mp3_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError as e:
        print(f"Error converting vocals to mp3: {e}")
        sys.exit(1)

    return vocals_mp3_path

def upload_to_minio(client, audio_paths, json_path, upload_folder, tags_dict, video_id):
    # Ensure the bucket exists
    found = client.bucket_exists(MINIO_BUCKET)
    if not found:
        client.make_bucket(MINIO_BUCKET)
        print(f"Bucket '{MINIO_BUCKET}' created.")
    else:
        print(f"Bucket '{MINIO_BUCKET}' already exists.")

    # Build the object prefix with upload_folder and video_id
    object_prefix = f"{upload_folder}/{video_id}"

    # Upload the audio files
    for audio_path in audio_paths:
        audio_object_name = f"{object_prefix}/{os.path.basename(audio_path)}"
        client.fput_object(
            MINIO_BUCKET,
            audio_object_name,
            audio_path,
        )
        print(f"Uploaded audio to {MINIO_BUCKET}/{audio_object_name}")

    # Then upload the metadata JSON file with tags
    json_object_name = f"{object_prefix}/{os.path.basename(json_path)}"

    # Convert tags_dict to Tags object
    tags_obj = Tags(for_object=True)
    tags_obj.update(tags_dict)

    client.fput_object(
        MINIO_BUCKET,
        json_object_name,
        json_path,
        tags=tags_obj  # Pass the Tags object here
    )
    print(f"Uploaded metadata to {MINIO_BUCKET}/{json_object_name} with tags {tags_dict}")

def parse_arguments():
    parser = argparse.ArgumentParser(description='Download YouTube audio and upload to MinIO.')
    parser.add_argument('youtube_url', help='YouTube video URL')
    parser.add_argument('upload_folder', nargs='?', default=UPLOAD_FOLDER, help='Upload folder in MinIO (default: ar)')
    parser.add_argument('--no-vocals', action='store_true', help='Skip vocal separation (default: False)')
    parser.add_argument('--device', choices=['auto', 'cpu', 'gpu'], default='auto', help='Device to use for processing (default: auto)')
    return parser.parse_args()

def main():
    args = parse_arguments()

    youtube_url = args.youtube_url
    upload_folder = args.upload_folder
    process_vocals = not args.no_vocals  # Default is True unless --no-vocals is provided
    device_choice = args.device  # 'auto', 'cpu', or 'gpu'

    # Extract video ID and validate URL
    video_id = extract_video_id(youtube_url)
    if not video_id:
        print("Failed to extract valid video ID from the provided URL.")
        sys.exit(1)

    # Reconstruct the canonical YouTube URL
    canonical_url = f"https://www.youtube.com/watch?v={video_id}"

    # Determine Demucs device
    if device_choice == 'gpu':
        if torch.cuda.is_available():
            demucs_device = 'cuda'
            print("Using GPU for Demucs.")
        else:
            print("GPU not available. Exiting.")
            sys.exit(1)
    elif device_choice == 'cpu':
        demucs_device = 'cpu'
        print("Using CPU for Demucs.")
    else:  # 'auto'
        if torch.cuda.is_available():
            demucs_device = 'cuda'
            print("GPU detected. Using GPU for Demucs.")
        else:
            demucs_device = 'cpu'
            print("No GPU detected. Using CPU for Demucs.")

    # Update tags with language
    tags = DEFAULT_TAGS.copy()
    tags['language'] = upload_folder

    # Get video info
    info_dict = get_video_info(canonical_url)

    # Create MinIO client
    client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE
    )

    # Check if video already exists in MinIO
    exists = check_if_video_exists(client, MINIO_BUCKET, upload_folder, video_id)
    if exists:
        print(f"Video {video_id} already exists in MinIO. Skipping download and upload.")
        sys.exit(0)

    # Proceed to download and upload
    audio_path, json_path, temp_subdir = download_audio_and_metadata(canonical_url, video_id)

    # List to hold paths of audio files to upload
    audio_paths = [audio_path]

    if process_vocals:
        print("Processing audio with Demucs to extract vocals...")
        vocals_path = process_audio_with_demucs(audio_path, temp_subdir, demucs_device)
        audio_paths.append(vocals_path)
        print(f"Demucs processing complete. Vocals file saved at {vocals_path}")

    upload_to_minio(client, audio_paths, json_path, upload_folder, tags, video_id)

    # Clean up temporary subdirectory
    shutil.rmtree(temp_subdir)
    print(f"Temporary files cleaned up from {temp_subdir}")

if __name__ == '__main__':
    main()