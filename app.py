## --- Imports ---
import os
import datetime
import xml.etree.ElementTree as ET # For parsing NFO files
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
import mimetypes

## --- App Setup ---
basedir = os.path.abspath(os.path.dirname(__file__))
# Use DATA_DIR env var for persistent storage, default to app directory
data_dir = os.environ.get('DATA_DIR', basedir)
db_path = os.path.join(data_dir, "app.db")

# VIDEO_DIR will be our new env var for the video library
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

## --- Database Models ---

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)           # From NFO <title> or filename
    show_title = db.Column(db.String(200))                     # From NFO <showtitle> or folder name
    summary = db.Column(db.Text)                               # From NFO <plot>
    video_path = db.Column(db.String(1000), unique=True, nullable=False) # Full path to the video file
    thumbnail_path = db.Column(db.String(1000))                # Full path to the thumbnail file
    aired = db.Column(db.DateTime(timezone=False))             # From NFO <aired>
    uploaded_date = db.Column(db.DateTime(timezone=False))     # NEW: From file mtime
    is_favorite = db.Column(db.Boolean, default=False)
    is_watch_later = db.Column(db.Boolean, default=False)

    def to_dict(self):
        """
        Serializes the Video object to a dictionary, mapping to old 'Article' keys
        to minimize frontend changes.
        """
        # Calculate relative path for folder view
        try:
            # Ensure video_path is a string
            if not isinstance(self.video_path, str):
                self.video_path = str(self.video_path)
            
            # Use os.path.normpath to ensure paths are clean
            norm_video_path = os.path.normpath(self.video_path)
            norm_base_dir = os.path.normpath(video_dir)
            
            relative_dir = os.path.relpath(os.path.dirname(norm_video_path), norm_base_dir)
            # Normalize to use forward slashes for consistency in JS/Python
            relative_dir = relative_dir.replace(os.sep, '/')
        except ValueError:
            # This can happen if paths are on different drives (windows)
            relative_dir = '.' 
        except TypeError:
            # This can happen if self.video_path is None or not a path-like object
            print(f"Error processing path for video ID {self.id}: {self.video_path}")
            relative_dir = '.'
            
        return {
            'id': self.id,
            'title': self.title,
            'summary': self.summary,
            'author': self.show_title or 'Unknown Show',  # Map show_title -> author
            # 'published' is the NFO <aired> date
            'published': self.aired.isoformat() if self.aired else (self.uploaded_date.isoformat() if self.uploaded_date else datetime.datetime.now().isoformat()),
            # 'uploaded' is the file modification time
            'uploaded': self.uploaded_date.isoformat() if self.uploaded_date else datetime.datetime.now().isoformat(),
            'is_favorite': self.is_favorite,
            'is_read_later': self.is_watch_later, # Map is_watch_later -> is_read_later
            
            'video_url': f'/api/video/{self.id}',
            'image_url': f'/api/thumbnail/{self.id}' if self.thumbnail_path else None,
            
            'feed_title': self.show_title or 'Local Media',
            'feed_id': self.id, 
            'link': f'/api/video/{self.id}',
            
            'relative_path': relative_dir
        }

## --- Helper Functions ---
def scan_videos():
    """
    Scans the VIDEO_DIR for video files, *then* looks for optional
    .nfo and thumbnail files. Creates fallbacks if metadata is missing.
    """
    print(f"Starting scan of: {video_dir}")
    added_count = 0
    updated_count = 0
    
    # Supported video and image extensions
    video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm']
    image_extensions = ['.jpg', '.jpeg', '.png', '.tbn']

    # Use os.walk to go through all subdirectories
    for dirpath, dirnames, filenames in os.walk(video_dir, topdown=True):
        for filename in filenames:
            # 1. Find a video file first
            file_ext = os.path.splitext(filename)[1].lower()
            if file_ext not in video_extensions:
                continue

            base_filename = os.path.splitext(filename)[0]
            video_file_path = os.path.normpath(os.path.join(dirpath, filename))
            
            # 2. Find matching NFO file (optional)
            nfo_path = os.path.normpath(os.path.join(dirpath, base_filename + '.nfo'))
            
            # 3. Find matching Thumbnail file (optional)
            thumbnail_file_path = None
            for img_ext in image_extensions:
                potential_thumb = os.path.normpath(os.path.join(dirpath, base_filename + img_ext))
                if os.path.exists(potential_thumb):
                    thumbnail_file_path = potential_thumb
                    break
            
            if not thumbnail_file_path:
                for suffix in ['-thumb', ' thumbnail', ' folder']:
                    for img_ext in image_extensions:
                        potential_thumb = os.path.normpath(os.path.join(dirpath, base_filename + suffix + img_ext))
                        if os.path.exists(potential_thumb):
                            thumbnail_file_path = potential_thumb
                            break
                    if thumbnail_file_path:
                        break

            # 4. Parse NFO or create default metadata
            title = None
            show_title = None
            plot = None
            aired_date = None
            uploaded_date = None # NEW

            if os.path.exists(nfo_path):
                # Try to parse NFO
                try:
                    tree = ET.parse(nfo_path)
                    root = tree.getroot()
                    title = root.findtext('title')
                    show_title = root.findtext('showtitle')
                    plot = root.findtext('plot') # <plot> is standard for summary
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
            
            # 5. Fill in missing metadata with fallbacks
            if not title:
                title = base_filename.replace('.', ' ') # Use filename
            if not show_title:
                show_title = os.path.basename(os.path.dirname(video_file_path))
                if os.path.normpath(os.path.dirname(video_file_path)) == video_dir:
                    show_title = "Unknown Show"
            
            # Get file modification time as 'uploaded_date'
            try:
                mtime = os.path.getmtime(video_file_path)
                uploaded_date = datetime.datetime.fromtimestamp(mtime)
            except OSError:
                pass # Fallback to None

            if not aired_date:
                aired_date = uploaded_date # Fallback: <aired> date is file mtime

            if not plot:
                plot = "" # Default to empty string

            # 6. Add or Update the database
            try:
                existing_video = Video.query.filter_by(video_path=video_file_path).first()
                
                if existing_video:
                    # Update existing entry
                    existing_video.title = title
                    existing_video.show_title = show_title
                    existing_video.summary = plot
                    existing_video.aired = aired_date
                    existing_video.uploaded_date = uploaded_date # NEW
                    existing_video.thumbnail_path = thumbnail_file_path
                    updated_count += 1
                else:
                    # Create new entry
                    new_video = Video(
                        title=title,
                        show_title=show_title,
                        summary=plot,
                        aired=aired_date,
                        uploaded_date=uploaded_date, # NEW
                        video_path=video_file_path,
                        thumbnail_path=thumbnail_file_path
                    )
                    db.session.add(new_video)
                    added_count += 1

            except Exception as e:
                print(f"  - DB Error processing {video_file_path}: {e}")
                db.session.rollback()

    # Commit all changes after the loop
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
        parts = path.split('/')
        
        # Handle the root directory case
        if parts == ['.']:
            continue
            
        current_level = tree
        for part in parts:
            if part: # Avoid empty strings
                current_level = current_level.setdefault(part, {})
    return tree


## --- Initialization Function ---
def initialize_database():
    """Creates all database tables and runs initial scan if empty."""
    with app.app_context():
        print("Initializing database...")
        db.create_all()
        
        # Check if the database is empty
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
    """Returns all video data and the folder tree as a JSON object."""
    videos = Video.query.order_by(Video.aired.desc()).all()
    
    video_dtos = [v.to_dict() for v in videos]
    
    # We use a set to automatically get unique folder paths
    relative_paths = set(v['relative_path'] for v in video_dtos if v['relative_path'] != '.')
    
    # --- DEBUGGING: Print the paths we're about to build a tree from ---
    print(f"DEBUG: Relative paths for tree: {relative_paths}")
    
    # Build the nested dictionary tree
    folder_tree = build_folder_tree(relative_paths)

    # --- DEBUGGING: Print the final tree ---
    print(f"DEBUG: Built folder tree: {folder_tree}")
    
    return jsonify({
        'categories': [], 
        'feeds': [],
        'removedFeeds': [],
        'customStreams': [],
        'removedStreams': [],
        'customStreamFeedLinks': [],
        'articles': video_dtos, 
        'folder_tree': folder_tree
    })

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


## --- API: Video Actions ---

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


## --- Main Execution ---

if __name__ == '__main__':
    # This block only runs when you execute `python app.py` directly
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)

