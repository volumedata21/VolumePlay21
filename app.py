## --- Imports ---
import os
import datetime
import xml.etree.ElementTree as ET
import json
import threading
import subprocess
import sys
from flask import Flask, render_template, request, jsonify, send_from_directory, Response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func, or_, and_, select, delete
import mimetypes
import hashlib
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

## --- App Setup ---
basedir = os.path.abspath(os.path.dirname(__file__))
data_dir = os.environ.get('DATA_DIR', basedir)
db_path = os.path.join(data_dir, "app.db")
video_dir_env = os.environ.get('VIDEO_DIR', os.path.join(basedir, "videos"))
video_dir = os.path.normpath(video_dir_env)

if not os.path.exists(video_dir):
    os.makedirs(video_dir, exist_ok=True)
    print(f"Created default video directory at: {video_dir}")
print(f"Using video directory: {video_dir}")

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

## --- Hardware Acceleration Check ---
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

## --- Global Locks & Status ---
# CRITICAL: This lock protects all concurrent database writes from background threads.
DB_WRITE_LOCK = threading.Lock()

thumbnail_generation_lock = threading.Lock()
THUMBNAIL_STATUS = {"status": "idle", "message": "", "progress": 0, "total": 0}

SCAN_LOCK = threading.Lock()
SCAN_STATUS = {"status": "idle", "message": "", "progress": 0}

TRANSCODE_LOCK = threading.Lock()
TRANSCODE_STATUS = {"status": "idle", "message": "", "video_id": None}

CLEANUP_LOCK = threading.Lock()
CLEANUP_STATUS = {"status": "idle", "message": "", "progress": 0}


## --- Database Models ---
class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)
    show_title = db.Column(db.String(200), index=True)
    summary = db.Column(db.Text)
    video_path = db.Column(db.String(1000), unique=True, nullable=False)
    relative_path = db.Column(db.String(1000), index=True, nullable=True)
    thumbnail_path = db.Column(db.String(1000))
    show_poster_path = db.Column(db.String(1000), nullable=True)
    custom_thumbnail_path = db.Column(db.String(1000), nullable=True)
    subtitle_path = db.Column(db.String(1000), nullable=True)
    subtitle_label = db.Column(db.String(50), nullable=True)
    subtitle_lang = db.Column(db.String(10), nullable=True)
    aired = db.Column(db.DateTime(timezone=False), index=True)
    uploaded_date = db.Column(db.DateTime(timezone=False), index=True)
    youtube_id = db.Column(db.String(100), nullable=True)
    is_favorite = db.Column(db.Boolean, default=False, index=True)
    is_watch_later = db.Column(db.Boolean, default=False, index=True)
    last_watched = db.Column(db.DateTime(timezone=False), nullable=True, index=True)
    watched_duration = db.Column(db.Integer, default=0)

    # Technical Info
    filename = db.Column(db.String(500), nullable=True)
    file_size = db.Column(db.BigInteger, nullable=True)
    file_format = db.Column(db.String(10), nullable=True)
    has_nfo = db.Column(db.Boolean, default=False)
    is_short = db.Column(db.Boolean, default=False, index=True)
    dimensions = db.Column(db.String(100), nullable=True)
    duration = db.Column(db.Integer, default=0, index=True)
    video_codec = db.Column(db.String(50), nullable=True)
    transcoded_path = db.Column(db.String(1000), nullable=True, index=True)
    video_type = db.Column(db.String(50), nullable=True, index=True)

    # --- NEW FIELDS FOR IMAGE SUPPORT ---
    media_type = db.Column(db.String(20), default='video', index=True) # 'video' or 'image'
    is_associated_thumbnail = db.Column(db.Boolean, default=False, index=True)

    def to_dict(self):
        """Serializes the Video object to a dictionary for the frontend API."""
        has_custom_thumb = bool(self.custom_thumbnail_path and os.path.exists(self.custom_thumbnail_path))
        has_auto_thumb = bool(self.thumbnail_path and os.path.exists(self.thumbnail_path))
        
        image_url_to_use = None
        mtime = 0
        
        # If this is an image, the "thumbnail" is the file itself.
        if self.media_type == 'image':
            image_url_to_use = f'/api/video/{self.id}' # Reuse stream endpoint
        else:
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
            'video_type': self.video_type,
            'media_type': self.media_type,
            'is_associated_thumbnail': self.is_associated_thumbnail
        }

class SmartPlaylist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    filters = db.Column(db.Text, default='[]') # Stores JSON array of rules

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'filters': json.loads(self.filters) if self.filters else [], 
        }

class StandardPlaylist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    
    def to_dict(self, video_playlist_ids=None):
        is_in_playlist = False
        if video_playlist_ids and self.id in video_playlist_ids:
            is_in_playlist = True

        return {
            'id': self.id,
            'name': self.name,
            'is_in_playlist': is_in_playlist 
        }

class PlaylistItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey('standard_playlist.id'), nullable=False)
    video_id = db.Column(db.Integer, db.ForeignKey('video.id'), nullable=False)
    
    __table_args__ = (db.UniqueConstraint('playlist_id', 'video_id', name='_playlist_video_uc'),)


with app.app_context():
    db.create_all()


## --- Library Pruning Helper ---
def _prune_missing_videos(found_video_paths):
    """
    Compares a set of found video paths against the DB and deletes entries
    that are no longer found. Also cleans up associated thumbnails/transcodes.
    Returns the number of deleted videos.
    """
    deleted_count = 0
    try:
        all_db_videos_query = select(Video.id, Video.video_path, Video.thumbnail_path, Video.custom_thumbnail_path, Video.transcoded_path)
        all_db_videos = db.session.execute(all_db_videos_query).all()
        
        db_video_map = {v.video_path: v for v in all_db_videos}
        paths_to_delete = set(db_video_map.keys()) - found_video_paths
        
        if not paths_to_delete:
            print("  - Prune: No videos to delete.")
            return 0

        print(f"  - Prune: Found {len(paths_to_delete)} videos to delete...")
        video_ids_to_delete = [db_video_map[path].id for path in paths_to_delete]

        if video_ids_to_delete:
            db.session.execute(
                delete(PlaylistItem).where(PlaylistItem.video_id.in_(video_ids_to_delete))
            )
            print(f"  - Prune: Removed {len(video_ids_to_delete)} videos from standard playlists.")

        for path in paths_to_delete:
            video_data = db_video_map[path]
            
            try:
                if video_data.transcoded_path and os.path.exists(video_data.transcoded_path):
                    os.remove(video_data.transcoded_path)
                    print(f"    - Deleted transcoded file: {video_data.transcoded_path}")
            except Exception as e:
                print(f"    - Error deleting transcoded file {video_data.transcoded_path}: {e}")
            
            try:
                if video_data.thumbnail_path and os.path.exists(video_data.thumbnail_path):
                    os.remove(video_data.thumbnail_path)
                    print(f"    - Deleted thumbnail: {video_data.thumbnail_path}")
            except OSError as e:
                print(f"    - Error deleting thumbnail {video_data.thumbnail_path}: {e}")
            
            try:
                if video_data.custom_thumbnail_path and os.path.exists(video_data.custom_thumbnail_path):
                    os.remove(video_data.custom_thumbnail_path)
                    print(f"    - Deleted custom thumbnail: {video_data.custom_thumbnail_path}")
            except OSError as e:
                print(f"    - Error deleting custom thumbnail {video_data.custom_thumbnail_path}: {e}")
            
            db.session.execute(delete(Video).where(Video.id == video_data.id))
            print(f"    - Deleted video record: {video_data.video_path}")
            deleted_count += 1
            
        if deleted_count > 0:
            with DB_WRITE_LOCK:
                db.session.commit()
        print(f"  - Prune: Finished. Deleted {deleted_count} videos.")

    except Exception as e:
        print(f"  - Error during prune: {e}")
        with DB_WRITE_LOCK:
            db.session.rollback()
    
    return deleted_count


## --- Background Task Functions ---
def _scan_videos_task(full_scan=False, auto_chain=False):
    """
    Optimized Scan:
    1. Loads existing DB paths into memory first.
    2. Checks file existence against memory cache.
    3. Handles Videos AND Images (including GIFs).
    4. Finds local artwork (jpg/png) for videos.
    5. If auto_chain=True, triggers thumbnail generation after scanning.
    """
    global SCAN_STATUS
    try:
        with app.app_context():
            SCAN_STATUS = {"status": "scanning", "message": "Starting optimized library scan...", "progress": 0}
            print(f"Starting scan of: {video_dir} (Full Scan: {full_scan})")
            
            # --- OPTIMIZATION: Pre-load all existing paths ---
            print("  - Pre-loading existing database records...")
            all_existing_videos = db.session.execute(select(Video)).scalars().all()
            db_cache = {v.video_path: v for v in all_existing_videos}
            print(f"  - Loaded {len(db_cache)} existing items from DB.")

            added_count = 0
            updated_count = 0
            skipped_count = 0
            found_video_paths = set()
            video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm']
            # ADDED: .gif, .webp, .bmp, .tiff
            image_extensions = ['.jpg', '.jpeg', '.png', '.tbn', '.gif', '.webp', '.bmp', '.tiff']
            poster_filenames = ['poster.jpg', 'poster.jpeg', 'poster.png', 'poster.gif']

            for dirpath, dirnames, filenames in os.walk(video_dir, topdown=True):
                dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                if 'vd21_hide' in filenames: dirnames[:] = []; continue

                for filename in filenames:
                    if filename.startswith('.'): continue
                    file_ext = os.path.splitext(filename)[1].lower()
                    
                    is_video = file_ext in video_extensions
                    is_image = file_ext in image_extensions
                    
                    if not is_video and not is_image: continue

                    video_file_path = os.path.normpath(os.path.join(dirpath, filename))
                    found_video_paths.add(video_file_path)

                    # --- OPTIMIZATION: Skip if known ---
                    if not full_scan and video_file_path in db_cache:
                        skipped_count += 1
                        continue

                    # If we are here, it's a NEW file. Process it.
                    video_base_filename = os.path.splitext(filename)[0]
                    video_full_filename = filename
                    
                    # --- NEW LOGIC: Differentiate Video vs Image ---
                    media_type = 'video' if is_video else 'image'
                    is_associated_thumb = False
                    
                    # If it's an image, check if it belongs to a video (shares same name)
                    if is_image:
                        for vext in video_extensions:
                            sibling_video = os.path.normpath(os.path.join(dirpath, video_base_filename + vext))
                            if os.path.exists(sibling_video):
                                is_associated_thumb = True
                                break
                    
                    try:
                        file_size_bytes = os.path.getsize(video_file_path)
                        mtime = os.path.getmtime(video_file_path)
                        uploaded_date = datetime.datetime.fromtimestamp(mtime)
                    except OSError: continue 

                    relative_dir = None
                    try:
                        norm_base_dir = os.path.normpath(video_dir)
                        relative_dir = os.path.relpath(os.path.dirname(video_file_path), norm_base_dir)
                        relative_dir = relative_dir.replace(os.sep, '/')
                        if relative_dir == '.': relative_dir = None 
                    except: relative_dir = None

                    file_format_str = file_ext.replace('.', '')
                    nfo_path = os.path.normpath(os.path.join(dirpath, video_base_filename + '.nfo'))
                    has_nfo_file = os.path.exists(nfo_path)

                    # --- FFPROBE (Videos Only) ---
                    is_short = False
                    effective_width = 0
                    effective_height = 0
                    duration_sec = 0
                    video_codec = 'unknown'
                    
                    if is_video:
                        try:
                            ffprobe_cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,duration,codec_name:stream_tags=rotate:stream_side_data=rotation:stream_disposition=rotate', '-of', 'json', video_file_path]
                            result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True, timeout=30)
                            data = json.loads(result.stdout)
                            if 'streams' in data and len(data['streams']) > 0:
                                stream = data['streams'][0]
                                coded_width = stream.get('width', 0)
                                coded_height = stream.get('height', 0)
                                try: duration_sec = int(float(stream.get('duration', '0')))
                                except: duration_sec = 0
                                video_codec = stream.get('codec_name', 'unknown').upper()
                                
                                rotation = 0
                                try: rotation_str = stream.get('tags', {}).get('rotate', '0'); rotation = int(float(rotation_str))
                                except: pass
                                if rotation == 0:
                                    try: side_data = stream.get('side_data_list', [{}])[0]; rotation_str = side_data.get('rotation', '0'); rotation = int(float(rotation_str))
                                    except: pass
                                
                                effective_width = coded_width
                                effective_height = coded_height
                                if abs(rotation) == 90 or abs(rotation) == 270:
                                    effective_width = coded_height
                                    effective_height = coded_width
                                if effective_height > effective_width: is_short = True
                        except: pass

                    # --- ASSETS (Only relevant for Videos) ---
                    srt_path = None; srt_label = None; srt_lang = None
                    poster_path_to_save = None; custom_thumb_file_path = None; transcoded_file_path = None
                    thumbnail_file_path = None

                    if is_video:
                        # Find SRTs
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

                        # Find Local Images (Thumbnails)
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
                            except: pass

                        # Find Posters
                        current_search_dir = os.path.dirname(video_file_path)
                        try:
                            while True:
                                if not os.path.commonpath([video_dir, current_search_dir]) == video_dir: break
                                try:
                                    files_in_dir = os.listdir(current_search_dir)
                                    for f in files_in_dir:
                                        if f.lower() in poster_filenames:
                                            poster_path_to_save = os.path.join(current_search_dir, f)
                                            break
                                except: pass
                                if poster_path_to_save: break 
                                if os.path.samefile(current_search_dir, video_dir): break
                                current_search_dir = os.path.dirname(current_search_dir)
                        except: pass

                        # Transcode Check
                        try:
                            potential_transcode = get_transcoded_path_for_video(video_file_path)
                            if os.path.exists(potential_transcode):
                                transcoded_file_path = potential_transcode
                        except: pass
                        
                        # Custom Thumb Check
                        try:
                            potential_custom_thumb = get_custom_thumbnail_path(video_file_path)
                            if os.path.exists(potential_custom_thumb):
                                custom_thumb_file_path = potential_custom_thumb
                        except: pass
                    
                    # NFO Parsing
                    title = video_base_filename.replace('.', ' ')
                    show_title = "Unknown Show" if not relative_dir else os.path.basename(relative_dir)
                    plot = ""; aired_date = uploaded_date; youtube_id = None
                    
                    if has_nfo_file:
                        try:
                            tree = ET.parse(nfo_path); root = tree.getroot()
                            title = root.findtext('title') or title
                            show_title = root.findtext('showtitle') or show_title
                            plot = root.findtext('plot') or plot
                            youtube_id = root.findtext('uniqueid')
                            if root.findtext('aired'):
                                try: aired_date = datetime.datetime.strptime(root.findtext('aired').split(' ')[0], '%Y-%m-%d')
                                except: pass
                        except: pass

                    # DB Add/Update
                    try:
                        existing_video = db_cache.get(video_file_path)
                        if existing_video:
                            existing_video.media_type = media_type
                            existing_video.is_associated_thumbnail = is_associated_thumb
                            existing_video.title = title
                            existing_video.duration = duration_sec
                            if thumbnail_file_path:
                                existing_video.thumbnail_path = thumbnail_file_path
                            existing_video.show_poster_path = poster_path_to_save
                            existing_video.custom_thumbnail_path = custom_thumb_file_path
                            existing_video.subtitle_path = srt_path
                            # ... (abbreviating update fields for brevity since logic is restored)
                            updated_count += 1
                        else:
                            new_video = Video(
                                title=title, show_title=show_title, summary=plot, aired=aired_date, uploaded_date=uploaded_date,
                                video_path=video_file_path, relative_path=relative_dir, thumbnail_path=thumbnail_file_path,
                                show_poster_path=poster_path_to_save, custom_thumbnail_path=custom_thumb_file_path,
                                subtitle_path=srt_path, subtitle_label=srt_label, subtitle_lang=srt_lang,
                                filename=filename, file_size=file_size_bytes, file_format=file_format_str,
                                has_nfo=has_nfo_file, is_short=is_short, dimensions=f"{effective_width}x{effective_height}",
                                duration=duration_sec, video_codec=video_codec, transcoded_path=transcoded_file_path,
                                media_type=media_type, is_associated_thumbnail=is_associated_thumb
                            )
                            db.session.add(new_video)
                            added_count += 1
                    except Exception as e:
                        print(f"  - DB Error: {e}")
                        with DB_WRITE_LOCK: db.session.rollback()

                    if (added_count + updated_count) > 0 and (added_count + updated_count) % 50 == 0:
                        with DB_WRITE_LOCK: db.session.commit()
                        SCAN_STATUS['progress'] = added_count + updated_count
                        SCAN_STATUS['message'] = f"Scanning... {added_count} new."

            if added_count > 0 or updated_count > 0:
                with DB_WRITE_LOCK: db.session.commit()
            
            deleted_count = 0
            if full_scan:
                SCAN_STATUS['message'] = "Pruning deleted videos..."
                deleted_count = _prune_missing_videos(found_video_paths)
            
            print(f"Scan finished. Added: {added_count}, Updated: {updated_count}, Skipped: {skipped_count}.")
            SCAN_STATUS = {"status": "idle", "message": "Scan complete.", "progress": 0}

            # --- AUTO CHAINING ---
            if auto_chain and (added_count > 0 or updated_count > 0):
                print("Auto-Chain: Triggering thumbnail generation...")
                if thumbnail_generation_lock.acquire(blocking=False):
                    thumb_thread = threading.Thread(target=_generate_thumbnails_task)
                    thumb_thread.start()

    except Exception as e:
        print(f"Scan Error: {e}")
        with DB_WRITE_LOCK: db.session.rollback()
        SCAN_STATUS = {"status": "error", "message": str(e), "progress": 0}
    finally:
        SCAN_LOCK.release()


def _cleanup_library_task():
    """
    Scans the library for deleted videos *only* and prunes them.
    This is much faster than a full scan.
    """
    global CLEANUP_STATUS
    try:
        with app.app_context():
            CLEANUP_STATUS = {"status": "cleaning", "message": "Finding all video files...", "progress": 0}
            print("Starting library cleanup task...")
            
            found_video_paths = set()
            video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm']
            # Fix cleanup to include images, otherwise they get deleted by cleanup
            image_extensions = ['.jpg', '.jpeg', '.png', '.tbn', '.gif', '.webp', '.bmp', '.tiff']
            
            for dirpath, dirnames, filenames in os.walk(video_dir, topdown=True):
                dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                if 'vd21_hide' in filenames:
                    dirnames[:] = []
                    continue

                for filename in filenames:
                    if filename.startswith('.'):
                        continue
                    ext = os.path.splitext(filename)[1].lower()
                    if ext in video_extensions or ext in image_extensions:
                        video_file_path = os.path.normpath(os.path.join(dirpath, filename))
                        found_video_paths.add(video_file_path)

            print(f"  - Cleanup: Found {len(found_video_paths)} items on disk.")
            
            CLEANUP_STATUS['message'] = "Pruning deleted items..."
            deleted_count = _prune_missing_videos(found_video_paths)

            print(f"Cleanup finished. Pruned {deleted_count} items.")
            CLEANUP_STATUS = {"status": "idle", "message": f"Cleanup complete. Removed {deleted_count} items.", "progress": 0}

    except Exception as e:
        print(f"  - Error during cleanup task: {e}")
        with DB_WRITE_LOCK:
            db.session.rollback()
        CLEANUP_STATUS = {"status": "error", "message": str(e), "progress": 0}
    finally:
        CLEANUP_LOCK.release()
        print("Cleanup lock released.")
        sys.stdout.flush()


def build_folder_tree(paths):
    tree = {}
    for path in paths:
        if not path: continue
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
            
            # --- UPDATED LOGIC ---
            # 1. Only look for VIDEOS (media_type='video')
            # 2. Check if file is missing locally
            print("Checking for videos with missing or broken thumbnails...")
            all_videos = db.session.scalars(select(Video).filter(Video.media_type == 'video')).all()
            videos_to_process = []

            for v in all_videos:
                if not v.thumbnail_path:
                    videos_to_process.append(v)
                elif not os.path.exists(v.thumbnail_path):
                    videos_to_process.append(v)

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
                        print(f"  - Skipping {video.filename} (source file not found)")
                        continue

                    new_thumb_path = get_thumbnail_path_for_video(video.video_path)

                    # Using the optimized input seeking (-ss before -i)
                    result = subprocess.run([
                        "ffmpeg",
                        "-ss", "00:00:10",  
                        "-i", video_path,
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
                            print(f"  - Generated thumbnail for: {video.filename}")
                        else:
                            print(f"  - FAILED to write file for: {video.filename}")
                    else:
                        print(f"  - FAILED to generate for: {video.filename}")

                except Exception as e:
                    print(f"  - Error processing {video.filename}: {e}")
                    with DB_WRITE_LOCK: db.session.rollback()
                
                if generated_count > 0 and generated_count % 50 == 0:
                    with DB_WRITE_LOCK: db.session.commit()
            
            if generated_count > 0:
                with DB_WRITE_LOCK: db.session.commit()

            print(f"Thumbnail generation task finished. Generated {generated_count} new thumbnails.")
            sys.stdout.flush() 

        except Exception as e:
            print(f"Fatal error in thumbnail task: {e}")
            with DB_WRITE_LOCK: db.session.rollback()
            THUMBNAIL_STATUS.update({"status": "error", "message": str(e)})
        finally:
            thumbnail_generation_lock.release()
            THUMBNAIL_STATUS.update({"status": "idle", "message": "Done."})

def _transcode_video_task(video_id):
    """
    Runs the ffmpeg transcode process in a background thread.
    """
    global TRANSCODE_STATUS
    try:
        with app.app_context():
            video = db.get_or_404(Video, video_id)
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
                if APP_HW_ACCEL_MODE == 'qsv':
                    print(f"  - [HW-VAAPI] Using HYBRID (CPU decode + VAAPI encode) for: {video.filename}")
                    ffmpeg_cmd = [
                        'ffmpeg',
                        '-vaapi_device', '/dev/dri/renderD128', 
                        '-i', input_path,
                        '-vf', "format=nv12,hwupload,scale_vaapi=w='min(iw,1920)':h='min(ih,1080)'",
                        '-c:v', 'h264_vaapi',
                        '-c:a', 'aac',
                        '-b:a', '128k',
                        '-movflags', '+faststart',
                        output_path
                    ]
                else:
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
                sys.stdout.flush()
                subprocess.run(ffmpeg_cmd, check=True, capture_output=True)
            
            video.transcoded_path = output_path
            with DB_WRITE_LOCK:
                db.session.commit()
            print(f"  - Transcode complete: {output_path}")
            
            TRANSCODE_STATUS = {"status": "idle", "message": "Transcode complete.", "video_id": None}

    except subprocess.CalledProcessError as e:
        print(f"  - FFmpeg error during transcode: {e.stderr.decode('utf-8', errors='ignore')}")
        TRANSCODE_STATUS = {"status": "error", "message": "FFmpeg failed.", "video_id": video_id}
    except Exception as e:
        print(f"  - Error during transcode task: {e}")
        with DB_WRITE_LOCK:
            db.session.rollback()
        TRANSCODE_STATUS = {"status": "error", "message": str(e), "video_id": video_id}
    finally:
        TRANSCODE_LOCK.release()
        print("Transcode lock released.")
        sys.stdout.flush()
        
        ## --- Watchdog & Automation Helpers ---
def trigger_auto_scan():
    """Helper to safely trigger a background scan if one isn't running."""
    if SCAN_LOCK.acquire(blocking=False):
        print("Watchdog: Triggering automatic library scan...")
        # We chain the tasks: Scan -> Generate Thumbs -> (Optional) Transcode
        scan_thread = threading.Thread(target=_scan_videos_task, args=(False, True)) # Added 'True' arg for chaining
        scan_thread.start()
    else:
        print("Watchdog: Scan already in progress, skipping trigger.")

class LibraryEventHandler(FileSystemEventHandler):
    """Handles file system events (create/delete/move)."""
    
    def on_created(self, event):
        if event.is_directory: return
        filename = os.path.basename(event.src_path)
        if filename.startswith('.'): return
        
        # Debounce: Wait a moment for file copy to finish (basic)
        # For robust production use, checking file size stability is better, 
        # but this is usually sufficient for personal servers.
        print(f"Watchdog: File detected - {filename}")
        trigger_auto_scan()

    def on_moved(self, event):
        if event.is_directory: return
        print(f"Watchdog: File moved/renamed - {event.src_path}")
        trigger_auto_scan()

    def on_deleted(self, event):
        if event.is_directory: return
        print(f"Watchdog: File deleted - {event.src_path}")
        # Trigger cleanup for deletes
        if CLEANUP_LOCK.acquire(blocking=False):
            cleanup_thread = threading.Thread(target=_cleanup_library_task)
            cleanup_thread.start()

def start_watchdog():
    """Starts the background file observer."""
    event_handler = LibraryEventHandler()
    observer = Observer()
    observer.schedule(event_handler, video_dir, recursive=True)
    observer.start()
    print(f"*** Watchdog Active: Monitoring {video_dir} for changes ***")

## --- Initialization Function ---
def initialize_database():
    """Checks if DB is empty and starts initial scan."""
    with app.app_context():
        print("Initializing database...")
        video_count = db.session.scalar(select(func.count(Video.id)))
        if video_count == 0:
            print("No videos found. Acquiring scan lock for initial scan...")
            if SCAN_LOCK.acquire(blocking=False):
                print("Lock acquired. Starting initial background scan...")
                SCAN_STATUS = {"status": "scanning", "message": "Starting initial scan...", "progress": 0}
                scan_thread = threading.Thread(target=_scan_videos_task, args=(True, True))
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

## --- API Endpoints ---
@app.route('/api/metadata')
def get_metadata():
    """
    Returns non-video data: playlists, folder tree, and author counts.
    """
    playlists = db.session.scalars(select(SmartPlaylist).order_by(SmartPlaylist.id.asc())).all()
    playlist_dtos = [p.to_dict() for p in playlists]
    
    all_paths = db.session.scalars(select(Video.relative_path).distinct()).all()
    folder_tree = build_folder_tree([p for p in all_paths if p])
    
    author_counts_query = db.session.execute(
        select(Video.show_title, func.count(Video.id)).group_by(Video.show_title)
    ).all()
    
    author_counts_map = {}
    for author, count in author_counts_query:
        key = author if author else "Unknown Show"
        author_counts_map[key] = count

    standard_playlists = db.session.scalars(select(StandardPlaylist).order_by(StandardPlaylist.name.asc())).all()
    standard_playlist_dtos = [p.to_dict() for p in standard_playlists]

    return jsonify({
        'folder_tree': folder_tree,
        'smartPlaylists': playlist_dtos,
        'standardPlaylists': standard_playlist_dtos,
        'author_counts': author_counts_map
    })

@app.route('/api/videos')
def get_videos():
    """
    Returns paginated video data based on all filter, sort, and view parameters.
    """
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 30
        
        viewType = request.args.get('viewType', 'all')
        viewId = request.args.get('viewId', None)
        viewAuthor = request.args.get('viewAuthor', None)
        searchQuery = request.args.get('searchQuery', None)
        sortOrder = request.args.get('sortOrder', 'aired_newest')
        
        filterShorts = request.args.get('filterShorts', 'normal')
        filterVR = request.args.get('filterVR', 'normal')
        filterOptimized = request.args.get('filterOptimized', 'normal')
        
        # --- NEW FILTERS FOR IMAGES ---
        showImages = request.args.get('showImages', 'false') == 'true'
        showThumbnails = request.args.get('showThumbnails', 'false') == 'true'

        base_query = select(Video)
        
        # --- LOGIC: Image Support ---
        # 1. By default, show ONLY videos.
        # 2. If showImages=True, show images.
        # 3. If showImages=True AND showThumbnails=False, hide "associated thumbnails".
        
        conditions = []
        is_video = Video.media_type == 'video'
        
        if showImages:
            if showThumbnails:
                # Show everything (videos + all images)
                is_valid_image = Video.media_type == 'image'
            else:
                # Show videos + images that are NOT thumbnails
                is_valid_image = and_(Video.media_type == 'image', Video.is_associated_thumbnail == False)
            
            # Combine logic: (It is a video) OR (It is a valid image to show)
            base_query = base_query.where(or_(is_video, is_valid_image))
        else:
            # Standard behavior: Only videos
            base_query = base_query.where(is_video)

        # Handle Special Views
        if viewType == 'favorites':
            base_query = base_query.where(Video.is_favorite == True)
        elif viewType == 'watchLater':
            base_query = base_query.where(Video.is_watch_later == True)
        elif viewType == 'history':
            base_query = base_query.where(Video.watched_duration >= 4)
        elif viewType == 'shorts':
            base_query = base_query.where(Video.is_short == True)
        elif viewType == 'optimized':
            base_query = base_query.where(Video.transcoded_path != None)
        elif viewType == 'VR180':
            base_query = base_query.where(or_(Video.video_type == 'VR180_SBS', Video.video_type == 'VR180_TB'))
        elif viewType == 'VR360':
            base_query = base_query.where(Video.video_type == 'VR360')
        elif viewType == 'author' and viewAuthor:
            base_query = base_query.where(Video.show_title == viewAuthor)
        elif viewType == 'folder' and viewId:
            base_query = base_query.where(
                or_(
                    Video.relative_path == viewId,
                    Video.relative_path.like(viewId + '/%')
                )
            )
        elif viewType == 'standard_playlist' and viewId:
            video_ids_query = db.session.scalars(select(PlaylistItem.video_id).filter_by(playlist_id=viewId)).all()
            if not video_ids_query:
                video_ids_query = [-1] # Return no videos if empty
            base_query = base_query.where(Video.id.in_(video_ids_query))
        
        elif viewType == 'smart_playlist' and viewId:
            playlist = db.get_or_404(SmartPlaylist, viewId)
            filters_json = request.args.get('smart_filters') # Filters are passed from JS
            filters = json.loads(filters_json) if filters_json else []
            
            if filters:
                master_filter_conditions = []
                
                # --- 1. Process Author Filters (as one OR group) ---
                author_values = []
                for f in filters:
                    if f.get('type') == 'author' and f.get('value'):
                        author_values.extend(f['value'])
                
                if author_values:
                    master_filter_conditions.append(Video.show_title.in_(author_values))
                    
                # --- 2. Process Title Keyword Filters (as one OR group) ---
                keyword_values = []
                for f in filters:
                    if f.get('type') == 'title' and f.get('value'): 
                        keyword_values.extend(f['value'])
                
                if keyword_values:
                    keyword_or_conditions = []
                    for keyword in keyword_values:
                        keyword_or_conditions.append(Video.title.ilike(f"%{keyword}%"))
                    if keyword_or_conditions:
                        master_filter_conditions.append(or_(*keyword_or_conditions))

                # --- 3. Process Duration Filters (as individual ANDs) ---
                for f in filters:
                    if f.get('type') == 'duration' and f.get('value') and f.get('operator'):
                        try:
                            duration_seconds = int(f['value'])
                            if f['operator'] == 'gt':
                                master_filter_conditions.append(Video.duration > duration_seconds)
                            elif f['operator'] == 'lt':
                                master_filter_conditions.append(Video.duration < duration_seconds)
                        except (ValueError, TypeError):
                            pass # Ignore invalid duration filter
                
                if master_filter_conditions:
                    base_query = base_query.where(and_(*master_filter_conditions))
        
        elif viewType == 'video' and viewId:
            base_query = base_query.where(Video.id == viewId)
            
        # Global Filters
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
            base_query = base_query.where(or_(*solo_filters))

        if not isSoloActive:
            if filterShorts == 'hide' and viewType != 'shorts':
                base_query = base_query.where(Video.is_short == False)
            if filterVR == 'hide' and viewType not in ['VR180', 'VR360']:
                base_query = base_query.where(Video.video_type == None)
            if filterOptimized == 'hide' and viewType != 'optimized':
                base_query = base_query.where(Video.transcoded_path == None)

        # Search Query
        if searchQuery:
            search_term = f"%{searchQuery.lower()}%"
            base_query = base_query.where(
                or_(
                    Video.title.ilike(search_term),
                    Video.summary.ilike(search_term),
                    Video.show_title.ilike(search_term)
                )
            )
            
        # Sort Order
        if viewType == 'history':
            base_query = base_query.order_by(Video.last_watched.desc().nullslast())
        else:
            if sortOrder == 'aired_oldest':
                base_query = base_query.order_by(Video.aired.asc().nullsfirst())
            elif sortOrder == 'uploaded_newest':
                base_query = base_query.order_by(Video.uploaded_date.desc().nullslast())
            elif sortOrder == 'uploaded_oldest':
                base_query = base_query.order_by(Video.uploaded_date.asc().nullsfirst())
            elif sortOrder == 'duration_longest':
                base_query = base_query.order_by(Video.duration.desc().nullslast())
            elif sortOrder == 'duration_shortest':
                base_query = base_query.order_by(Video.duration.asc().nullsfirst())
            else: # aired_newest
                base_query = base_query.order_by(Video.aired.desc().nullslast())

        # Paginate and Return
        if viewType in ['standard_playlist', 'video']:
            videos = db.session.scalars(base_query).all()
            video_dtos = [v.to_dict() for v in videos]
            return jsonify({
                'articles': video_dtos,
                'total_items': len(video_dtos),
                'total_pages': 1,
                'current_page': 1,
                'has_next_page': False
            })
        else:
            pagination = db.paginate(base_query, page=page, per_page=per_page, error_out=False)
            videos_on_page = pagination.items
            video_dtos = [v.to_dict() for v in videos_on_page]
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

@app.route('/api/videos_all')
def get_all_videos():
    """
    Returns ALL videos, unpaginated.
    Used *only* by the Smart Playlist modal, which requires a full author list.
    """
    try:
        videos = db.session.scalars(select(Video)).all()
        video_dtos = [v.to_dict() for v in videos]
        return jsonify({'articles': video_dtos})
    except Exception as e:
        print(f"Error in /api/videos_all: {e}")
        return jsonify({"error": str(e)}), 500


## --- API: Smart Playlist Management ---
@app.route('/api/playlist/smart/create', methods=['POST'])
def create_smart_playlist():
    data = request.get_json()
    name = data.get('name')
    if not name:
        return jsonify({"error": "Playlist name is required"}), 400
    new_playlist = SmartPlaylist(name=name.strip())
    db.session.add(new_playlist)
    db.session.commit()
    return jsonify(new_playlist.to_dict()), 201

@app.route('/api/playlist/smart/<int:playlist_id>/delete', methods=['POST'])
def delete_smart_playlist(playlist_id):
    playlist = db.get_or_404(SmartPlaylist, playlist_id)
    db.session.delete(playlist)
    db.session.commit()
    return jsonify({'success': True}), 200

@app.route('/api/playlist/smart/<int:playlist_id>/update_filters', methods=['POST'])
def update_smart_playlist_filters(playlist_id):
    playlist = db.get_or_404(SmartPlaylist, playlist_id)
    data = request.get_json()
    new_filters = data.get('filters')

    if not isinstance(new_filters, list):
        return jsonify({"error": "A valid 'filters' array is required"}), 400

    try:
        playlist.filters = json.dumps(new_filters)
        db.session.commit()
        return jsonify(playlist.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/playlist/smart/<int:playlist_id>/rename', methods=['POST'])
def rename_smart_playlist(playlist_id):
    playlist = db.get_or_404(SmartPlaylist, playlist_id)
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

## --- API: Standard Playlist Management ---
@app.route('/api/playlist/standard/create', methods=['POST'])
def create_standard_playlist():
    data = request.get_json()
    name = data.get('name', '').strip()
    video_id_to_add = data.get('video_id', None)

    if not name:
        return jsonify({"error": "Playlist name is required"}), 400
    
    existing_playlist = db.session.scalar(
        select(StandardPlaylist).filter_by(name=name)
    )
    if existing_playlist:
        return jsonify({"error": "A playlist with this name already exists."}), 409

    try:
        new_playlist = StandardPlaylist(name=name)
        db.session.add(new_playlist)
        db.session.commit()
        
        if video_id_to_add:
            video = db.session.get(Video, video_id_to_add)
            if video:
                new_item = PlaylistItem(playlist_id=new_playlist.id, video_id=video.id)
                db.session.add(new_item)
                db.session.commit()
        
        all_playlists = db.session.scalars(select(StandardPlaylist).order_by(StandardPlaylist.name.asc())).all()
        video_playlists = get_video_playlist_ids(video_id_to_add)
        return jsonify([p.to_dict(video_playlists) for p in all_playlists]), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/playlist/toggle_video', methods=['POST'])
def toggle_video_in_playlist():
    data = request.get_json()
    playlist_id = data.get('playlist_id')
    video_id = data.get('video_id')

    if not playlist_id or not video_id:
        return jsonify({"error": "playlist_id and video_id are required"}), 400
    
    try:
        item = db.session.scalar(
            select(PlaylistItem).filter_by(playlist_id=playlist_id, video_id=video_id)
        )
        
        if item:
            db.session.delete(item)
            db.session.commit()
        else:
            new_item = PlaylistItem(playlist_id=playlist_id, video_id=video_id)
            db.session.add(new_item)
            db.session.commit()
        
        all_playlists = db.session.scalars(select(StandardPlaylist).order_by(StandardPlaylist.name.asc())).all()
        video_playlists = get_video_playlist_ids(video_id)
        return jsonify([p.to_dict(video_playlists) for p in all_playlists]), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/api/video/<int:video_id>/playlists', methods=['GET'])
def get_video_playlists(video_id):
    """
    Gets all standard playlists and marks which ones this video is in.
    """
    try:
        all_playlists = db.session.scalars(select(StandardPlaylist).order_by(StandardPlaylist.name.asc())).all()
        video_playlists = get_video_playlist_ids(video_id)
        
        return jsonify([p.to_dict(video_playlists) for p in all_playlists]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def get_video_playlist_ids(video_id):
    """Helper function to get a set of playlist IDs a video belongs to."""
    if not video_id:
        return set()
    items = db.session.scalars(select(PlaylistItem).filter_by(video_id=video_id)).all()
    return {item.playlist_id for item in items}


## --- API: Video/Thumbnail Serving ---
@app.route('/api/video/<int:video_id>')
def stream_video(video_id):
    """Streams the original video file."""
    video = db.get_or_404(Video, video_id)
    if not os.path.exists(video.video_path):
        return jsonify({"error": "Video file not found"}), 404
    mimetype = mimetypes.guess_type(video.video_path)[0] or 'video/mp4'
    video_dir_path = os.path.dirname(video.video_path)
    video_filename = os.path.basename(video.video_path)
    return send_from_directory(video_dir_path, video_filename, as_attachment=False, mimetype=mimetype)

@app.route('/api/thumbnail/<int:video_id>')
def get_thumbnail(video_id):
    """Serves the highest priority thumbnail for a video."""
    video = db.get_or_404(Video, video_id)
    
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
    video = db.get_or_404(Video, video_id)
    if not video.show_poster_path or not os.path.exists(video.show_poster_path):
        return jsonify({"error": "Show poster not found"}), 404
    poster_dir = os.path.dirname(video.show_poster_path)
    poster_filename = os.path.basename(video.show_poster_path)
    mimetype = mimetypes.guess_type(video.show_poster_path)[0] or 'image/jpeg'
    return send_from_directory(poster_dir, poster_filename, as_attachment=False, mimetype=mimetype)

@app.route('/api/subtitle/<int:video_id>')
def get_subtitle(video_id):
    """Serves the subtitle file, converting it from SRT to VTT on-the-fly."""
    video = db.get_or_404(Video, video_id)
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
    video = db.get_or_404(Video, article_id)
    video.is_favorite = not video.is_favorite
    db.session.commit()
    return jsonify({'is_favorite': video.is_favorite})

@app.route('/api/article/<int:article_id>/bookmark', methods=['POST'])
def toggle_watch_later(article_id):
    video = db.get_or_404(Video, article_id)
    video.is_watch_later = not video.is_watch_later
    db.session.commit()
    return jsonify({'is_read_later': video.is_watch_later})

@app.route('/api/video/<int:video_id>/progress', methods=['POST'])
def update_video_progress(video_id):
    video = db.get_or_404(Video, video_id)
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
    API endpoint to trigger a video scan in the background.
    Accepts {'full_scan': true} to run a full library refresh.
    Defaults to a "quick scan" (full_scan=False) which only adds new files.
    """
    print("API: Received scan request.")
    if not SCAN_LOCK.acquire(blocking=False):
        print("API: Scan already in progress.")
        return jsonify({"message": "Scan already in progress."}), 409
    
    try:
        data = request.get_json()
        full_scan = data.get('full_scan', False)
        
        if full_scan:
            print("API: Starting FULL background video scan...")
            SCAN_STATUS = {"status": "scanning", "message": "Full scan started by user.", "progress": 0}
        else:
            print("API: Starting NEW-ONLY background video scan...")
            SCAN_STATUS = {"status": "scanning", "message": "New-only scan started by user.", "progress": 0}
            
        # Passing auto_chain=True so UI button triggers thumbnails too
        scan_thread = threading.Thread(target=_scan_videos_task, args=(full_scan, True))
        scan_thread.start()
        return jsonify({"message": "Scan started in background."}), 202
    except Exception as e:
        SCAN_LOCK.release()
        SCAN_STATUS = {"status": "error", "message": str(e), "progress": 0}
        print(f"API: Failed to start scan: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/thumbnails/generate_missing', methods=['POST'])
def generate_missing_thumbnails_route():
    """Triggers a background task to generate missing thumbnails."""
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

@app.route('/api/library/cleanup', methods=['POST'])
def cleanup_library_route():
    """Triggers a background task to prune deleted videos."""
    global CLEANUP_STATUS
    
    if not CLEANUP_LOCK.acquire(blocking=False):
        print("API: Cleanup already in progress.")
        return jsonify({"message": "Cleanup is already in progress."}), 409
    
    try:
        print("API: Starting background library cleanup thread...")
        CLEANUP_STATUS.update({"status": "cleaning", "message": "Starting cleanup..."})
        thread = threading.Thread(target=_cleanup_library_task)
        thread.start()
        return jsonify({"message": "Library cleanup started in background."}), 202
    except Exception as e:
        CLEANUP_LOCK.release()
        print(f"API: Failed to start background cleanup task: {str(e)}")
        CLEANUP_STATUS.update({"status": "error", "message": str(e)})
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

@app.route('/api/library/cleanup/status', methods=['GET'])
def get_cleanup_status():
    global CLEANUP_STATUS
    return jsonify(CLEANUP_STATUS)

@app.route('/api/video/<int:video_id>/stream_transcoded')
def stream_transcoded_video(video_id):
    """Streams the transcoded video file."""
    video = db.get_or_404(Video, video_id)
    if not video.transcoded_path or not os.path.exists(video.transcoded_path):
        return jsonify({"error": "Transcoded file not found"}), 404
    mimetype = 'video/mp4'
    video_dir_path = os.path.dirname(video.transcoded_path)
    video_filename = os.path.basename(video.transcoded_path)
    return send_from_directory(video_dir_path, video_filename, as_attachment=False, mimetype=mimetype)

@app.route('/api/video/<int:video_id>/download_transcoded')
def download_transcoded_video(video_id):
    """Downloads the transcoded video file."""
    video = db.get_or_404(Video, video_id)
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
    video = db.get_or_404(Video, video_id)
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

@app.route('/api/video/<int:video_id>/thumbnail/create_at_time', methods=['POST'])
def create_custom_thumbnail(video_id):
    """
    Generates a new thumbnail for the video at a specific timestamp.
    This overwrites any existing custom thumbnail.
    """
    video = db.get_or_404(Video, video_id)
    data = request.get_json()
    try:
        timestamp = float(data.get('timestamp', 10.0))
    except (ValueError, TypeError):
        timestamp = 10.0
    
    try:
        input_path = video.video_path
        output_path = get_custom_thumbnail_path(input_path)
        ss_time = str(datetime.timedelta(seconds=timestamp))

        print(f"  - Generating custom thumb for {video.filename} at {ss_time}...")
        
        # Ensure the thumbnail directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        result = subprocess.run([
            "ffmpeg",
            "-ss", ss_time,
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
    """
    video = db.get_or_404(Video, video_id)
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

@app.route('/api/video/<int:video_id>/set_tag', methods=['POST'])
def set_video_tag(video_id):
    """
    Manually sets the video tag (short, vr180, vr360, or none).
    """
    video = db.get_or_404(Video, video_id)
    data = request.get_json()
    tag = data.get('tag', 'none')
    
    try:
        if tag == 'short':
            video.is_short = True
            video.video_type = None
        elif tag == 'vr180':
            video.is_short = False
            video.video_type = 'VR180_SBS'
        elif tag == 'vr360':
            video.is_short = False
            video.video_type = 'VR360'
        else:
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
    start_watchdog() # Start the monitoring
    
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    # use_reloader=False prevents running two watchdogs in debug mode
    app.run(debug=debug_mode, host='0.0.0.0', port=5000, use_reloader=False)