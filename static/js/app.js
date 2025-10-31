/**
 * Main Alpine.js component for the VolumePlay video application.
 */
function videoApp() {
    return {
        // --- State Variables ---
        isMobileMenuOpen: false,
        isModalOpen: false,
        isScanning: false,         // Replaces isRefreshing
        openFolderPaths: [],       // New: Tracks open folders
        currentView: { type: 'all', id: null, author: null },
        currentTitle: 'All Videos',
        modalVideo: null,          // Replaces modalArticle
        searchQuery: '',
        sortOrder: 'newest',
        appData: {
            videos: [],            // Renamed from articles
            folder_tree: {}      // New: For the folder sidebar
        },
        videosToShow: 75,          // Renamed from articlesToShow

        // --- Init ---
        init() {
            this.fetchData();
            // Removed all drag-drop listeners and auto-refresh timer
        },
        
        async fetchData() {
            try {
                const response = await fetch('/api/data');
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                const data = await response.json();
                
                // Map 'articles' (from API) to 'videos' (for our app)
                this.appData.videos = data.articles || []; 
                this.appData.folder_tree = data.folder_tree || {};

                if (!this.currentTitle || this.currentTitle === 'All Videos') {
                    this.setView('all');
                }
            } catch (e) {
                console.error('Error fetching data:', e);
                this.appData = { videos: [], folder_tree: {} };
            }
        },

        // --- Computed Properties (Getters) ---
        get fullFilteredList() {
            let videos = [];
            const viewType = this.currentView.type;
            const viewId = this.currentView.id;
            const viewAuthor = this.currentView.author;

            if (viewType === 'all') {
                videos = this.appData.videos;
            } else if (viewType === 'favorites') {
                videos = this.appData.videos.filter(v => v.is_favorite);
            } else if (viewType === 'watchLater') { // Renamed from readLater
                // API maps is_watch_later -> is_read_later for compatibility
                videos = this.appData.videos.filter(v => v.is_read_later); 
            } else if (viewType === 'history') { // NEW: History View
                // Filter for videos watched for 4 seconds or more
                videos = this.appData.videos.filter(v => v.watched_duration >= 4);
            } else if (viewType === 'author') {
                videos = this.appData.videos.filter(v => v.author && v.author === viewAuthor);
            } else if (viewType === 'folder') { // New View Type
                const path = this.currentView.id;
                videos = this.appData.videos.filter(v => 
                    // Match the folder OR any subfolder
                    // Use forward slash for consistency
                    v.relative_path === path || 
                    (v.relative_path && v.relative_path.startsWith(path + '/'))
                );
            }
            // Removed feed, custom_stream, category views

            if (this.searchQuery.trim() !== '') {
                const query = this.searchQuery.toLowerCase();
                videos = videos.filter(v =>
                    (v.title && v.title.toLowerCase().includes(query)) ||
                    (v.summary && v.summary.toLowerCase().includes(query)) ||
                    (v.author && v.author.toLowerCase().includes(query))
                );
            }

            videos.sort((a, b) => {
                // NEW: History View always sorts by last_watched (most recent first)
                if (viewType === 'history') {
                    // Use a fallback of 0 for videos without a last_watched date
                    const dateA = a.last_watched ? new Date(a.last_watched) : 0;
                    const dateB = b.last_watched ? new Date(b.last_watched) : 0;
                    return dateB - dateA;
                }
                
                // Default sort (for all other views)
                // API maps aired -> published
                const dateA = a.published ? new Date(a.published) : 0; 
                const dateB = b.published ? new Date(b.published) : 0;
                return this.sortOrder === 'newest' ? dateB - dateA : dateA - dateB;
            });
            
            return videos;
        },

        get filteredVideos() {
            // Renamed from filteredArticles
            return this.fullFilteredList.slice(0, this.videosToShow);
        },

        getEmptyMessage() {
            if (this.isScanning && this.appData.videos.length === 0) return 'Scanning library...';
            if (this.searchQuery.trim() !== '') return 'No videos match your search.';
            if (!this.appData.videos || this.appData.videos.length === 0) {
                 return 'No videos found. Click the refresh icon to scan your library.';
            }
            if (this.currentView.type === 'author') return `No videos found for: ${this.currentView.author || 'Unknown'}.`;
            if (this.currentView.type === 'folder') return 'No videos found in this folder.';
            if (this.currentView.type === 'history') return 'No videos in your history yet.'; // NEW
            if (this.fullFilteredList.length === 0) return 'No videos found for this view.';
            return 'No videos found.'; // Fallback
        },

        // --- UI Actions ---
        setView(type, id = null, author = null) {
            this.currentView = { type: type, id: id, author: author };
            this.updateTitle();
            this.isMobileMenuOpen = false;
            this.videosToShow = 75; // Reset count
        },

        updateTitle() {
            const { type, id, author } = this.currentView;
            if (type === 'all') { this.currentTitle = 'All Videos'; }
            else if (type === 'favorites') { this.currentTitle = 'Favorites'; }
            else if (type === 'watchLater') { this.currentTitle = 'Watch Later'; } // Renamed
            else if (type === 'history') { this.currentTitle = 'History'; } // NEW
            else if (type === 'author') { this.currentTitle = `Author: ${author || 'Unknown'}`; }
            else if (type === 'folder') { this.currentTitle = `Folder: ${id.split('/').pop() || '...'}`; } // Show last part of path
            else { this.currentTitle = 'All Videos'; }
            // Removed feed, stream, category titles
        },

        loadMoreVideos() {
            // Renamed from loadMoreArticles
            this.videosToShow += 75;
        },

        openModal(video) {
            this.modalVideo = video;
            this.isModalOpen = true;
        },

        closeModal() {
            // CRITICAL: Stop video playback and save progress
            if (this.modalVideo && this.$refs.videoPlayer) {
                const videoElement = this.$refs.videoPlayer;
                const durationWatched = videoElement.currentTime;

                // Stop video playback (existing logic)
                videoElement.pause();
                videoElement.src = ''; 
                
                // Save the progress if watched for 4 or more seconds
                this.updateVideoProgress(this.modalVideo, durationWatched);
            }
            
            this.isModalOpen = false;
            this.modalVideo = null;
        },

        // --- Content Rendering ---
        
        /**
         * Converts HTML entities back to characters (e.g., &amp;#39; -> ').
         * FIX for double-encoding issue.
         */
        unescapeHTML(text) {
            if (!text) return '';
            // Create a temp element to leverage the browser's unescaping logic
            const doc = new DOMParser().parseFromString(text, 'text/html');
            return doc.documentElement.textContent;
        },
        
        // Safety function to escape HTML special characters
        escapeHTML(text) {
            if (!text) return '';
            return text.toString()
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        },

        formatVideoDescription(text) {
            if (!text) return 'No summary available.';

            // 1. FIX: Unescape the text first to resolve double-encoding issues
            let cleanText = this.unescapeHTML(text);

            // 2. Define regex patterns
            // Match URLs (http, https, ftp)
            const urlRegex = /(\b(https?|ftp):\/\/[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/ig;
            // Match @usernames (YouTube channels)
            const atRegex = /@([\w\d_.-]+)/g;
            // Match #hashtags (YouTube hashtags)
            const hashRegex = /#([\w\d_.-]+)/g;

            // 3. Apply replacements
            let formattedText = cleanText
                .replace(urlRegex, (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`)
                .replace(atRegex, (match, username) => `<a href="https://www.youtube.com/@${username}" target="_blank" rel="noopener noreferrer">${match}</a>`)
                .replace(hashRegex, (match, tag) => `<a href="https://www.youtube.com/hashtag/${tag}" target="_blank" rel="noopener noreferrer">${match}</a>`)
                .replace(/\n/g, '<br>'); // Finally, replace newlines with <br>

            return formattedText;
        },

        /**
         * Generates a "time ago" string based on your new logic.
         * @param {string} publishedDateISO - The NFO <aired> date.
         * @param {string} uploadedDateISO - The file modification date.
         */
        formatDateAgo(publishedDateISO, uploadedDateISO) {
            if (!publishedDateISO && !uploadedDateISO) return '';

            const now = new Date();
            const today = now.toDateString(); // e.g., "Fri Oct 31 2025"
            
            let dateToCompare;
            const publishedDate = new Date(publishedDateISO);

            // Check if publishedDate is today
            if (publishedDate.toDateString() === today) {
                // It's from today, use the more accurate 'uploaded' time
                dateToCompare = new Date(uploadedDateISO);
            } else {
                // It's an older video, use the NFO 'published' date
                dateToCompare = publishedDate;
            }

            if (isNaN(dateToCompare.getTime())) {
                // Fallback if date is invalid
                return new Date(publishedDateISO || uploadedDateISO).toLocaleDateString();
            }

            const seconds = Math.round((now - dateToCompare) / 1000);
            
            const intervals = {
                year: 31536000,
                month: 2592000,
                week: 604800,
                day: 86400,
                hour: 3600,
                minute: 60
            };

            if (seconds < 60) return 'just now';
            
            let counter;
            for (const unit in intervals) {
                counter = Math.floor(seconds / intervals[unit]);
                if (counter > 0) {
                    return `${counter} ${unit}${counter !== 1 ? 's' : ''} ago`;
                }
            }
            return 'just now'; // Fallback
        },

        toggleFolder(path) {
            // New: Manages the open/closed state of folders in the sidebar
            const index = this.openFolderPaths.indexOf(path);
            if (index === -1) {
                this.openFolderPaths.push(path);
            } else {
                this.openFolderPaths.splice(index, 1);
            }
        },

        // --- Data Modification ---
        
        // NEW: Function to send video progress to the backend
        async updateVideoProgress(video, duration) {
            // Check the 4-second minimum requirement on the frontend too
            if (duration < 4) return; 

            try {
                const response = await fetch(`/api/video/${video.id}/progress`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ duration_watched: Math.floor(duration) })
                });
                const result = await response.json();
                if (response.ok) {
                    // Update frontend state immediately to reflect in history view
                    video.watched_duration = result.watched_duration;
                    video.last_watched = result.last_watched;
                } else {
                    console.error('Failed to save progress:', result.error);
                }
            } catch (e) {
                console.error('Error saving video progress:', e);
            }
        },
        
        async scanVideoLibrary(isQuiet = false) {
            if (this.isScanning) return;
            if (!isQuiet) this.isScanning = true;
            try {
                const response = await fetch('/api/scan_videos', { method: 'POST' });
                if (!response.ok) {
                    const result = await response.json();
                    console.warn('Scan endpoint reported errors:', result.error || 'Unknown error');
                }
                await this.fetchData(); // Reload all data
            } catch (e) { 
                console.error('Error scanning library:', e); 
            }
            finally { 
                if (!isQuiet) this.isScanning = false; 
            }
        },
        
        async toggleFavorite(video) {
            const originalState = video.is_favorite;
            video.is_favorite = !video.is_favorite;
            try {
                const response = await fetch(`/api/article/${video.id}/favorite`, { method: 'POST' });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error);
                video.is_favorite = result.is_favorite;
            } catch (e) { 
                video.is_favorite = originalState; 
                console.error('Favorite toggle failed:', e); 
            }
        },
        
        async toggleBookmark(video) {
            const originalState = video.is_read_later; // Mapped state
            video.is_read_later = !video.is_read_later;
            try {
                const response = await fetch(`/api/article/${video.id}/bookmark`, { method: 'POST' });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error);
                video.is_read_later = result.is_read_later; // API returns 'is_read_later'
            } catch(e) { 
                video.is_read_later = originalState; 
                console.error('Bookmark toggle failed:', e); 
            }
        },

        // --- All feed/category/stream/drag-drop functions have been removed ---
    };
}

/**
 * New Alpine.js component for the recursive folder tree.
 */
function folderTree(tree, basePath = '') {
    return {
        tree: tree,
        basePath: basePath,
        
        /** Checks if a path is in the parent's openFolderPaths array. */
        isOpen(path) { return this.openFolderPaths.includes(path); },
        
        /** Calls the parent's toggleFolder method. */
        toggle(path) { this.toggleFolder(path); },
        
        /** Calls the parent's setView method. */
        setView(type, path) { this.setView(type, path, null); },
        
        /** Checks if this folder is the currently active view. */
        isCurrentView(path) { return this.currentView.type === 'folder' && this.currentView.id === path; },
        
        /** Calculates the full path for a subfolder. */
        fullPath(name) { return this.basePath + name; },
        
        /** Checks if a folder node has children. */
        hasChildren(children) { return children && Object.keys(children).length > 0; },
        
        /** Returns sorted [name, children] entries for the x-for loop. */
        sortedEntries(obj) { 
            if (!obj) return [];
            return Object.entries(obj).sort((a, b) => a[0].localeCompare(b[0])); 
        }
    }
}

// --- ALPINE INITIALIZATION ---
document.addEventListener('alpine:init', () => {
    Alpine.data('videoApp', videoApp);
    Alpine.data('folderTree', folderTree);
});