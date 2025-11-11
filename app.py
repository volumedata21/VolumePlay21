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
from sqlalchemy.sql import func, or_, and_
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

# --- Check for Hardware Acceleration ---
# Read the environment variable to determine transcode mode
APP_HW_ACCEL_MODE = os.environ.get('HW_ACCEL_TYPE', 'none').lower()
if APP_HW_ACCEL_MODE == 'qsv':
    print("***********************************************************")
    print("*** [INFO] Hardware acceleration: Intel QSV ENABLED     ***")
    print("*** Make sure /dev/dri is passed to this container.   ***")
    print("***********************************************************")
else:
    print("***********************************************************")
    print("*** [INFO] Hardware acceleration: DISABLED (CPU/libx264)  ***")
    print("***********************************************************")
sys.stdout.flush()
# --- End Hardware Acceleration Check ---

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
    title = db.Column(db.String(300), nullable=False)
    show_title = db.Column(db.String(200))
    summary = db.Column(db.Text)
    video_path = db.Column(db.String(1000), unique=True, nullable=False)
    
    # --- NEW COLUMN FOR PERFORMANCE ---
    relative_path = db.Column(db.String(1000), index=True, nullable=True) 
    # --- END NEW COLUMN ---

    thumbnail_path = db.Column(db.String(1000))
    show_poster_path = db.Column(db.String(1000), nullable=True)
    custom_thumbnail_path = db.Column(db.String(1000), nullable=True)
    subtitle_path = db.Column(db.String(1000), nullable=True)
    subtitle_label = db.Column(db.String(50), nullable=True)
    subtitle_lang = db.Column(db.String(10), nullable=True)
    aired = db.Column(db.DateTime(timezone=False))
    uploaded_date = db.Column(db.DateTime(timezone=False))
    youtube_id = db.Column(db.String(100), nullable=True)
    is_favorite = db.Column(db.Boolean, default=False)
    is_watch_later = db.Column(db.Boolean, default=False)
    last_watched = db.Column(db.DateTime(timezone=False), nullable=True)
    watched_duration = db.Column(db.Integer, default=0)

    # --- Technical Info ---
    filename = db.Column(db.String(500), nullable=True)
    file_size = db.Column(db.BigInteger, nullable=True)
    file_format = db.Column(db.String(10), nullable=True)
    has_nfo = db.Column(db.Boolean, default=False)
    is_short = db.Column(db.Boolean, default=False)
    dimensions = db.Column(db.String(100), nullable=True)
    duration = db.Column(db.Integer, default=0)
    video_codec = db.Column(db.String(50), nullable=True)
    transcoded_path = db.Column(db.String(1000), nullable=True)
    video_type = db.Column(db.String(50), nullable=True) # e.g., "VR180_SBS", "VR180_TB", "VR360"

    def to_dict(self):
        """
        Serializes the Video object to a dictionary for the frontend API.
        """
        has_custom_thumb = bool(self.custom_thumbnail_path and os.path.exists(self.custom_thumbnail_path))
        has_auto_thumb = bool(self.thumbnail_path and os.path.exists(self.thumbnail_path))
        
        image_url_to_use = None
        mtime = 0
        if has_custom_thumb:
            try: mtime = os.path.getmtime(self.custom_thumbnail_path)
            except: pass
            image_url_to_use = f'/api/thumbnail/{self.id}?v={mtime}'
        elif has_auto_thumb:
            try: mtime = os.path.getmtime(self.thumbnail_path)
            except: pass
            image_url_to_use = f'/api/thumbnail/{self.id}?v={mtime}'
            
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
            'image_url': image_url_to_use,
            'show_poster_url': f'/api/show_poster/{self.id}' if self.show_poster_path else None,
            'subtitle_url': f'/api/subtitle/{self.id}' if self.subtitle_path else None,
            'subtitle_label': self.subtitle_label or 'Subtitles',
            'subtitle_lang': self.subtitle_lang or 'en',
            'youtube_id': self.youtube_id,
            
            'feed_title': self.show_title or 'Local Media',
            'feed_id': self.id, 
            'link': f'/api/video/{self.id}',
            
            # Use the new pre-calculated relative_path
            'relative_path': self.relative_path or '.',
            
            'last_watched': self.last_watched.isoformat() if self.last_watched else None,
            'watched_duration': self.watched_duration,

            'filename': self.filename,
            'file_size': self.file_size,
            'file_format': self.file_format.upper() if self.file_format else 'Unknown',
            'has_nfo': self.has_nfo,
            'has_thumbnail': bool(image_url_to_use),
            'has_subtitle': bool(self.subtitle_path),
            'has_custom_thumb': has_custom_thumb,
            'is_short': self.is_short,
            'dimensions': self.dimensions,
            'duration': self.duration,
            'video_codec': self.video_codec,
            'has_transcode': bool(self.transcoded_path),
            'transcode_url': f'/api/video/{self.id}/stream_transcoded' if self.transcoded_path else None,
            'transcode_download_url': f'/api/video/{self.id}/download_transcoded' if self.transcoded_path else None,
            'video_type': self.video_type
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

# --- Create tables *before* app context, ensuring they exist before requests. ---
with app.app_context():
    db.create_all()


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
                if 'vd21_hide' in filenames:
                    print(f"  - Skipping hidden folder: {dirpath}")
                    dirnames[:] = []
                    continue

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

                    # --- UPDATED: Calculate relative_path for DB ---
                    relative_dir = None
                    try:
                        norm_base_dir = os.path.normpath(video_dir)
                        relative_dir = os.path.relpath(os.path.dirname(video_file_path), norm_base_dir)
                        relative_dir = relative_dir.replace(os.sep, '/')
                        if relative_dir == '.':
                            relative_dir = None # Store as NULL
                    except (ValueError, TypeError) as e:
                        print(f"  - Error calculating rel_path for {video_file_path}: {e}")
                        relative_dir = None
                    # --- END UPDATE ---

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
                            '-show_entries', 'stream=width,height,duration,codec_name:stream_tags=rotate:stream_side_data=rotation:stream_disposition=rotate',
                            '-of', 'json',
                            video_file_path
                        ]
                        result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True, timeout=30)
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
                    except subprocess.TimeoutExpired:
                        print(f"  - Warning: ffprobe timed out for {filename}. Skipping file.")
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

                    # --- 8. Find Existing Custom Thumbnail ---
                    custom_thumb_file_path = None
                    try:
                        potential_custom_thumb = get_custom_thumbnail_path(video_file_path)
                        if os.path.exists(potential_custom_thumb):
                            custom_thumb_file_path = potential_custom_thumb
                    except Exception as e:
                        print(f"  - Error checking for custom thumb: {e}")

                    # --- 9. Parse NFO ---
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
                        # Use the pre-calculated relative_dir to determine show_title
                        show_title = "Unknown Show" if not relative_dir else os.path.basename(relative_dir) 
                    if not aired_date: aired_date = uploaded_date 
                    if not plot: plot = ""

                    # --- 10. Database Update ---
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
                            existing_video.custom_thumbnail_path = custom_thumb_file_path
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
                            existing_video.relative_path = relative_dir # --- SAVE NEW COLUMN ---
                            # We don't update video_type, as it's manually set
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
                                relative_path=relative_dir, # --- SAVE NEW COLUMN ---
                                thumbnail_path=thumbnail_file_path,
                                show_poster_path=poster_path_to_save,
                                custom_thumbnail_path=custom_thumb_file_path,
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
                                transcoded_path=transcoded_file_path,
                                video_type=None # Always defaults to None
                            )
                            db.session.add(new_video)
                            added_count += 1
                    except Exception as e:
                        print(f"  - DB Error processing {video_file_path}: {e}")
                        db.session.rollback()

                    # --- 11. Commit in Batches ---
                    current_progress = added_count + updated_count
                    if current_progress > 0 and current_progress % 50 == 0:
                        print(f"  - Committing batch of 50 to database...")
                        SCAN_STATUS['progress'] = current_progress
                        SCAN_STATUS['message'] = f"Scanning... {current_progress} processed."
                        sys.stdout.flush()
                        db.session.commit()

            # --- 12. Final Commit ---
            if added_count > 0 or updated_count > 0:
                db.session.commit()
            print(f"Scan finished. Added: {added_count}, Updated: {updated_count} videos.")
            
            # --- 13. Pruning Logic ---
            print("Starting prune of missing videos...")
            SCAN_STATUS['message'] = "Pruning deleted videos..."
            deleted_count = 0
            try:
                all_db_videos = Video.query.with_entities(Video.id, Video.video_path, Video.thumbnail_path, Video.custom_thumbnail_path).all()
                db_video_map = {v.video_path: v for v in all_db_videos}
                paths_to_delete = set(db_video_map.keys()) - found_video_paths
                
                if not paths_to_delete:
                    print("Prune finished. No videos to delete.")
                else:
                    print(f"Found {len(paths_to_delete)} videos to delete...")
                    for path in paths_to_delete:
                        video_data = db_video_map[path]
                        
                        try:
                            transcoded_path = get_transcoded_path_for_video(video_data.video_path)
                            if os.path.exists(transcoded_path):
                                os.remove(transcoded_path)
                                print(f"  - Deleted transcoded file: {transcoded_path}")
                        except Exception as e:
                            print(f"  - Error deleting transcoded file {transcoded_path}: {e}")
                        
                        if video_data.thumbnail_path and os.path.exists(video_data.thumbnail_path):
                            try:
                                os.remove(video_data.thumbnail_path)
                                print(f"  - Deleted thumbnail: {video_data.thumbnail_path}")
                            except OSError as e:
                                print(f"  - Error deleting thumbnail {video_data.thumbnail_path}: {e}")
                        
                        if video_data.custom_thumbnail_path and os.path.exists(video_data.custom_thumbnail_path):
                            try:
                                os.remove(video_data.custom_thumbnail_path)
                                print(f"  - Deleted custom thumbnail: {video_data.custom_thumbnail_path}")
                            except OSError as e:
                                print(f"  - Error deleting custom thumbnail {video_data.custom_thumbnail_path}: {e}")
                        
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
    tree = {}
    for path in paths:
        if not path: continue # Skip None or empty strings
        path = path.replace('\\', '/') 
        parts = path.split('/')
        if parts == ['.'] or parts == ['']: continue
        current_level = tree
        for part in parts:
            if part: 
                current_level = current_level.setdefault(part, {})
    return tree

def get_thumbnail_path_for_video(video_path):
    hash_name = hashlib.md5(video_path.encode('utf-8')).hexdigest()
    thumb_dir = os.path.join(data_dir, 'thumbnails')
    return os.path.join(thumb_dir, f"{hash_name}.jpg")

def get_custom_thumbnail_path(video_path):
    hash_name = hashlib.md5(video_path.encode('utf-8')).hexdigest()
    thumb_dir = os.path.join(data_dir, 'thumbnails')
    return os.path.join(thumb_dir, f"{hash_name}_custom.jpg")

def get_transcoded_path_for_video(video_path):
    hash_name = hashlib.md5(video_path.encode('utf-8')).hexdigest()
    transcode_dir = os.path.join(data_dir, 'optimized')
    os.makedirs(transcode_dir, exist_ok=True)
    return os.path.join(transcode_dir, f"{hash_name}_opt.mp4")

def srt_to_vtt(srt_content):
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
                    ], check=True, capture_output=True)

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
                # --- NEW: Check APP_HW_ACCEL_MODE to select encoder ---
                if APP_HW_ACCEL_MODE == 'qsv':
                    print(f"  - [HW-QSV] Using Intel QSV (h264_qsv) for: {video.filename}")
                    ffmpeg_cmd = [
                        'ffmpeg',
                        '-hwaccel', 'qsv',
                        '-hwaccel_output_format', 'qsv', # Modern FFmpeg 7 flag
                        '-i', input_path,
                        '-c:v', 'h264_qsv',
                        '-preset', 'fast',
                        # '-look_ahead', '1',  <-- REMOVED THIS UNRECOGNIZED OPTION
                        # Use the modern QSV Video Post-Processing filter 'vpp_qsv'
                        '-vf', "vpp_qsv=w='min(iw,1920)':h='min(ih,1080)'", # Modern scaler
                        '-c:a', 'aac',
                        '-b:a', '128k',
                        '-movflags', '+faststart',
                        output_path
                    ]
                else:
                    # Default to software (libx264)
                    print(f"  - [CPU] Using software (libx264) for: {video.filename}")
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
    """Checks if DB is empty and starts initial scan."""
    # This is called *after* db.create_all()
    with app.app_context():
        print("Initializing database...")
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


## --- Main Routes ---

@app.route('/')
def home():
    """Serves the main index.html template."""
    return render_template('index.html')

## --- API: Get All Data ---

# --- NEW: Metadata Endpoint ---
@app.route('/api/metadata')
def get_metadata():
    """
    Returns non-video data: smart playlists, the folder tree, and author counts.
    This is called once on application load.
    """
    playlists = SmartPlaylist.query.order_by(SmartPlaylist.id.asc()).all()
    playlist_dtos = [p.to_dict() for p in playlists]
    
    # Build folder tree from the new `relative_path` column (much faster)
    all_paths = db.session.query(Video.relative_path).distinct().all()
    folder_tree = build_folder_tree([p[0] for p in all_paths if p[0]])
    
    # --- THIS IS THE FIX ---
    # Efficiently query all author counts at once
    author_counts_query = db.session.query(
        Video.show_title, 
        func.count(Video.id)
    ).group_by(Video.show_title).all()
    
    # Convert list of tuples [('Author A', 10), (None, 5)] to a dict
    author_counts_map = {}
    for author, count in author_counts_query:
        key = author if author else "Unknown Show" # Match frontend logic
        author_counts_map[key] = count
    # --- END FIX ---

    return jsonify({
        'folder_tree': folder_tree,
        'smartPlaylists': playlist_dtos,
        'author_counts': author_counts_map # --- AND THIS FIX ---
    })

# --- NEW: Paginated Videos Endpoint ---
@app.route('/api/videos')
def get_videos():
    """
    Returns paginated video data based on all filter, sort, and view parameters.
    """
    try:
        # --- 1. Get All Parameters ---
        page = request.args.get('page', 1, type=int)
        per_page = 50
        
        # View & Filter State
        viewType = request.args.get('viewType', 'all')
        viewId = request.args.get('viewId', None)
        viewAuthor = request.args.get('viewAuthor', None)
        searchQuery = request.args.get('searchQuery', None)
        sortOrder = request.args.get('sortOrder', 'aired_newest')
        
        # Global Filters
        filterShorts = request.args.get('filterShorts', 'normal')
        filterVR = request.args.get('filterVR', 'normal')
        filterOptimized = request.args.get('filterOptimized', 'normal')

        # --- 2. Build Base Query ---
        base_query = Video.query
        
        # --- 3. Apply View Filter (viewType) ---
        if viewType == 'favorites':
            base_query = base_query.filter(Video.is_favorite == True)
        elif viewType == 'watchLater':
            base_query = base_query.filter(Video.is_watch_later == True)
        elif viewType == 'history':
            base_query = base_query.filter(Video.watched_duration >= 4)
        elif viewType == 'shorts':
            base_query = base_query.filter(Video.is_short == True)
        elif viewType == 'optimized':
            base_query = base_query.filter(Video.transcoded_path != None)
        elif viewType == 'VR180':
            base_query = base_query.filter(or_(Video.video_type == 'VR180_SBS', Video.video_type == 'VR180_TB'))
        elif viewType == 'VR360':
            base_query = base_query.filter(Video.video_type == 'VR360')
        elif viewType == 'author' and viewAuthor:
            base_query = base_query.filter(Video.show_title == viewAuthor)
        elif viewType == 'folder' and viewId:
            # Use the new relative_path column for efficient folder filtering
            base_query = base_query.filter(
                or_(
                    Video.relative_path == viewId,
                    Video.relative_path.like(viewId + '/%')
                )
            )
        # 'all' and 'smart_playlist' (handled by client) don't add filters here
            
        
        # --- 4. Apply Global Filters (Solo / Hide) ---
        # Note: These filters are NOT applied if we are already in that view
        
        # Solo Logic
        isShortsSolo = filterShorts == 'solo' and viewType != 'shorts'
        isVRSolo = filterVR == 'solo' and viewType not in ['VR180', 'VR360']
        isOptimizedSolo = filterOptimized == 'solo' and viewType != 'optimized'
        isSoloActive = isShortsSolo or isVRSolo or isOptimizedSolo
        
        if isSoloActive:
            solo_filters = []
            if isShortsSolo:
                solo_filters.append(Video.is_short == True)
            if isVRSolo:
                solo_filters.append(Video.video_type != None)
            if isOptimizedSolo:
                solo_filters.append(Video.transcoded_path != None)
            base_query = base_query.filter(or_(*solo_filters))

        # Hide Logic (only applies if Solo is not active)
        if not isSoloActive:
            if filterShorts == 'hide' and viewType != 'shorts':
                base_query = base_query.filter(Video.is_short == False)
            if filterVR == 'hide' and viewType not in ['VR180', 'VR360']:
                base_query = base_query.filter(Video.video_type == None)
            if filterOptimized == 'hide' and viewType != 'optimized':
                base_query = base_query.filter(Video.transcoded_path == None)

        # --- 5. Apply Search Query ---
        if searchQuery:
            search_term = f"%{searchQuery.lower()}%"
            base_query = base_query.filter(
                or_(
                    Video.title.ilike(search_term),
                    Video.summary.ilike(search_term),
                    Video.show_title.ilike(search_term)
                )
            )
            
        # --- 6. Apply Sort Order ---
        if viewType == 'history':
            base_query = base_query.order_by(Video.last_watched.desc().nullslast())
        else:
            if sortOrder == 'aired_oldest':
                base_query = base_query.order_by(Video.aired.asc().nullsfirst())
            elif sortOrder == 'uploaded_newest':
                base_query = base_query.order_by(Video.uploaded_date.desc().nullslast())
            elif sortOrder == 'uploaded_oldest':
                base_query = base_query.order_by(Video.uploaded_date.asc().nullsfirst())
            else: # aired_newest
                base_query = base_query.order_by(Video.aired.desc().nullslast())

        # --- 7. Paginate the Final Query ---
        pagination = base_query.paginate(page=page, per_page=per_page, error_out=False)
        
        videos_on_page = pagination.items
        video_dtos = [v.to_dict() for v in videos_on_page]

        # --- 8. Return Paginated Results ---
        return jsonify({
            'articles': video_dtos,
            'total_items': pagination.total,
            'total_pages': pagination.pages,
            'current_page': page,
            'has_next_page': pagination.has_next
        })
        
    except Exception as e:
        print(f"Error in /api/videos: {e}")
        return jsonify({"error": str(e)}), 500


# --- NEW: Unpaginated endpoint for Smart Playlists ---
@app.route('/api/videos_all')
def get_all_videos():
    """
    Returns ALL videos, unpaginated.
    Used *only* by the Smart Playlist view, which requires client-side filtering.
    """
    try:
        videos = Video.query.all()
        video_dtos = [v.to_dict() for v in videos]
        return jsonify({'articles': video_dtos})
    except Exception as e:
        print(f"Error in /api/videos_all: {e}")
        return jsonify({"error": str(e)}), 500


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
    """
    Serves the highest priority thumbnail for a video.
    Priority: Custom -> Auto-generated/Local
    """
    video = Video.query.get_or_404(video_id)
    
    path_to_serve = None
    if video.custom_thumbnail_path and os.path.exists(video.custom_thumbnail_path):
        path_to_serve = video.custom_thumbnail_path
    elif video.thumbnail_path and os.path.exists(video.thumbnail_path):
        path_to_serve = video.thumbnail_path
    
    if not path_to_serve:
        return jsonify({"error": "Thumbnail not found"}), 404
    
    thumb_dir = os.path.dirname(path_to_serve)
    thumb_filename = os.path.basename(path_to_serve)
    mimetype = mimetypes.guess_type(path_to_serve)[0] or 'image/jpeg'
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
    global SCAN_STATUS
    return jsonify(SCAN_STATUS)
    
@app.route('/api/thumbnails/status', methods=['GET'])
def get_thumbnail_status():
    global THUMBNAIL_STATUS
    return jsonify(THUMBNAIL_STATUS)

@app.route('/api/transcode/status', methods=['GET'])
def get_transcode_status():
    global TRANSCODE_STATUS
    return jsonify(TRANSCODE_STATUS)

@app.route('/api/video/<int:video_id>/stream_transcoded')
def stream_transcoded_video(video_id):
    """Streams the transcoded video file."""
    video = Video.query.get_or_404(video_id)
    if not video.transcoded_path or not os.path.exists(video.transcoded_path):
        return jsonify({"error": "Transcoded file not found"}), 404
    mimetype = 'video/mp4'
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
        return jsonify(video.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# --- API routes for Custom Thumbnails ---
@app.route('/api/video/<int:video_id>/thumbnail/create_at_time', methods=['POST'])
def create_custom_thumbnail(video_id):
    """
    Generates a new thumbnail for the video at a specific timestamp.
    This overwrites any existing custom thumbnail.
    """
    video = Video.query.get_or_404(video_id)
    data = request.get_json()
    try:
        timestamp = float(data.get('timestamp', 10.0))
    except (ValueError, TypeError):
        timestamp = 10.0
    
    try:
        input_path = video.video_path
        output_path = get_custom_thumbnail_path(input_path) # Get the persistent path
        ss_time = str(datetime.timedelta(seconds=timestamp))

        print(f"  - Generating custom thumb for {video.filename} at {ss_time}...")
        
        result = subprocess.run([
            "ffmpeg",
            "-ss", ss_time,   # <--- FAST SEEK (Before -i)
            "-i", input_path,
            "-vframes", "1",
            "-q:v", "2",
            "-f", "image2pipe",
            "pipe:1"
        ], 
        check=True, 
        capture_output=True, 
        timeout=30
        )
        
        if result.stdout:
            with open(output_path, "wb") as f:
                f.write(result.stdout)
            
            if os.path.exists(output_path):
                video.custom_thumbnail_path = output_path
                db.session.commit()
                print(f"  - Custom thumb created: {output_path}")
                return jsonify(video.to_dict()), 200
            else:
                raise Exception("Failed to write thumbnail file to disk.")
        else:
            raise Exception("FFmpeg ran but produced no image data.")

    except subprocess.TimeoutExpired:
        print(f"  - Warning: Custom thumb generation timed out for {video.filename}.")
        return jsonify({"error": "Thumbnail generation timed out"}), 500
    except subprocess.CalledProcessError as e:
        stderr_output = e.stderr.decode('utf-8', errors='ignore')
        print(f"  - FFmpeg error for custom thumb {video.filename}: {stderr_output}")
        return jsonify({"error": f"FFmpeg failed: {stderr_output}"}), 500
    except Exception as e:
        print(f"  - General error creating custom thumb: {e}")
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/video/<int:video_id>/thumbnail/delete_custom', methods=['POST'])
def delete_custom_thumbnail(video_id):
    """
    Deletes a video's custom thumbnail.
    The app will fall back to the auto-generated/local thumb.
    """
    video = Video.query.get_or_404(video_id)
    if not video.custom_thumbnail_path:
        return jsonify({"error": "No custom thumbnail to delete."}), 404
    
    try:
        if os.path.exists(video.custom_thumbnail_path):
            os.remove(video.custom_thumbnail_path)
            print(f"  - Deleted custom thumbnail: {video.custom_thumbnail_path}")
        
        video.custom_thumbnail_path = None
        db.session.commit()
        return jsonify(video.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# --- API route for manual VR tagging ---
@app.route('/api/video/<int:video_id>/set_tag', methods=['POST'])
def set_video_tag(video_id):
    """
    Manually sets the video tag. This can be 'short', 'vr180', 'vr360', or 'none'.
    This single endpoint manages both the 'is_short' boolean and 'video_type' string.
    """
    video = Video.query.get_or_404(video_id)
    data = request.get_json()
    tag = data.get('tag', 'none') # Get the new tag, default to 'none'
    
    try:
        if tag == 'short':
            video.is_short = True
            video.video_type = None
        elif tag == 'vr180':
            video.is_short = False
            video.video_type = 'VR180_SBS' # Default to Side-by-Side
        elif tag == 'vr360':
            video.is_short = False
            video.video_type = 'VR360'
        else: # 'none' or any other value
            video.is_short = False
            video.video_type = None
        
        db.session.commit()
        print(f"  - Set tag for {video.filename} to: {tag}")
        return jsonify(video.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


## --- Main Execution ---

initialize_database()
if __name__ == '__main__':
    # This block is now only for local development (e.g., `python app.py`)
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)