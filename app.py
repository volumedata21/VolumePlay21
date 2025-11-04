## --- Imports ---
import os
import datetime
import xml.etree.ElementTree as ET # For parsing NFO files
import json # For handling playlist filters
import threading # --- NEW --- For background tasks
import subprocess # --- NEW --- For running ffmpeg
import sys # --- NEW --- To flush print statements
# NEW: Import Response
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

# --- NEW --- Global lock to prevent multiple thumbnail jobs
thumbnail_generation_lock = threading.Lock()

# --- NEW --- Global status for the thumbnail task
THUMBNAIL_STATUS = {
    "status": "idle", # idle, starting, generating, error
    "message": "",
    "progress": 0,
    "total": 0
}

## --- Database Models ---

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)           # From NFO <title> or filename
    show_title = db.Column(db.String(200))                     # From NFO <showtitle> or folder name
    summary = db.Column(db.Text)                               # From NFO <plot>
    video_path = db.Column(db.String(1000), unique=True, nullable=False) # Full path to the video file
    thumbnail_path = db.Column(db.String(1000))                # Full path to the thumbnail file
    subtitle_path = db.Column(db.String(1000), nullable=True)  # Full path to the .srt file
    subtitle_label = db.Column(db.String(50), nullable=True)   # e.g., "English" or "CC On"
    subtitle_lang = db.Column(db.String(10), nullable=True)    # e.g., "en"
    aired = db.Column(db.DateTime(timezone=False))             # From NFO <aired>
    uploaded_date = db.Column(db.DateTime(timezone=False))     # NEW: From file mtime
    youtube_id = db.Column(db.String(100), nullable=True)      # NEW: From NFO <uniqueid>
    is_favorite = db.Column(db.Boolean, default=False)
    is_watch_later = db.Column(db.Boolean, default=False)
    last_watched = db.Column(db.DateTime(timezone=False), nullable=True)
    watched_duration = db.Column(db.Integer, default=0) # Stored in seconds

    # --- NEW FIELDS FOR "MORE INFO" ---
    filename = db.Column(db.String(500), nullable=True)        # The video's filename (e.g., "video.mp4")
    file_size = db.Column(db.BigInteger, nullable=True)        # Filesize in bytes
    file_format = db.Column(db.String(10), nullable=True)      # e.g., "mp4", "mkv"
    has_nfo = db.Column(db.Boolean, default=False)             # True if .nfo file exists
    is_short = db.Column(db.Boolean, default=False)            # True if height > width
    dimensions = db.Column(db.String(100), nullable=True)      # e.g., "1920x1080"

    def to_dict(self):
        """
        Serializes the Video object to a dictionary, mapping to old 'Article' keys
        to minimize frontend changes.
        """
        # Calculate relative path for folder view
        relative_dir = '.'
        try:
            if not isinstance(self.video_path, str):
                self.video_path = str(self.video_path)
            
            norm_video_path = os.path.normpath(self.video_path)
            norm_base_dir = os.path.normpath(video_dir) # Use the derived base path
            
            relative_dir = os.path.relpath(os.path.dirname(norm_video_path), norm_base_dir)
            # Normalize to use forward slashes for consistency in JS/Python
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
            'author': self.show_title or 'Unknown Show',  # Map show_title -> author
            'published': self.aired.isoformat() if self.aired else (self.uploaded_date.isoformat() if self.uploaded_date else datetime.datetime.now().isoformat()),
            'aired_date': self.aired.isoformat() if self.aired else None,
            'uploaded': self.uploaded_date.isoformat() if self.uploaded_date else datetime.datetime.now().isoformat(),
            'is_favorite': self.is_favorite,
            'is_read_later': self.is_watch_later, # Map is_watch_later -> is_read_later
            
            'video_url': f'/api/video/{self.id}',
            'image_url': f'/api/thumbnail/{self.id}' if self.thumbnail_path else None,
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

            # --- NEW FIELDS FOR "MORE INFO" ---
            'filename': self.filename,
            'file_size': self.file_size,
            'file_format': self.file_format.upper() if self.file_format else 'Unknown',
            'has_nfo': self.has_nfo,
            # We can re-use existing data for the "Associated Files" list
            'has_thumbnail': bool(self.thumbnail_path),
            'has_subtitle': bool(self.subtitle_path),
            'is_short': self.is_short,
            'dimensions': self.dimensions,
        }

# SmartPlaylist Model
class SmartPlaylist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    # Tags added via drag-and-drop (stored as JSON array of strings)
    tags = db.Column(db.Text, default='[]') 
    # Filters for smart criteria (stored as JSON array of objects)
    filters = db.Column(db.Text, default='[]') 

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            # Deserialize tags/filters for frontend use
            'tags': json.loads(self.tags) if self.tags else [],
            'filters': json.loads(self.filters) if self.filters else [], 
        }

## --- Helper Functions ---
def scan_videos():
    """
    Scans the VIDEO_DIR for video files, *then* looks for optional
    .nfo, .srt, and thumbnail files. Creates fallbacks if metadata is missing.
    """
    print(f"Starting scan of: {video_dir}")
    added_count = 0
    updated_count = 0
    
    video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm']
    image_extensions = ['.jpg', '.jpeg', '.png', '.tbn']

    # CRITICAL: Start scanning from the actual video_dir root
    for dirpath, dirnames, filenames in os.walk(video_dir, topdown=True):
        for filename in filenames:
            file_ext = os.path.splitext(filename)[1].lower()
            if file_ext not in video_extensions:
                continue

            # --- GATHER ALL FILE INFO ---
            video_file_path = os.path.normpath(os.path.join(dirpath, filename))
            video_base_filename = os.path.splitext(filename)[0]
            video_full_filename = filename
            
            # Get file info (fast)
            try:
                file_size_bytes = os.path.getsize(video_file_path)
                mtime = os.path.getmtime(video_file_path)
                uploaded_date = datetime.datetime.fromtimestamp(mtime)
            except OSError as e:
                print(f"  - Skipping {filename} (OS Error): {e}")
                continue # Skip this file if we can't even get its size/mtime

            file_format_str = file_ext.replace('.', '')
            
            nfo_path = os.path.normpath(os.path.join(dirpath, video_base_filename + '.nfo'))
            has_nfo_file = os.path.exists(nfo_path)

            # --- START: FINAL, ROBUST FFPROBE LOGIC ---
            is_short = False
            effective_width = 0
            effective_height = 0
            try:
                # Run ffprobe to get width, height, AND rotation from multiple sources
                ffprobe_cmd = [
                    'ffprobe',
                    '-v', 'error',
                    '-select_streams', 'v:0',
                    # Ask for coded dimensions, stream tags, AND side data
                    '-show_entries', 'stream=width,height:stream_tags=rotate:stream_side_data=rotation',
                    '-of', 'json', # Output as JSON
                    video_file_path
                ]
                
                result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, check=True)
                
                # Parse the JSON output
                data = json.loads(result.stdout)
                
                if 'streams' in data and len(data['streams']) > 0:
                    stream = data['streams'][0]
                    coded_width = stream.get('width', 0)
                    coded_height = stream.get('height', 0)
                    
                    rotation = 0
                    try:
                        # Check 1: The 'stream_tags'
                        rotation_str = stream.get('tags', {}).get('rotate', '0')
                        rotation = int(float(rotation_str))
                    except (ValueError, TypeError):
                        rotation = 0 # Tag was missing or "N/A"

                    # Check 2: The 'side_data_list' (This is the most likely place)
                    if rotation == 0:
                        try:
                            # side_data_list is a list, get the first item
                            side_data = stream.get('side_data_list', [{}])[0]
                            rotation_str = side_data.get('rotation', '0')
                            rotation = int(float(rotation_str))
                        except (ValueError, TypeError, IndexError):
                            rotation = 0 # Failed to find it here too
                    
                    effective_width = coded_width
                    effective_height = coded_height

                    # If rotated 90 or 270 degrees, swap the effective dimensions
                    if abs(rotation) == 90 or abs(rotation) == 270:
                        effective_width = coded_height
                        effective_height = coded_width
                    
                    # Final check: is the *displayed* video vertical?
                    if effective_height > effective_width:
                        is_short = True
                    
                    # --- DEBUGGING PRINT ---
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
            # --- END: FINAL, ROBUST FFPROBE LOGIC ---

            # --- START: Find Subtitles ---
            srt_path = None
            srt_label = None
            srt_lang = None

            found_srts = []
            for srt_filename in filenames:
                if not srt_filename.endswith('.srt'):
                    continue
                lang_code = None
                
                # Pattern 1: Matches "My Video.mp4.en.srt"
                if srt_filename.startswith(video_full_filename):
                    suffix = srt_filename[len(video_full_filename):-4] # e.g., ".en"
                    if suffix.startswith('.'):
                        lang_code = suffix[1:] # e.g., "en"
                # Pattern 2: Matches "My Video.en.srt"
                elif srt_filename.startswith(video_base_filename):
                    suffix = srt_filename[len(video_base_filename):-4] # e.g., ".en" or ""
                    if suffix.startswith('.'):
                        lang_code = suffix[1:] # e.g., "en"
                    elif suffix == "":
                        lang_code = "en" # "My Video.srt" (default to en)

                if lang_code:
                    found_srts.append({
                        "lang": lang_code, 
                        "path": os.path.normpath(os.path.join(dirpath, srt_filename))
                    })

            if found_srts:
                best_track = None
                en_track = next((t for t in found_srts if t['lang'] == 'en'), None)
                
                if en_track:
                    best_track = en_track
                    srt_lang = "en"
                    if en_track['path'].endswith('.en.srt'):
                        srt_label = "English"
                    else:
                        srt_label = "CC On" # It was ".srt" or ".mp4.srt"
                else:
                    best_track = found_srts[0]
                    srt_lang = best_track['lang'].split('.')[0] # "es" from "es.forced"
                    srt_label = srt_lang.capitalize()
                
                srt_path = best_track['path']
            # --- END: Find Subtitles ---

            # --- START: Find Thumbnail ---
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
                    if thumbnail_file_path:
                        break
            # --- END: Find Thumbnail ---
            
            # --- END: Find Thumbnail ---

            # --- NEW: Check for a centrally-generated thumbnail ---
            # This gives local thumbnails (MyVideo.jpg) priority,
            # but allows us to find generated ones (a1b2c3d4.jpg).
            if not thumbnail_file_path:
                try:
                    generated_thumb_path = get_thumbnail_path_for_video(video_file_path)
                    if os.path.exists(generated_thumb_path):
                        thumbnail_file_path = generated_thumb_path # We found one!
                except Exception as e:
                    print(f"  - Error checking for generated thumb: {e}")
            # --- END NEW ---

            # --- START: Parse NFO ---
            title = None

            # --- START: Parse NFO ---
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
                        except (ValueError, TypeError):
                            print(f"  - Warning: Could not parse <aired> date '{aired_str}' in {nfo_path}")
                            pass
                except ET.ParseError:
                    print(f"  - Skipping {nfo_path} (XML Parse Error)")
                except Exception as e:
                    print(f"  - Error processing {nfo_path}: {e}")
            
            if not title:
                title = video_base_filename.replace('.', ' ')
            if not show_title:
                current_dir = os.path.dirname(video_file_path)
                relative_path_segment = os.path.relpath(current_dir, video_dir)
                
                if relative_path_segment == '.':
                    show_title = "Unknown Show"
                else:
                    show_title = os.path.basename(relative_path_segment) 
            
            if not aired_date:
                aired_date = uploaded_date # Fallback to file mtime
            if not plot:
                plot = ""
            # --- END: Parse NFO ---

            # --- START: Database Update ---
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
                    
                    existing_video.subtitle_path = srt_path
                    existing_video.subtitle_label = srt_label
                    existing_video.subtitle_lang = srt_lang
                    # Update new fields
                    existing_video.filename = filename
                    existing_video.file_size = file_size_bytes
                    existing_video.file_format = file_format_str
                    existing_video.has_nfo = has_nfo_file
                    existing_video.is_short = is_short # --- ADD THIS ---
                    existing_video.dimensions = f"{effective_width}x{effective_height}" # --- ADD THIS ---
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
                        subtitle_path=srt_path,
                        subtitle_label=srt_label,
                        subtitle_lang=srt_lang,
                        # Add new fields
                        filename=filename,
                        file_size=file_size_bytes,
                        file_format=file_format_str,
                        has_nfo=has_nfo_file,
                        is_short=is_short, # --- ADD THIS ---
                        dimensions=f"{effective_width}x{effective_height}" # --- ADD THIS ---
                    )
                    db.session.add(new_video)
                    added_count += 1

            except Exception as e:
                print(f"  - DB Error processing {video_file_path}: {e}")
                db.session.rollback()
            # --- END: Database Update ---

    if added_count > 0 or updated_count > 0:
        db.session.commit()
    print(f"Scan finished. Added: {added_count}, Updated: {updated_count} videos.")
    return added_count + updated_count


def build_folder_tree(paths):
    """
    Converts a list of relative paths (e.g., ['Movies/Action', 'Movies/Comedy'])
    into a nested dictionary tree structure.
    """
    tree = {}
    for path in paths:
        # Use forward slash for consistency with frontend JS
        path = path.replace('\\', '/') 
        parts = path.split('/')
        if parts == ['.'] or parts == ['']:
            continue
        current_level = tree
        for part in parts:
            if part: 
                current_level = current_level.setdefault(part, {})
    return tree

# --- NEW HELPER FUNCTION ---
def get_thumbnail_path_for_video(video_path):
    """
    Generates a persistent and unique thumbnail path based on a
    hash of the video's full file path.
    """
    # Create a stable MD5 hash of the full video path
    hash_name = hashlib.md5(video_path.encode('utf-8')).hexdigest()
    # Define the central, writable thumbnail directory
    thumb_dir = os.path.join(data_dir, 'thumbnails')
    # Return the full path for the new thumbnail, e.g., /data/thumbnails/a1b2c3d4.jpg
    return os.path.join(thumb_dir, f"{hash_name}.jpg")
# --- END NEW HELPER FUNCTION ---

# --- NEW: Helper function to convert SRT to WebVTT ---
def srt_to_vtt(srt_content):
    """
    Converts SRT content (string) to WebVTT content (string).
    - Replaces comma timestamps with periods.
    - Removes numeric cues.
    - Adds WEBVTT header.
    """
    try:
        lines = srt_content.strip().split('\n')
        vtt_lines = ["WEBVTT", ""]
        
        i = 0
        while i < len(lines):
            # Check if line is a numeric cue index
            if lines[i].isdigit():
                i += 1
                # Check for end of file
                if i >= len(lines):
                    continue

            # Process timestamp (e.g., 00:00:00,440 --> 00:00:07,829)
            if '-->' in lines[i]:
                timestamp = lines[i].replace(',', '.')
                vtt_lines.append(timestamp)
                i += 1
            
            # Process text lines
            while i < len(lines) and lines[i].strip() != '':
                vtt_lines.append(lines[i])
                i += 1
            
            # Add blank line between cues
            vtt_lines.append("")
            i += 1
            
        return "\n".join(vtt_lines)
    except Exception as e:
        print(f"Error converting SRT to VTT: {e}")
        # Fallback: return a valid but empty VTT
        return "WEBVTT\n\n"

# --- NEW --- Background task for generating thumbnails
def _generate_thumbnails_task():
    """
    This function runs in a separate thread to generate missing thumbnails.
    It MUST be run within an app context.
    """
    global THUMBNAIL_STATUS
    print("Background thumbnail generation task started...")
    sys.stdout.flush() 
    with app.app_context(): # Need app context to access db
        generated_count = 0 # --- MOVED COUNT UP HERE ---
        try:
            # --- NEW: Define a writable thumbnail directory ---
            thumb_dir = os.path.join(data_dir, 'thumbnails')
            os.makedirs(thumb_dir, exist_ok=True) # Create it if it doesn't exist
            # --- END NEW ---

            # Find all videos where thumbnail_path is NULL
            videos_to_process = Video.query.filter(Video.thumbnail_path == None).all()
            print(f"Found {len(videos_to_process)} videos needing thumbnails.")
            sys.stdout.flush() 

            # --- NEW: Update status for the poller ---
            THUMBNAIL_STATUS.update({
                "status": "generating",
                "message": f"Found {len(videos_to_process)} videos to process.",
                "progress": 0,
                "total": len(videos_to_process)
            })
            # --- END NEW ---
            
            for i, video in enumerate(videos_to_process):
                
                # --- NEW: Update progress ---
                THUMBNAIL_STATUS["progress"] = i
                # --- END NEW ---

                try:
                    video_path = video.video_path
                    if not os.path.exists(video_path):
                        print(f"  - Skipping {video.filename} (source file not found at {video_path})")
                        sys.stdout.flush() 
                        continue

                    # --- NEW: Create a safe, writable path in the /data directory ---
                    # e.g., /data/thumbnails/a1b2c3d4.jpg
                    new_thumb_path = get_thumbnail_path_for_video(video.video_path)
                    # --- END NEW ---

                    # --- FINAL FIX: Pipe output from ffmpeg, write with Python ---
                    result = subprocess.run([
                        "ffmpeg",
                        "-i", video_path,
                        "-ss", "00:00:10", # Seek 10s in
                        "-vframes", "1",
                        "-q:v", "2",
                        "-f", "image2pipe", # Format as an image pipe
                        "pipe:1"            # Output to stdout
                    ], 
                    check=True, 
                    capture_output=True # Capture stdout (binary) and stderr
                    )
                    # --- END FIX ---

                    # If ffmpeg succeeded, result.stdout will have the binary image data
                    if result.stdout:
                        # Use Python's open() to write the file to the NEW path
                        with open(new_thumb_path, "wb") as f:
                            f.write(result.stdout)
                        
                        # Verify the file was written by Python
                        if os.path.exists(new_thumb_path):
                            video.thumbnail_path = new_thumb_path # Save the new path
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
                    # Decode stderr from bytes to string for printing
                    stderr_output = e.stderr.decode('utf-8', errors='ignore') 
                    print(f"  - FFmpeg error for {video.filename}: {e}")
                    print(f"  - FFmpeg STDERR: {stderr_output}") 
                    sys.stdout.flush() 
                except Exception as e:
                    print(f"  - General error processing {video.filename}: {e}")
                    sys.stdout.flush() 
                    db.session.rollback() # Rollback this single video's change
                
                # --- NEW: Commit in batches of 50 ---
                # This allows the frontend to see progress as it polls.
                if generated_count > 0 and generated_count % 50 == 0:
                    print(f"  - Committing batch of 50 to database...")
                    sys.stdout.flush()
                    db.session.commit()
                # --- END NEW ---
            
            # --- MODIFIED: Commit any remaining items at the end ---
            if generated_count > 0:
                print("  - Committing final batch to database...")
                sys.stdout.flush()
                db.session.commit() # Commit all successful updates at the end
            # --- END MODIFIED ---

            print(f"Thumbnail generation task finished. Generated {generated_count} new thumbnails.")
            sys.stdout.flush() 

        except Exception as e:
            print(f"Fatal error in thumbnail task: {e}")
            sys.stdout.flush() 
            db.session.rollback()
            # --- NEW: Report error status ---
            THUMBNAIL_STATUS.update({
                "status": "error",
                "message": str(e),
                "progress": 0,
                "total": 0
            })
            # --- END NEW ---
        finally:
            # CRITICAL: Release the lock so the task can be run again later
            thumbnail_generation_lock.release()
            print("Thumbnail lock released.")
            sys.stdout.flush()
            # --- NEW: Set status to idle ---
            if THUMBNAIL_STATUS["status"] != "error":
                THUMBNAIL_STATUS.update({
                    "status": "idle",
                    "message": f"Successfully generated {generated_count} thumbnails.",
                    "progress": 0,
                    "total": 0
                })
            # --- END NEW ---


## --- Initialization Function ---
def initialize_database():
    """Creates all database tables and runs initial scan if empty."""
    with app.app_context():
        print("Initializing database...")
        db.create_all()
        video_count = Video.query.count()
        if video_count == 0:
            print("No videos found in database. Running initial scan...")
            scan_videos()
        else:
            print(f"Database already contains {video_count} videos.")
        print("Database initialization complete.")

# Run initialization logic *outside* the __name__ == '__main__' block
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
        'categories': [], 
        'feeds': [],
        'removedFeeds': [],
        'customStreams': [],
        'removedStreams': [],
        'customStreamFeedLinks': [],
        'articles': video_dtos, 
        'folder_tree': folder_tree,
        'smartPlayLists': playlist_dtos
    })

## --- API: Playlist Management ---

@app.route('/api/playlist/create', methods=['POST'])
def create_playlist():
    """Creates a new smart playlist."""
    data = request.get_json()
    name = data.get('name')
    if not name:
        return jsonify({"error": "Playlist name is required"}), 400
    
    new_playlist = SmartPlaylist(name=name.strip())
    db.session.add(new_playlist)
    db.session.commit()
    
    return jsonify(new_playlist.to_dict()), 201

# IMPLEMENTED: Delete Playlist
@app.route('/api/playlist/<int:playlist_id>/delete', methods=['POST'])
def delete_playlist(playlist_id):
    """Deletes a smart playlist by ID."""
    playlist = SmartPlaylist.query.get_or_404(playlist_id)
    
    db.session.delete(playlist)
    db.session.commit()
    
    return jsonify({'success': True}), 200

@app.route('/api/playlist/<int:playlist_id>/filter', methods=['POST'])
def add_playlist_filter(playlist_id):
    
    """Adds a new filter object to a smart playlist, checking for duplicates."""
    playlist = SmartPlaylist.query.get_or_404(playlist_id)
    data = request.get_json()
    new_filter = data.get('filter')
    
    if not new_filter or not isinstance(new_filter, dict) or not new_filter.get('value'):
        return jsonify({"error": "Valid filter object required"}), 400
    
    try:
        # 1. Load existing filters
        filters_list = json.loads(playlist.filters) if playlist.filters else []
        
        # 2. --- UPDATED DUPLICATE CHECK ---
        new_value = new_filter.get('value')
        new_type = new_filter.get('type')
        found_duplicate = False

        for f in filters_list:
            existing_value = f.get('value')
            existing_type = f.get('type')
            
            # Only compare filters of the same type
            if existing_type != new_type:
                continue

            # Compare values based on their type (string vs list)
            is_match = False
            if isinstance(new_value, str) and isinstance(existing_value, str):
                is_match = new_value.lower().strip() == existing_value.lower().strip()
            elif isinstance(new_value, list) and isinstance(existing_value, list):
                # Use sets for order-insensitive comparison of lists
                is_match = set(new_value) == set(existing_value)
            
            if is_match:
                found_duplicate = True
                break
        
        # 3. If a-duplicate is found, just return the playlist as-is.
        if found_duplicate:
            return jsonify(playlist.to_dict()), 200

        # 4. No duplicate found, so append the new filter
        filters_list.append(new_filter)
        
        # 5. Save back to the database
        playlist.filters = json.dumps(filters_list)
        db.session.commit()
        
        return jsonify(playlist.to_dict()), 200
        
    except json.JSONDecodeError:
        return jsonify({"error": "Failed to decode existing playlist filters"}), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# --- ADDED FUNCTIONS ---
# (Make sure these start with NO indentation)

@app.route('/api/playlist/<int:playlist_id>/rename', methods=['POST'])
def rename_playlist(playlist_id):
    """Renames a smart playlist."""
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
    """Removes a specific filter object from a smart playlist."""
    playlist = SmartPlaylist.query.get_or_404(playlist_id)
    data = request.get_json()
    filter_id_to_remove = data.get('filterId')
    
    if not filter_id_to_remove:
        return jsonify({"error": "Valid filterId required"}), 400
    
    try:
        # 1. Load existing filters
        filters_list = json.loads(playlist.filters) if playlist.filters else []
        
        # 2. Create a new list *without* the filter to be removed
        new_filters_list = [f for f in filters_list if f.get('id') != filter_id_to_remove]
        
        # 3. Save back to the database
        playlist.filters = json.dumps(new_filters_list)
        db.session.commit()
        
        return jsonify(playlist.to_dict()), 200
        
    except json.JSONDecodeError:
        return jsonify({"error": "Failed to decode existing playlist filters"}), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

## --- API: Video/Thumbnail Serving ---

@app.route('/api/video/<int:video_id>')
def stream_video(video_id):
    """Streams the video file."""
    video = Video.query.get_or_404(video_id)
    if not os.path.exists(video.video_path):
        return jsonify({"error": "Video file not found"}), 404
    mimetype = mimetypes.guess_type(video.video_path)[0] or 'video/mp4'
    video_dir_path = os.path.dirname(video.video_path)
    video_filename = os.path.basename(video.video_path)
    return send_from_directory(video_dir_path, video_filename, as_attachment=False, mimetype=mimetype)


@app.route('/api/thumbnail/<int:video_id>')
def get_thumbnail(video_id):
    """Serves the thumbnail file."""
    video = Video.query.get_or_404(video_id)
    if not video.thumbnail_path or not os.path.exists(video.thumbnail_path):
        return jsonify({"error": "Thumbnail not found"}), 404
    thumb_dir = os.path.dirname(video.thumbnail_path)
    thumb_filename = os.path.basename(video.thumbnail_path)
    mimetype = mimetypes.guess_type(video.thumbnail_path)[0] or 'image/jpeg'
    return send_from_directory(thumb_dir, thumb_filename, as_attachment=False, mimetype=mimetype)

# --- UPDATED: API Route for Subtitles ---
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
        # Try reading as UTF-8 first
        with open(video.subtitle_path, 'r', encoding='utf-8') as f:
            srt_content = f.read()
    except UnicodeDecodeError:
        try:
            # Fallback to latin-1 (common for older SRT files)
            with open(video.subtitle_path, 'r', encoding='latin-1') as f:
                srt_content = f.read()
        except Exception as e:
            print(f"Failed to read subtitle file {video.subtitle_path}: {e}")
            return jsonify({"error": "Could not read subtitle file"}), 500
    except Exception as e:
        print(f"Failed to read subtitle file {video.subtitle_path}: {e}")
        return jsonify({"error": "Could not read subtitle file"}), 500

    # Convert the SRT content to VTT
    vtt_content = srt_to_vtt(srt_content)
    
    # Create a Flask Response object
    response = Response(vtt_content, mimetype='text/vtt; charset=utf-8')
    
    # ADDED: Add CORS header to allow the video player to load it
    response.headers['Access-Control-Allow-Origin'] = '*'
    
    return response


## --- API: Video Actions (Favorites/Watch Later/Progress) ---

@app.route('/api/article/<int:article_id>/favorite', methods=['POST'])
def toggle_favorite(article_id):
    """Toggles the 'is_favorite' status of a video."""
    video = Video.query.get_or_404(article_id)
    video.is_favorite = not video.is_favorite
    db.session.commit()
    return jsonify({'is_favorite': video.is_favorite})

@app.route('/api/article/<int:article_id>/bookmark', methods=['POST'])
def toggle_watch_later(article_id):
    """Toggles the 'is_watch_later' status of a video."""
    video = Video.query.get_or_404(article_id)
    video.is_watch_later = not video.is_watch_later
    db.session.commit()
    return jsonify({'is_read_later': video.is_watch_later})

@app.route('/api/video/<int:video_id>/progress', methods=['POST'])
def update_video_progress(video_id):
    """
    Updates the watched duration and last watched timestamp for a video.
    Requires duration_watched in the JSON body.
    """
    video = Video.query.get_or_404(video_id)
    data = request.get_json()
    
    try:
        duration_watched = int(data.get('duration_watched', 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid duration_watched format"}), 400
    
    # Check the 4-second minimum requirement
    if duration_watched >= 4:
        # For simplicity, we only track the last recorded duration
        video.watched_duration = duration_watched 
        video.last_watched = datetime.datetime.now()
        db.session.commit()
    
    return jsonify({
        'success': True, 
        'watched_duration': video.watched_duration, 
        'last_watched': video.last_watched.isoformat() if video.last_watched else None
    })

## --- API: Scan ---

@app.route('/api/scan_videos', methods=['POST'])
def scan_videos_route():
    """
    API endpoint to trigger a full video scan.
    """
    print("API: Received scan request.")
    try:
        count = scan_videos()
        print(f"Scan complete. Found/updated {count} videos.")
        return jsonify({'success': True, 'videos_found': count})
    except Exception as e:
        print(f"Error during scan: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

# --- NEW --- API to generate missing thumbnails in the background
@app.route('/api/thumbnails/generate_missing', methods=['POST'])
def generate_missing_thumbnails_route():
    """
    Triggers a background task to generate missing thumbnails.
    Returns immediately and does not wait for the task to finish.
    """
    global THUMBNAIL_STATUS
    
    # Check if the task is already running by trying to acquire the lock
    if not thumbnail_generation_lock.acquire(blocking=False):
        print("API: Thumbnail generation already in progress.")
        sys.stdout.flush() 
        # 200 OK: Tell the frontend "it's already running, start polling"
        return jsonify({"message": "Thumbnail generation is already in progress."}), 200
    
    # If lock is acquired, start the background thread
    try:
        print("API: Starting background thumbnail generation thread...")
        sys.stdout.flush()
        
        # --- NEW: Reset status for a new job ---
        THUMBNAIL_STATUS.update({
            "status": "starting",
            "message": "Initializing task...",
            "progress": 0,
            "total": 0
        })
        # --- END NEW ---

        thread = threading.Thread(target=_generate_thumbnails_task)
        thread.start()
        
        # 202 Accepted: The request has been accepted for processing.
        return jsonify({"message": "Thumbnail generation started in background."}), 202
    except Exception as e:
        # If starting the thread fails, release the lock
        thumbnail_generation_lock.release()
        print(f"API: Failed to start background thumbnail task: {str(e)}")
        sys.stdout.flush() 
        THUMBNAIL_STATUS.update({"status": "error", "message": str(e)})
        return jsonify({"error": f"Failed to start background task: {str(e)}"}), 500

@app.route('/api/thumbnails/status', methods=['GET'])
def get_thumbnail_status():
    """
    Returns the current status of the thumbnail generation task.
    """
    global THUMBNAIL_STATUS
    return jsonify(THUMBNAIL_STATUS)


## --- Main Execution ---

if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)