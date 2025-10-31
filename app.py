## --- Imports ---
import os
import datetime
import xml.etree.ElementTree as ET # For parsing NFO files
from flask import Flask, render_template, request, jsonify, send_from_directory # Updated
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func

## --- App Setup ---
basedir = os.path.abspath(os.path.dirname(__file__))
# Use DATA_DIR env var for persistent storage, default to app directory
data_dir = os.environ.get('DATA_DIR', basedir)
db_path = os.path.join(data_dir, "app.db")

# VIDEO_DIR will be our new env var for the video library
video_dir = os.environ.get('VIDEO_DIR', os.path.join(basedir, "videos")) 
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
    title = db.Column(db.String(300), nullable=False)           # From NFO <title>
    show_title = db.Column(db.String(200))                     # From NFO <showtitle> (will be our 'author')
    summary = db.Column(db.Text)                               # From NFO <plot>
    video_path = db.Column(db.String(1000), unique=True, nullable=False) # Full path to the video file
    thumbnail_path = db.Column(db.String(1000))                # Full path to the thumbnail file
    aired = db.Column(db.DateTime(timezone=False))             # From NFO <aired> (for sorting)
    is_favorite = db.Column(db.Boolean, default=False)
    is_watch_later = db.Column(db.Boolean, default=False)      # Renamed from 'is_read_later'

    def to_dict(self):
        """
        Serializes the Video object to a dictionary, mapping to old 'Article' keys
        to minimize frontend changes.
        """
        return {
            'id': self.id,
            'title': self.title,
            'summary': self.summary,
            'author': self.show_title or 'Unknown Show',  # Map show_title -> author
            'published': self.aired.isoformat() if self.aired else datetime.datetime.now().isoformat(), # Map aired -> published
            'is_favorite': self.is_favorite,
            'is_read_later': self.is_watch_later, # Map is_watch_later -> is_read_later
            
            # These are new keys we'll use on the frontend later
            'video_url': f'/api/video/{self.id}',
            'image_url': f'/api/thumbnail/{self.id}' if self.thumbnail_path else None,
            
            # We add these just for consistency, though we won't use them
            'feed_title': self.show_title or 'Local Media',
            'feed_id': self.id, # Just needs a unique value
            'link': f'/api/video/{self.id}' # Point to our own player
        }

## --- Helper Functions ---
# This is our new Video Scanner
def scan_videos():
    """
    Scans the VIDEO_DIR for .nfo files, parses them, finds matching
    video/thumbnail files, and updates the database.
    """
    print(f"Starting scan of: {video_dir}")
    added_count = 0
    video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.wmv']
    thumb_suffixes = ['-thumb.jpg', '-thumb.png', '.jpg', '.png'] # Order matters

    # Use os.walk to go through all subdirectories
    for dirpath, dirnames, filenames in os.walk(video_dir):
        for filename in filenames:
            if not filename.endswith('.nfo'):
                continue
            
            base_filename = os.path.splitext(filename)[0]
            nfo_path = os.path.join(dirpath, filename)
            
            # 1. Find the matching video file
            video_file_path = None
            for ext in video_extensions:
                potential_video = os.path.join(dirpath, base_filename + ext)
                if os.path.exists(potential_video):
                    video_file_path = potential_video
                    break
            
            if not video_file_path:
                print(f"  - Skipping {nfo_path} (no matching video file found)")
                continue

            # 2. Find the matching thumbnail (optional)
            thumbnail_file_path = None
            for suffix in thumb_suffixes:
                potential_thumb = os.path.join(dirpath, base_filename + suffix)
                if os.path.exists(potential_thumb):
                    thumbnail_file_path = potential_thumb
                    break
            
            # 3. Parse the NFO file
            try:
                tree = ET.parse(nfo_path)
                root = tree.getroot()
                
                title = root.findtext('title')
                if not title:
                    print(f"  - Skipping {nfo_path} (NFO missing <title>)")
                    continue

                show_title = root.findtext('showtitle')
                plot = root.findtext('plot') # <plot> is standard for summary
                aired_str = root.findtext('aired')
                
                # Parse the aired date
                aired_date = None
                if aired_str:
                    try:
                        aired_date = datetime.datetime.strptime(aired_str, '%Y-%m-%d')
                    except (ValueError, TypeError):
                        print(f"  - Warning: Could not parse <aired> date '{aired_str}' in {nfo_path}")
                        pass
                
                # 4. Add or Update the database
                existing_video = Video.query.filter_by(video_path=video_file_path).first()
                
                if existing_video:
                    # Update existing entry
                    existing_video.title = title
                    existing_video.show_title = show_title
                    existing_video.summary = plot
                    existing_video.aired = aired_date
                    existing_video.thumbnail_path = thumbnail_file_path
                else:
                    # Create new entry
                    new_video = Video(
                        title=title,
                        show_title=show_title,
                        summary=plot,
                        aired=aired_date,
                        video_path=video_file_path,
                        thumbnail_path=thumbnail_file_path
                    )
                    db.session.add(new_video)
                
                added_count += 1

            except ET.ParseError:
                print(f"  - Skipping {nfo_path} (XML Parse Error)")
            except Exception as e:
                print(f"  - Error processing {nfo_path}: {e}")
                db.session.rollback()

    # Commit all changes after the loop
    if added_count > 0:
        db.session.commit()
    print(f"Scan finished. Added/updated {added_count} videos.")
    return added_count


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

## --- Main Routes ---

@app.route('/')
def home():
    """Serves the main index.html template."""
    return render_template('index.html')

## --- API: Get All Data ---

@app.route('/api/data')
def get_data():
    """Returns all video data as a JSON object."""
    # This replaces the complex /api/data endpoint.
    # We now just query the Video table.
    videos = Video.query.order_by(Video.aired.desc()).all()
    
    # We return it in the *same structure* as the old app
    # to make the frontend transition easier.
    # In Step 3, we will add the 'folder_tree' here.
    return jsonify({
        'categories': [], # Send empty lists for old, unused models
        'feeds': [],
        'removedFeeds': [],
        'customStreams': [],
        'removedStreams': [],
        'customStreamFeedLinks': [],
        'articles': [v.to_dict() for v in videos] # Our videos go where 'articles' used to be
        # 'folder_tree': {} # We will add this in the next step
    })

## --- API: Video/Thumbnail Serving ---
# These are new endpoints to serve the media files

@app.route('/api/video/<int:video_id>')
def stream_video(video_id):
    """Streams the video file."""
    video = Video.query.get_or_404(video_id)
    if not os.path.exists(video.video_path):
        return jsonify({"error": "Video file not found"}), 404
    # send_from_directory is safer as it handles pathing
    video_dir = os.path.dirname(video.video_path)
    video_filename = os.path.basename(video.video_path)
    return send_from_directory(video_dir, video_filename, as_attachment=False)

@app.route('/api/thumbnail/<int:video_id>')
def get_thumbnail(video_id):
    """Serves the thumbnail file."""
    video = Video.query.get_or_404(video_id)
    if not video.thumbnail_path or not os.path.exists(video.thumbnail_path):
        # Optional: return a default placeholder image
        return jsonify({"error": "Thumbnail not found"}), 404
    thumb_dir = os.path.dirname(video.thumbnail_path)
    thumb_filename = os.path.basename(video.thumbnail_path)
    return send_from_directory(thumb_dir, thumb_filename, as_attachment=False)


## --- API: Video Actions ---
# These are the updated 'Article' actions, now for 'Video'

@app.route('/api/article/<int:article_id>/favorite', methods=['POST'])
def toggle_favorite(article_id):
    """Toggles the 'is_favorite' status of a video."""
    # Note: The frontend will still call /api/article/...
    # We just know it's a video ID.
    video = Video.query.get_or_404(article_id)
    video.is_favorite = not video.is_favorite
    db.session.commit()
    return jsonify({'is_favorite': video.is_favorite})

@app.route('/api/article/<int:article_id>/bookmark', methods=['POST'])
def toggle_watch_later(article_id):
    """Toggles the 'is_watch_later' status of a video."""
    # Renamed from toggle_bookmark
    video = Video.query.get_or_404(article_id)
    video.is_watch_later = not video.is_watch_later
    db.session.commit()
    # Frontend expects 'is_read_later', so we send it back with that key
    return jsonify({'is_read_later': video.is_watch_later})

## --- API: Scan ---
# New endpoint to trigger a library scan

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
    # Ensure the data directory exists
    if 'DATA_DIR' in os.environ and not os.path.exists(data_dir):
        print(f"Creating data directory: {data_dir}")
        os.makedirs(data_dir)
        
    initialize_database()
    
    # Use FLASK_DEBUG env var to control debug mode
    debug_mode = os.environ.get('FLASK_DEBUG') == '1'
    app.run(debug=debug=debug_mode, host='0.0.0.0', port=5000)