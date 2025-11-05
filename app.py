## --- Imports ---
import os
import datetime
import xml.etree.ElementTree as ET # For parsing NFO files
import json # For handling playlist filters
import threading # For background tasks
import subprocess # For running ffmpeg
import sys # To flush print statements
from flask import Flask, render_template, request, jsonify, send_from_directory, Response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
import mimetypes
import hashlib

## --- App Setup ---
basedir = os.path.abspath(os.path.dirname(__file__))
# Use DATA_DIR env var for persistent storage, default to app directory
data_dir = os.environ.get('DATA_DIR', basedir)
db_path = os.path.join(data_dir, "app.db")

# VIDEO_DIR will be our new env var for the video library (e.g., /videos)
video_dir_env = os.environ.get('VIDEO_DIR', os.path.join(basedir, "videos")) 
# Normalize the video_dir path for consistent relative path calculation
video_dir = os.path.normpath(video_dir_env)

# Ensure the video directory exists (for default)
if not os.path.exists(video_dir):
    os.makedirs(video_dir, exist_ok=True)
    print(f"Created default video directory at: {video_dir}")
print(f"Using video directory: {video_dir}")


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Global Locks and Status Dictionaries for Background Tasks ---
thumbnail_generation_lock = threading.Lock()
THUMBNAIL_STATUS = {
    "status": "idle", # idle, starting, generating, error
    "message": "",
    "progress": 0,
    "total": 0
}

SCAN_LOCK = threading.Lock()
SCAN_STATUS = {
    "status": "idle", # idle, scanning, error
    "message": "",
    "progress": 0
}

TRANSCODE_LOCK = threading.Lock()
TRANSCODE_STATUS = {
    "status": "idle", # idle, transcoding, error
    "message": "",
    "video_id": None
}

## --- Database Models ---

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)           # From NFO <title> or filename
    show_title = db.Column(db.String(200))                     # From NFO <showtitle> or folder name
    summary = db.Column(db.Text)                               # From NFO <plot>
    video_path = db.Column(db.String(1000), unique=True, nullable=False) # Full path to the video file
    thumbnail_path = db.Column(db.String(1000))                # Full path to the thumbnail file
    show_poster_path = db.Column(db.String(1000), nullable=True) # Path to the show-level poster.jpg
    subtitle_path = db.Column(db.String(1000), nullable=True)  # Full path to the .srt file
    subtitle_label = db.Column(db.String(50), nullable=True)   # e.g., "English" or "CC On"
    subtitle_lang = db.Column(db.String(10), nullable=True)    # e.g., "en"
    aired = db.Column(db.DateTime(timezone=False))             # From NFO <aired>
    uploaded_date = db.Column(db.DateTime(timezone=False))     # File mtime
    youtube_id = db.Column(db.String(100), nullable=True)      # From NFO <uniqueid>
    is_favorite = db.Column(db.Boolean, default=False)
    is_watch_later = db.Column(db.Boolean, default=False)
    last_watched = db.Column(db.DateTime(timezone=False), nullable=True)
    watched_duration = db.Column(db.Integer, default=0) # Stored in seconds

    # --- Technical Info ---
    filename = db.Column(db.String(500), nullable=True)        # The video's filename (e.g., "video.mp4")
    file_size = db.Column(db.BigInteger, nullable=True)        # Filesize in bytes
    file_format = db.Column(db.String(10), nullable=True)      # e.g., "mp4", "mkv"
    has_nfo = db.Column(db.Boolean, default=False)             # True if .nfo file exists
    is_short = db.Column(db.Boolean, default=False)            # True if height > width
    dimensions = db.Column(db.String(100), nullable=True)      # e.g., "1920x1080"
    duration = db.Column(db.Integer, default=0)                # Duration in seconds
    video_codec = db.Column(db.String(50), nullable=True)      # e.g., "h264", "hevc"
    transcoded_path = db.Column(db.String(1000), nullable=True) # Path to the optimized MP4

    def to_dict(self):
        """
        Serializes the Video object to a dictionary for the frontend API.
        """
        relative_dir = '.'
        try:
            if not isinstance(self.video_path, str):
                self.video_path = str(self.video_path)
            
            norm_video_path = os.path.normpath(self.video_path)
            norm_base_dir = os.path.normpath(video_dir)
            
            relative_dir = os.path.relpath(os.path.dirname(norm_video_path), norm_base_dir)
            relative_dir = relative_dir.replace(os.sep, '/')
        except ValueError:
            relative_dir = '.' 
        except TypeError:
            print(f"Error processing path for video ID {self.id}: {self.video_path}")
            relative_dir = '.'
            
        return {
            'id': self.id,
            'title': self.title,
            'summary': self.summary,
            'author': self.show_title or 'Unknown Show',
            'published': self.aired.isoformat() if self.aired else (self.uploaded_date.isoformat() if self.uploaded_date else datetime.datetime.now().isoformat()),
            'aired_date': self.aired.isoformat() if self.aired else None,
            'uploaded': self.uploaded_date.isoformat() if self.uploaded_date else datetime.datetime.now().isoformat(),
            'is_favorite': self.is_favorite,
            'is_read_later': self.is_watch_later,
            
            'video_url': f'/api/video/{self.id}',
            'image_url': f'/api/thumbnail/{self.id}' if self.thumbnail_path else None,
            'show_poster_url': f'/api/show_poster/{self.id}' if self.show_poster_path else None,
            'subtitle_url': f'/api/subtitle/{self.id}' if self.subtitle_path else None,
            'subtitle_label': self.subtitle_label or 'Subtitles',
            'subtitle_lang': self.subtitle_lang or 'en',
            'youtube_id': self.youtube_id,
            
            'feed_title': self.show_title or 'Local Media',
            'feed_id': self.id, 
            'link': f'/api/video/{self.id}',
            
            'relative_path': relative_dir,
            
            'last_watched': self.last_watched.isoformat() if self.last_watched else None,
            'watched_duration': self.watched_duration,

            'filename': self.filename,
            'file_size': self.file_size,
            'file_format': self.file_format.upper() if self.file_format else 'Unknown',
            'has_nfo': self.has_nfo,
            'has_thumbnail': bool(self.thumbnail_path),
            'has_subtitle': bool(self.subtitle_path),
            'is_short': self.is_short,
            'dimensions': self.dimensions,
            'duration': self.duration,
            'video_codec': self.video_codec,
            'has_transcode': bool(self.transcoded_path),
            'transcode_url': f'/api/video/{self.id}/stream_transcoded' if self.transcoded_path else None,
            'transcode_download_url': f'/api/video/{self.id}/download_transcoded' if self.transcoded_path else None
        }

# SmartPlaylist Model
class SmartPlaylist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    tags = db.Column(db.Text, default='[]')
    filters = db.Column(db.Text, default='[]')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'tags': json.loads(self.tags) if self.tags else [],
            'filters': json.loads(self.filters) if self.filters else [], 
        }

## --- Helper Functions ---

def _scan_videos_task():
    """
    Scans the VIDEO_DIR for video files in a background thread.
    Finds metadata, NFOs, subs, and thumbnails.
    Prunes videos from the DB that are no longer found on disk.
    """
    global SCAN_STATUS
    try:
        with app.app_context():
            SCAN_STATUS = {"status": "scanning", "message": "Starting library scan...", "progress": 0}
            print(f"Starting scan of: {video_dir}")
            added_count = 0
            updated_count = 0
            deleted_count = 0
            
            found_video_paths = set()
            video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm']
            image_extensions = ['.jpg', '.jpeg', '.png', '.tbn']

            # --- 1. Walk the directory ---
            for dirpath, dirnames, filenames in os.walk(video_dir, topdown=True):
                
                dirnames[:] = [d for d in dirnames if not d.startswith('.')]

                for filename in filenames:
                    
                    if filename.startswith('.'):
                        continue

                    file_ext = os.path.splitext(filename)[1].lower()
                    if file_ext not in video_extensions:
                        continue

                    # --- 2. Gather File Info & Path ---
                    video_file_path = os.path.normpath(os.path.join(dirpath, filename))
                    found_video_paths.add(video_file_path)

                    video_base_filename = os.path.splitext(filename)[0]
                    video_full_filename = filename
                    
                    try:
                        file_size_bytes = os.path.getsize(video_file_path)
                        mtime = os.path.getmtime(video_file_path)
                        uploaded_date = datetime.datetime.fromtimestamp(mtime)
                    except OSError as e:
                        print(f"  - Skipping {filename} (OS Error): {e}")
                        continue 

                    file_format_str = file_ext.replace('.', '')
                    nfo_path = os.path.normpath(os.path.join(dirpath, video_base_filename + '.nfo'))
                    has_nfo_file = os.path.exists(nfo_path)

                    # --- 3. Run ffprobe to get Technical Metadata ---
                    is_short = False
                    effective_width = 0
                    effective_height = 0
                    duration_sec = 0
                    video_codec = 'unknown'
                    try:
                        ffprobe_cmd = [
                            'ffprobe',
                            '-v', 'error',
                            '-select_streams', 'v:0',
                            '-show_entries', 'stream=width,height,duration,codec_name:stream_tags=rotate:stream_side_data=rotation',
                            '-of', 'json',
                            video_file_path
                        ]
                        result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
                        data = json.loads(result.stdout)
                        
                        if 'streams' in data and len(data['streams']) > 0:
                            stream = data['streams'][0]
                            coded_width = stream.get('width', 0)
                            coded_height = stream.get('height', 0)
                            duration_sec = int(float(stream.get('duration', '0')))
                            video_codec = stream.get('codec_name', 'unknown').upper()
                            rotation = 0
                            
                            try: rotation_str = stream.get('tags', {}).get('rotate', '0'); rotation = int(float(rotation_str))
                            except (ValueError, TypeError): rotation = 0
                            
                            if rotation == 0:
                                try: side_data = stream.get('side_data_list', [{}])[0]; rotation_str = side_data.get('rotation', '0'); rotation = int(float(rotation_str))
                                except (ValueError, TypeError, IndexError): rotation = 0
                            
                            if rotation == 0:
                                try: rotation_str = stream.get('disposition', {}).get('rotate', '0'); rotation = int(float(rotation_str))
                                except (ValueError, TypeError): rotation = 0
                            
                            effective_width = coded_width
                            effective_height = coded_height
                            if abs(rotation) == 90 or abs(rotation) == 270:
                                effective_width = coded_height
                                effective_height = coded_width
                            
                            if effective_height > effective_width:
                                is_short = True
                            
                            print(f"  - ffprobe OK: {filename} | Coded: {coded_width}x{coded_height} | Rotation: {rotation} | Effective: {effective_width}x{effective_height} | is_short: {is_short}")
                            sys.stdout.flush()
                        else:
                            print(f"  - ffprobe WARN: No streams found for {filename}.")
                            sys.stdout.flush()
                    except subprocess.CalledProcessError as e:
                        stderr_output = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "(No stderr)"
                        print(f"  - Warning: ffprobe failed for {filename}. STDERR: {stderr_output}")
                    except json.JSONDecodeError:
                        print(f"  - Warning: Could not parse ffprobe JSON for {filename}.")
                    except Exception as e:
                        print(f"  - Warning: Could not determine aspect ratio for {filename}: {e}")

                    # --- 4. Find Subtitles ---
                    srt_path = None
                    srt_label = None
                    srt_lang = None
                    found_srts = []
                    for srt_filename in filenames:
                        if not srt_filename.endswith('.srt'): continue
                        lang_code = None
                        if srt_filename.startswith(video_full_filename) and srt_filename[len(video_full_filename):].startswith('.'):
                            lang_code = srt_filename[len(video_full_filename)+1:-4]
                        elif srt_filename.startswith(video_base_filename):
                            suffix = srt_filename[len(video_base_filename):-4]
                            if suffix.startswith('.'): lang_code = suffix[1:]
                            elif suffix == "": lang_code = "en"
                        if lang_code:
                            found_srts.append({"lang": lang_code, "path": os.path.normpath(os.path.join(dirpath, srt_filename))})
                    
                    if found_srts:
                        en_track = next((t for t in found_srts if t['lang'] == 'en'), None)
                        best_track = en_track if en_track else found_srts[0]
                        srt_path = best_track['path']
                        srt_lang = best_track['lang'].split('.')[0]
                        srt_label = "English" if srt_lang == "en" else srt_lang.capitalize()

                    # --- 5. Find Thumbnail (Local first, then Generated) ---
                    thumbnail_file_path = None
                    for img_ext in image_extensions:
                        potential_thumb = os.path.normpath(os.path.join(dirpath, video_base_filename + img_ext))
                        if os.path.exists(potential_thumb):
                            thumbnail_file_path = potential_thumb
                            break
                    if not thumbnail_file_path:
                        for suffix in ['-thumb', ' thumbnail', ' folder']:
                            for img_ext in image_extensions:
                                potential_thumb = os.path.normpath(os.path.join(dirpath, video_base_filename + suffix + img_ext))
                                if os.path.exists(potential_thumb):
                                    thumbnail_file_path = potential_thumb
                                    break
                            if thumbnail_file_path: break
                    
                    if not thumbnail_file_path:
                        try:
                            generated_thumb_path = get_thumbnail_path_for_video(video_file_path)
                            if os.path.exists(generated_thumb_path):
                                thumbnail_file_path = generated_thumb_path
                        except Exception as e:
                            print(f"  - Error checking for generated thumb: {e}")

                    # --- 6. Find Show Poster (poster.jpg) ---
                    poster_path_to_save = None
                    current_search_dir = os.path.dirname(video_file_path)
                    try:
                        while True:
                            if not os.path.commonpath([video_dir, current_search_dir]) == video_dir:
                                break
                            potential_poster = os.path.join(current_search_dir, 'poster.jpg')
                            if os.path.exists(potential_poster):
                                poster_path_to_save = potential_poster
                                break
                            if os.path.samefile(current_search_dir, video_dir):
                                break
                            current_search_dir = os.path.dirname(current_search_dir)
                    except Exception as e:
                        print(f"  - Error searching for poster.jpg: {e}")

                    # --- 7. Find Existing Transcode ---
                    transcoded_file_path = None
                    try:
                        potential_transcode = get_transcoded_path_for_video(video_file_path)
                        if os.path.exists(potential_transcode):
                            transcoded_file_path = potential_transcode
                    except Exception as e:
                        print(f"  - Error checking for transcoded file: {e}")

                    # --- 8. Parse NFO ---
                    title = None
                    show_title = None
                    plot = None
                    aired_date = None
                    youtube_id = None 
                    if has_nfo_file:
                        try:
                            tree = ET.parse(nfo_path)
                            root = tree.getroot()
                            title = root.findtext('title')
                            show_title = root.findtext('showtitle')
                            plot = root.findtext('plot')
                            youtube_id = root.findtext('uniqueid')
                            aired_str = root.findtext('aired')
                            if aired_str:
                                try:
                                    date_only_str = aired_str.split(' ')[0].split('T')[0]
                                    aired_date = datetime.datetime.strptime(date_only_str, '%Y-%m-%d')
                                except (ValueError, TypeError): pass
                        except Exception as e:
                            print(f"  - Error processing {nfo_path}: {e}")
                    
                    if not title: title = video_base_filename.replace('.', ' ')
                    if not show_title:
                        relative_path_segment = os.path.relpath(os.path.dirname(video_file_path), video_dir)
                        show_title = "Unknown Show" if relative_path_segment == '.' else os.path.basename(relative_path_segment) 
                    if not aired_date: aired_date = uploaded_date 
                    if not plot: plot = ""

                    # --- 9. Database Update ---
                    try:
                        existing_video = Video.query.filter_by(video_path=video_file_path).first()
                        
                        if existing_video:
                            existing_video.title = title
                            existing_video.show_title = show_title
                            existing_video.summary = plot
                            existing_video.aired = aired_date
                            existing_video.uploaded_date = uploaded_date 
                            existing_video.youtube_id = youtube_id
                            if thumbnail_file_path:
                                existing_video.thumbnail_path = thumbnail_file_path
                            existing_video.show_poster_path = poster_path_to_save
                            existing_video.subtitle_path = srt_path
                            existing_video.subtitle_label = srt_label
                            existing_video.subtitle_lang = srt_lang
                            existing_video.filename = filename
                            existing_video.file_size = file_size_bytes
                            existing_video.file_format = file_format_str
                            existing_video.has_nfo = has_nfo_file
                            existing_video.is_short = is_short
                            existing_video.dimensions = f"{effective_width}x{effective_height}"
                            existing_video.duration = duration_sec
                            existing_video.video_codec = video_codec
                            existing_video.transcoded_path = transcoded_file_path
                            updated_count += 1
                        else:
                            new_video = Video(
                                title=title,
                                show_title=show_title,
                                summary=plot,
                                aired=aired_date,
                                uploaded_date=uploaded_date, 
                                youtube_id=youtube_id,
                                video_path=video_file_path,
                                thumbnail_path=thumbnail_file_path,
                                show_poster_path=poster_path_to_save,
                                subtitle_path=srt_path,
                                subtitle_label=srt_label,
                                subtitle_lang=srt_lang,
                                filename=filename,
                                file_size=file_size_bytes,
                                file_format=file_format_str,
                                has_nfo=has_nfo_file,
                                is_short=is_short,
                                dimensions=f"{effective_width}x{effective_height}",
                                duration=duration_sec,
                                video_codec=video_codec,
                                transcoded_path=transcoded_file_path
                            )
                            db.session.add(new_video)
                            added_count += 1
                    except Exception as e:
                        print(f"  - DB Error processing {video_file_path}: {e}")
                        db.session.rollback()

                    # --- 10. Commit in Batches ---
                    current_progress = added_count + updated_count
                    if current_progress > 0 and current_progress % 50 == 0:
                        print(f"  - Committing batch of 50 to database...")
                        SCAN_STATUS['progress'] = current_progress
                        SCAN_STATUS['message'] = f"Scanning... {current_progress} processed."
                        sys.stdout.flush()
                        db.session.commit()

            # --- 11. Final Commit ---
            if added_count > 0 or updated_count > 0:
                db.session.commit()
            print(f"Scan finished. Added: {added_count}, Updated: {updated_count} videos.")
            
            # --- 12. Pruning Logic ---
            print("Starting prune of missing videos...")
            SCAN_STATUS['message'] = "Pruning deleted videos..."
            deleted_count = 0
            try:
                all_db_videos = Video.query.with_entities(Video.id, Video.video_path, Video.thumbnail_path).all()
                db_video_map = {v.video_path: v for v in all_db_videos}
                paths_to_delete = set(db_video_map.keys()) - found_video_paths
                
                if not paths_to_delete:
                    print("Prune finished. No videos to delete.")
                else:
                    print(f"Found {len(paths_to_delete)} videos to delete...")
                    for path in paths_to_delete:
                        video_data = db_video_map[path]
                        
                        # Delete associated transcode
                        try:
                            transcoded_path = get_transcoded_path_for_video(video_data.video_path)
                            if os.path.exists(transcoded_path):
                                os.remove(transcoded_path)
                                print(f"  - Deleted transcoded file: {transcoded_path}")
                        except Exception as e:
                            print(f"  - Error deleting transcoded file {transcoded_path}: {e}")
                        
                        # Delete associated thumbnail
                        if video_data.thumbnail_path and os.path.exists(video_data.thumbnail_path):
                            try:
                                os.remove(video_data.thumbnail_path)
                                print(f"  - Deleted thumbnail: {video_data.thumbnail_path}")
                            except OSError as e:
                                print(f"  - Error deleting thumbnail {video_data.thumbnail_path}: {e}")
                        
                        # Delete video from DB
                        db.session.query(Video).filter(Video.id == video_data.id).delete()
                        print(f"  - Deleted video record: {video_data.video_path}")
                        deleted_count += 1
                        
                    if deleted_count > 0:
                        db.session.commit()
                    print(f"Prune finished. Deleted {deleted_count} videos.")

            except Exception as e:
                print(f"  - Error during prune: {e}")
                db.session.rollback()
            
            total_processed = added_count + updated_count + deleted_count
            print(f"Scan finished. Processed {total_processed} videos.")
            SCAN_STATUS = {"status": "idle", "message": "Scan complete.", "progress": 0}

    except Exception as e:
        print(f"  - Error during scan task: {e}")
        db.session.rollback()
        SCAN_STATUS = {"status": "error", "message": str(e), "progress": 0}
    finally:
        SCAN_LOCK.release()
        print("Scan lock released.")
        sys.stdout.flush()


def build_folder_tree(paths):
    """
    Converts a list of relative paths into a nested dictionary tree structure.
    """
    tree = {}
    for path in paths:
        path = path.replace('\\', '/') 
        parts = path.split('/')
        if parts == ['.'] or parts == ['']:
            continue
        current_level = tree
        for part in parts:
            if part: 
                current_level = current_level.setdefault(part, {})
    return tree

def get_thumbnail_path_for_video(video_path):
    """
    Generates a persistent thumbnail path based on a hash of the video's full file path.
    """
    hash_name = hashlib.md5(video_path.encode('utf-8')).hexdigest()
    thumb_dir = os.path.join(data_dir, 'thumbnails')
    return os.path.join(thumb_dir, f"{hash_name}.jpg")

def get_transcoded_path_for_video(video_path):
    """
    Generates a persistent path for a transcoded file.
    e.g., /data/optimized/a1b2c3d4_opt.mp4
    """
    hash_name = hashlib.md5(video_path.encode('utf-8')).hexdigest()
    transcode_dir = os.path.join(data_dir, 'optimized')
    os.makedirs(transcode_dir, exist_ok=True)
    return os.path.join(transcode_dir, f"{hash_name}_opt.mp4")

def srt_to_vtt(srt_content):
    """
    Converts SRT content (string) to WebVTT content (string).
    """
    try:
        lines = srt_content.strip().split('\n')
        vtt_lines = ["WEBVTT", ""]
        i = 0
        while i < len(lines):
            if lines[i].isdigit():
                i += 1
                if i >= len(lines): continue
            if '-->' in lines[i]:
                timestamp = lines[i].replace(',', '.')
                vtt_lines.append(timestamp)
                i += 1
            while i < len(lines) and lines[i].strip() != '':
                vtt_lines.append(lines[i])
                i += 1
            vtt_lines.append("")
            i += 1
        return "\n".join(vtt_lines)
    except Exception as e:
        print(f"Error converting SRT to VTT: {e}")
        return "WEBVTT\n\n"

def _generate_thumbnails_task():
    """
    Runs in a separate thread to generate missing thumbnails.
    """
    global THUMBNAIL_STATUS
    print("Background thumbnail generation task started...")
    sys.stdout.flush() 
    with app.app_context():
        generated_count = 0
        try:
            thumb_dir = os.path.join(data_dir, 'thumbnails')
            os.makedirs(thumb_dir, exist_ok=True)

            videos_to_process = Video.query.filter(Video.thumbnail_path == None).all()
            print(f"Found {len(videos_to_process)} videos needing thumbnails.")
            sys.stdout.flush() 

            THUMBNAIL_STATUS.update({
                "status": "generating",
                "message": f"Found {len(videos_to_process)} videos to process.",
                "progress": 0,
                "total": len(videos_to_process)
            })
            
            for i, video in enumerate(videos_to_process):
                THUMBNAIL_STATUS["progress"] = i
                try:
                    video_path = video.video_path
                    if not os.path.exists(video_path):
                        print(f"  - Skipping {video.filename} (source file not found at {video_path})")
                        sys.stdout.flush() 
                        continue

                    new_thumb_path = get_thumbnail_path_for_video(video.video_path)

                    result = subprocess.run([
                        "ffmpeg",
                        "-i", video_path,
                        "-ss", "00:00:10",
                        "-vframes", "1",
                        "-q:v", "2",
                        "-f", "image2pipe",
                        "pipe:1"
                    ], 
                    check=True, 
                    capture_output=True
                    )

                    if result.stdout:
                        with open(new_thumb_path, "wb") as f:
                            f.write(result.stdout)
                        
                        if os.path.exists(new_thumb_path):
                            video.thumbnail_path = new_thumb_path
                            db.session.add(video)
                            generated_count += 1
                            print(f"  - Generated thumbnail for: {video.filename} at {new_thumb_path}")
                            sys.stdout.flush()
                        else:
                            print(f"  - FAILED to write file for: {video.filename} (Python I/O error)")
                            sys.stdout.flush()
                    else:
                        print(f"  - FAILED to generate for: {video.filename} (ffmpeg ran but produced no output)")
                        sys.stdout.flush()

                except subprocess.CalledProcessError as e:
                    stderr_output = e.stderr.decode('utf-8', errors='ignore') 
                    print(f"  - FFmpeg error for {video.filename}: {e}")
                    print(f"  - FFmpeg STDERR: {stderr_output}") 
                    sys.stdout.flush() 
                except Exception as e:
                    print(f"  - General error processing {video.filename}: {e}")
                    sys.stdout.flush() 
                    db.session.rollback()
                
                if generated_count > 0 and generated_count % 50 == 0:
                    print(f"  - Committing batch of 50 thumbnails to database...")
                    sys.stdout.flush()
                    db.session.commit()
            
            if generated_count > 0:
                print("  - Committing final thumbnail batch to database...")
                sys.stdout.flush()
                db.session.commit()

            print(f"Thumbnail generation task finished. Generated {generated_count} new thumbnails.")
            sys.stdout.flush() 

        except Exception as e:
            print(f"Fatal error in thumbnail task: {e}")
            sys.stdout.flush() 
            db.session.rollback()
            THUMBNAIL_STATUS.update({
                "status": "error", "message": str(e), "progress": 0, "total": 0
            })
        finally:
            thumbnail_generation_lock.release()
            print("Thumbnail lock released.")
            sys.stdout.flush()
            if THUMBNAIL_STATUS["status"] != "error":
                THUMBNAIL_STATUS.update({
                    "status": "idle", "message": f"Successfully generated {generated_count} thumbnails.", "progress": 0, "total": 0
                })

def _transcode_video_task(video_id):
    """
    Runs the ffmpeg transcode process in a background thread.
    """
    global TRANSCODE_STATUS
    try:
        with app.app_context():
            video = Video.query.get(video_id)
            if not video:
                raise Exception(f"Video ID {video_id} not found.")
            
            TRANSCODE_STATUS = {
                "status": "transcoding",
                "message": f"Starting optimization for: {video.filename}",
                "video_id": video_id
            }
            
            input_path = video.video_path
            output_path = get_transcoded_path_for_video(input_path)

            if os.path.exists(output_path):
                print(f"  - Transcoded file already exists: {output_path}")
            else:
                ffmpeg_cmd = [
                    'ffmpeg',
                    '-i', input_path,
                    '-c:v', 'libx264',
                    '-preset', 'fast',
                    '-crf', '23',
                    '-vf', "scale=w='min(iw,1920)':h='min(ih,1080)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                    '-c:a', 'aac',
                    '-b:a', '128k',
                    '-movflags', '+faststart',
                    output_path
                ]
                
                print(f"  - Starting transcode: {' '.join(ffmpeg_cmd)}")
                subprocess.run(ffmpeg_cmd, check=True, capture_output=True)
            
            video.transcoded_path = output_path
            db.session.commit()
            print(f"  - Transcode complete: {output_path}")
            
            TRANSCODE_STATUS = {"status": "idle", "message": "Transcode complete.", "video_id": None}

    except subprocess.CalledProcessError as e:
        print(f"  - FFmpeg error during transcode: {e.stderr.decode('utf-8', errors='ignore')}")
        TRANSCODE_STATUS = {"status": "error", "message": "FFmpeg failed.", "video_id": video_id}
    except Exception as e:
        print(f"  - Error during transcode task: {e}")
        db.session.rollback()
        TRANSCODE_STATUS = {"status": "error", "message": str(e), "video_id": video_id}
    finally:
        TRANSCODE_LOCK.release()
        print("Transcode lock released.")
        sys.stdout.flush()

## --- Initialization Function ---
def initialize_database():
    """Creates all database tables and starts initial scan if empty."""
    with app.app_context():
        print("Initializing database...")
        db.create_all()
        video_count = Video.query.count()
        if video_count == 0:
            print("No videos found. Acquiring scan lock for initial scan...")
            if SCAN_LOCK.acquire(blocking=False):
                print("Lock acquired. Starting initial background scan...")
                SCAN_STATUS = {"status": "scanning", "message": "Starting initial scan...", "progress": 0}
                scan_thread = threading.Thread(target=_scan_videos_task)
                scan_thread.start()
            else:
                print("Scan lock is busy (another scan is already running).")
        else:
            print(f"Database already contains {video_count} videos.")
        print("Database initialization complete. Server is starting.")

initialize_database()


## --- Main Routes ---

@app.route('/')
def home():
    """Serves the main index.html template."""
    return render_template('index.html')

## --- API: Get All Data ---

@app.route('/api/data')
def get_data():
    """Returns all video data, the folder tree, and smart playlists as a JSON object."""
    videos = Video.query.order_by(Video.last_watched.desc(), Video.aired.desc()).all()
    playlists = SmartPlaylist.query.order_by(SmartPlaylist.id.asc()).all()
    
    video_dtos = [v.to_dict() for v in videos]
    playlist_dtos = [p.to_dict() for p in playlists]
    
    relative_paths = set(v['relative_path'] for v in video_dtos if v['relative_path'] != '.')
    folder_tree = build_folder_tree(relative_paths)
    
    return jsonify({
        'articles': video_dtos, 
        'folder_tree': folder_tree,
        'smartPlayLists': playlist_dtos
    })

## --- API: Playlist Management ---

@app.route('/api/playlist/create', methods=['POST'])
def create_playlist():
    data = request.get_json()
    name = data.get('name')
    if not name:
        return jsonify({"error": "Playlist name is required"}), 400
    new_playlist = SmartPlaylist(name=name.strip())
    db.session.add(new_playlist)
    db.session.commit()
    return jsonify(new_playlist.to_dict()), 201

@app.route('/api/playlist/<int:playlist_id>/delete', methods=['POST'])
def delete_playlist(playlist_id):
    playlist = SmartPlaylist.query.get_or_404(playlist_id)
    db.session.delete(playlist)
    db.session.commit()
    return jsonify({'success': True}), 200

@app.route('/api/playlist/<int:playlist_id>/filter', methods=['POST'])
def add_playlist_filter(playlist_id):
    playlist = SmartPlaylist.query.get_or_404(playlist_id)
    data = request.get_json()
    new_filter = data.get('filter')
    
    if not new_filter or not isinstance(new_filter, dict) or not new_filter.get('value'):
        return jsonify({"error": "Valid filter object required"}), 400
    
    try:
        filters_list = json.loads(playlist.filters) if playlist.filters else []
        new_value = new_filter.get('value')
        new_type = new_filter.get('type')
        found_duplicate = False

        for f in filters_list:
            if f.get('type') != new_type: continue
            is_match = False
            if isinstance(new_value, str) and isinstance(f.get('value'), str):
                is_match = new_value.lower().strip() == f.get('value').lower().strip()
            elif isinstance(new_value, list) and isinstance(f.get('value'), list):
                is_match = set(new_value) == set(f.get('value'))
            if is_match:
                found_duplicate = True
                break
        
        if found_duplicate:
            return jsonify(playlist.to_dict()), 200

        filters_list.append(new_filter)
        playlist.filters = json.dumps(filters_list)
        db.session.commit()
        return jsonify(playlist.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/playlist/<int:playlist_id>/rename', methods=['POST'])
def rename_playlist(playlist_id):
    playlist = SmartPlaylist.query.get_or_404(playlist_id)
    data = request.get_json()
    name = data.get('name')
    
    if not name or name.strip() == '':
        return jsonify({"error": "Playlist name is required"}), 400
        
    try:
        playlist.name = name.strip()
        db.session.commit()
        return jsonify(playlist.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/playlist/<int:playlist_id>/filter/remove', methods=['POST'])
def remove_playlist_filter(playlist_id):
    playlist = SmartPlaylist.query.get_or_404(playlist_id)
    data = request.get_json()
    filter_id_to_remove = data.get('filterId')
    
    if not filter_id_to_remove:
        return jsonify({"error": "Valid filterId required"}), 400
    
    try:
        filters_list = json.loads(playlist.filters) if playlist.filters else []
        new_filters_list = [f for f in filters_list if f.get('id') != filter_id_to_remove]
        playlist.filters = json.dumps(new_filters_list)
        db.session.commit()
        return jsonify(playlist.to_dict()), 200
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

## --- API: Video/Thumbnail Serving ---

@app.route('/api/video/<int:video_id>')
def stream_video(video_id):
    """Streams the original video file."""
    video = Video.query.get_or_404(video_id)
    if not os.path.exists(video.video_path):
        return jsonify({"error": "Video file not found"}), 404
    mimetype = mimetypes.guess_type(video.video_path)[0] or 'video/mp4'
    video_dir_path = os.path.dirname(video.video_path)
    video_filename = os.path.basename(video.video_path)
    return send_from_directory(video_dir_path, video_filename, as_attachment=False, mimetype=mimetype)

@app.route('/api/thumbnail/<int:video_id>')
def get_thumbnail(video_id):
    """Serves the video's generated thumbnail file."""
    video = Video.query.get_or_404(video_id)
    if not video.thumbnail_path or not os.path.exists(video.thumbnail_path):
        return jsonify({"error": "Thumbnail not found"}), 404
    thumb_dir = os.path.dirname(video.thumbnail_path)
    thumb_filename = os.path.basename(video.thumbnail_path)
    mimetype = mimetypes.guess_type(video.thumbnail_path)[0] or 'image/jpeg'
    return send_from_directory(thumb_dir, thumb_filename, as_attachment=False, mimetype=mimetype)

@app.route('/api/show_poster/<int:video_id>')
def get_show_poster(video_id):
    """Serves the video's associated show_poster.jpg file."""
    video = Video.query.get_or_404(video_id)
    if not video.show_poster_path or not os.path.exists(video.show_poster_path):
        return jsonify({"error": "Show poster not found"}), 404
    poster_dir = os.path.dirname(video.show_poster_path)
    poster_filename = os.path.basename(video.show_poster_path)
    mimetype = mimetypes.guess_type(video.show_poster_path)[0] or 'image/jpeg'
    return send_from_directory(poster_dir, poster_filename, as_attachment=False, mimetype=mimetype)

@app.route('/api/subtitle/<int:video_id>')
def get_subtitle(video_id):
    """
    Serves the subtitle file, converting it from SRT to VTT on-the-fly.
    """
    video = Video.query.get_or_404(video_id)
    if not video.subtitle_path or not os.path.exists(video.subtitle_path):
        return jsonify({"error": "Subtitle file not found"}), 404
    
    srt_content = ""
    try:
        with open(video.subtitle_path, 'r', encoding='utf-8') as f:
            srt_content = f.read()
    except UnicodeDecodeError:
        try:
            with open(video.subtitle_path, 'r', encoding='latin-1') as f:
                srt_content = f.read()
        except Exception as e:
            print(f"Failed to read subtitle file {video.subtitle_path}: {e}")
            return jsonify({"error": "Could not read subtitle file"}), 500
    except Exception as e:
        print(f"Failed to read subtitle file {video.subtitle_path}: {e}")
        return jsonify({"error": "Could not read subtitle file"}), 500

    vtt_content = srt_to_vtt(srt_content)
    response = Response(vtt_content, mimetype='text/vtt; charset=utf-8')
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


## --- API: Video Actions (Favorites/Watch Later/Progress) ---

@app.route('/api/article/<int:article_id>/favorite', methods=['POST'])
def toggle_favorite(article_id):
    video = Video.query.get_or_404(article_id)
    video.is_favorite = not video.is_favorite
    db.session.commit()
    return jsonify({'is_favorite': video.is_favorite})

@app.route('/api/article/<int:article_id>/bookmark', methods=['POST'])
def toggle_watch_later(article_id):
    video = Video.query.get_or_404(article_id)
    video.is_watch_later = not video.is_watch_later
    db.session.commit()
    return jsonify({'is_read_later': video.is_watch_later})

@app.route('/api/video/<int:video_id>/progress', methods=['POST'])
def update_video_progress(video_id):
    video = Video.query.get_or_404(video_id)
    data = request.get_json()
    
    try:
        duration_watched = int(data.get('duration_watched', 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid duration_watched format"}), 400
    
    if duration_watched >= 4:
        video.watched_duration = duration_watched 
        video.last_watched = datetime.datetime.now()
        db.session.commit()
    
    return jsonify({
        'success': True, 
        'watched_duration': video.watched_duration, 
        'last_watched': video.last_watched.isoformat() if video.last_watched else None
    })

## --- API: Background Tasks (Scan & Thumbnails) ---

@app.route('/api/scan_videos', methods=['POST'])
def scan_videos_route():
    """
    API endpoint to trigger a full video scan *in the background*.
    """
    print("API: Received scan request.")
    if not SCAN_LOCK.acquire(blocking=False):
        print("API: Scan already in progress.")
        return jsonify({"message": "Scan already in progress."}), 409
    
    try:
        print("API: Starting background video scan...")
        SCAN_STATUS = {"status": "scanning", "message": "Scan started by user.", "progress": 0}
        scan_thread = threading.Thread(target=_scan_videos_task)
        scan_thread.start()
        return jsonify({"message": "Scan started in background."}), 202
    except Exception as e:
        SCAN_LOCK.release()
        SCAN_STATUS = {"status": "error", "message": str(e), "progress": 0}
        print(f"API: Failed to start scan: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/thumbnails/generate_missing', methods=['POST'])
def generate_missing_thumbnails_route():
    """
    Triggers a background task to generate missing thumbnails.
    """
    global THUMBNAIL_STATUS
    
    if not thumbnail_generation_lock.acquire(blocking=False):
        print("API: Thumbnail generation already in progress.")
        sys.stdout.flush() 
        return jsonify({"message": "Thumbnail generation is already in progress."}), 200
    
    try:
        print("API: Starting background thumbnail generation thread...")
        sys.stdout.flush()
        
        THUMBNAIL_STATUS.update({
            "status": "starting",
            "message": "Initializing task...",
            "progress": 0,
            "total": 0
        })

        thread = threading.Thread(target=_generate_thumbnails_task)
        thread.start()
        return jsonify({"message": "Thumbnail generation started in background."}), 202
    except Exception as e:
        thumbnail_generation_lock.release()
        print(f"API: Failed to start background thumbnail task: {str(e)}")
        sys.stdout.flush()
        THUMBNAIL_STATUS.update({"status": "error", "message": str(e)})
        return jsonify({"error": f"Failed to start background task: {str(e)}"}), 500

@app.route('/api/scan/status', methods=['GET'])
def get_scan_status():
    """
    Returns the current status of the video scan task.
    """
    global SCAN_STATUS
    return jsonify(SCAN_STATUS)
    
@app.route('/api/thumbnails/status', methods=['GET'])
def get_thumbnail_status():
    """
    Returns the current status of the thumbnail generation task.
    """
    global THUMBNAIL_STATUS
    return jsonify(THUMBNAIL_STATUS)

@app.route('/api/transcode/status', methods=['GET'])
def get_transcode_status():
    """
    Returns the current status of the video transcode task.
    """
    global TRANSCODE_STATUS
    return jsonify(TRANSCODE_STATUS)

@app.route('/api/video/<int:video_id>/stream_transcoded')
def stream_transcoded_video(video_id):
    """Streams the transcoded video file."""
    video = Video.query.get_or_404(video_id)
    if not video.transcoded_path or not os.path.exists(video.transcoded_path):
        return jsonify({"error": "Transcoded file not found"}), 404
    mimetype = 'video/mp4' # Transcodes are always MP4
    video_dir_path = os.path.dirname(video.transcoded_path)
    video_filename = os.path.basename(video.transcoded_path)
    return send_from_directory(video_dir_path, video_filename, as_attachment=False, mimetype=mimetype)

@app.route('/api/video/<int:video_id>/download_transcoded')
def download_transcoded_video(video_id):
    """Downloads the transcoded video file."""
    video = Video.query.get_or_404(video_id)
    if not video.transcoded_path or not os.path.exists(video.transcoded_path):
        return jsonify({"error": "Transcoded file not found"}), 404
    
    base_filename = os.path.splitext(video.filename)[0]
    download_name = f"{base_filename}_Optimized.mp4"
    
    video_dir_path = os.path.dirname(video.transcoded_path)
    video_filename = os.path.basename(video.transcoded_path)
    return send_from_directory(video_dir_path, video_filename, as_attachment=True, download_name=download_name)

@app.route('/api/video/<int:video_id>/transcode/start', methods=['POST'])
def start_transcode_route(video_id):
    """Triggers a background task to transcode a specific video."""
    if not TRANSCODE_LOCK.acquire(blocking=False):
        return jsonify({"message": "A transcode is already in progress."}), 409
    
    try:
        print(f"API: Starting transcode for video ID {video_id}...")
        TRANSCODE_STATUS = {"status": "starting", "message": "Starting transcode...", "video_id": video_id}
        scan_thread = threading.Thread(target=_transcode_video_task, args=(video_id,))
        scan_thread.start()
        return jsonify({"message": "Transcode started in background."}), 202
    except Exception as e:
        TRANSCODE_LOCK.release()
        TRANSCODE_STATUS = {"status": "error", "message": str(e), "video_id": video_id}
        print(f"API: Failed to start transcode: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/video/<int:video_id>/transcode/delete', methods=['POST'])
def delete_transcode_route(video_id):
    """Deletes a transcoded video file."""
    video = Video.query.get_or_404(video_id)
    if not video.transcoded_path:
        return jsonify({"error": "No transcode to delete."}), 404
    
    try:
        if os.path.exists(video.transcoded_path):
            os.remove(video.transcoded_path)
            print(f"  - Deleted transcoded file: {video.transcoded_path}")
        
        video.transcoded_path = None
        db.session.commit()
        return jsonify(video.to_dict()), 200 # Return updated video
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


## --- Main Execution ---

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)