import os
import json
import uuid
import sys
import yt_dlp
from minio import Minio
from minio.error import S3Error
from minio.commonconfig import Tags  # Import the Tags class

# MinIO configuration variables (edit these as needed)
MINIO_ENDPOINT = 'localhost:9000'  # e.g., 'localhost:9000'
MINIO_ACCESS_KEY = 'minioadmin'
MINIO_SECRET_KEY = 'minioadmin'
MINIO_BUCKET = 'vtt-ds'
MINIO_SECURE = False  # Set to True if using HTTPS

# Define the main temporary directory within the project
TEMP_DIR = 'temp_downloads'  # You can set this to any folder name

# Default upload folder and tags
UPLOAD_FOLDER = 'ar'  # Default value; can be overridden via command-line argument
DEFAULT_TAGS = {
    'processed': 'false',
    # 'language' will be added dynamically based on UPLOAD_FOLDER or command-line argument
}

def get_video_info(youtube_url):
    ydl_opts = {}
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

def upload_to_minio(client, audio_path, json_path, upload_folder, tags_dict, video_id):
    # Ensure the bucket exists
    found = client.bucket_exists(MINIO_BUCKET)
    if not found:
        client.make_bucket(MINIO_BUCKET)
        print(f"Bucket '{MINIO_BUCKET}' created.")
    else:
        print(f"Bucket '{MINIO_BUCKET}' already exists.")

    # Build the object prefix with upload_folder and video_id
    object_prefix = f"{upload_folder}/{video_id}"

    # Upload the audio file first
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

def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: python script_name.py <YouTube_URL> [upload_folder]")
        sys.exit(1)

    youtube_url = sys.argv[1]
    if len(sys.argv) == 3:
        upload_folder = sys.argv[2]
    else:
        upload_folder = UPLOAD_FOLDER  # Use the default value

    # Update tags with language
    tags = DEFAULT_TAGS.copy()
    tags['language'] = upload_folder

    # Get video info and extract video_id
    info_dict = get_video_info(youtube_url)
    video_id = info_dict.get('id')
    if not video_id:
        print("Could not extract video ID.")
        sys.exit(1)

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
    audio_path, json_path, temp_subdir = download_audio_and_metadata(youtube_url, video_id)
    upload_to_minio(client, audio_path, json_path, upload_folder, tags, video_id)

    # Clean up temporary subdirectory
    import shutil
    shutil.rmtree(temp_subdir)
    print(f"Temporary files cleaned up from {temp_subdir}")

if __name__ == '__main__':
    main()