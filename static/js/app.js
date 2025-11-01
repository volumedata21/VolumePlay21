/**
 * Main Alpine.js component for the VolumePlay video application.
 */
function videoApp() {
    return {
        // --- State Variables (Managed by this component) ---
        isMobileMenuOpen: false,
        isModalOpen: false,
        isScanning: false,         
        // currentView is locally shadowed for reactive use in the component
        currentView: { type: 'all', id: null, author: null },
        currentTitle: 'All Videos',
        modalVideo: null,          
        searchQuery: '',
        sortOrder: 'newest',
        appData: {
            videos: [],            
            folder_tree: {}      
        },
        videosToShow: 75,          

        // --- Init ---
        init() {
            // Initialize the currentView and openFolderPaths in the global store
            Alpine.store('globalState').currentView = this.currentView;
            // Ensure openFolderPaths exists for the global store
            Alpine.store('globalState').openFolderPaths = []; 
            
            this.fetchData();
        },
        
        async fetchData() {
            try {
                const response = await fetch('/api/data');
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                const data = await response.json();
                
                this.appData.videos = data.articles || []; 
                this.appData.folder_tree = data.folder_tree || {};

                // CRITICAL FIX: If the view type is 'folder' but the folder tree is now empty, reset view
                if (this.currentView.type === 'folder' && Object.keys(this.appData.folder_tree).length === 0) {
                     this.setView('all');
                }
                
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
            // Read view state from the global store for responsiveness
            const viewState = Alpine.store('globalState').currentView;
            const viewType = viewState.type;
            const viewAuthor = viewState.author;

            if (viewType === 'all') {
                videos = this.appData.videos;
            } else if (viewType === 'favorites') {
                videos = this.appData.videos.filter(v => v.is_favorite);
            } else if (viewType === 'watchLater') { 
                videos = this.appData.videos.filter(v => v.is_read_later); 
            } else if (viewType === 'history') { 
                // Filter for videos watched for 4 seconds or more
                videos = this.appData.videos.filter(v => v.watched_duration >= 4);
            } else if (viewType === 'author') {
                videos = this.appData.videos.filter(v => v.author && v.author === viewAuthor);
            } else if (viewType === 'folder') { 
                const path = viewState.id;
                videos = this.appData.videos.filter(v => 
                    // Match the folder OR any subfolder
                    v.relative_path === path || 
                    (v.relative_path && v.relative_path.startsWith(path + '/'))
                );
            }

            if (this.searchQuery.trim() !== '') {
                const query = this.searchQuery.toLowerCase();
                videos = videos.filter(v =>
                    (v.title && v.title.toLowerCase().includes(query)) ||
                    (v.summary && v.summary.toLowerCase().includes(query)) ||
                    (v.author && v.author.toLowerCase().includes(query))
                );
            }

            videos.sort((a, b) => {
                // History View always sorts by last_watched (most recent first)
                if (viewType === 'history') {
                    const dateA = a.last_watched ? new Date(a.last_watched) : 0;
                    const dateB = b.last_watched ? new Date(b.last_watched) : 0;
                    return dateB - dateA; 
                }
                
                // Default sort (for all other views)
                const dateA = a.published ? new Date(a.published) : 0; 
                const dateB = b.published ? new Date(b.published) : 0;
                return this.sortOrder === 'newest' ? dateB - dateA : dateA - dateB;
            });
            
            return videos;
        },

        get filteredVideos() {
            return this.fullFilteredList.slice(0, this.videosToShow);
        },

        getEmptyMessage() {
            if (this.isScanning && this.appData.videos.length === 0) return 'Scanning library...';
            if (this.searchQuery.trim() !== '') return 'No videos match your search.';
            if (!this.appData.videos || this.appData.videos.length === 0) {
                 return 'No videos found. Click the refresh icon to scan your library.';
            }
            const viewType = Alpine.store('globalState').currentView.type;
            const viewAuthor = Alpine.store('globalState').currentView.author;

            if (viewType === 'author') return `No videos found for: ${viewAuthor || 'Unknown'}.`;
            if (viewType === 'folder') return 'No videos found in this folder.';
            if (viewType === 'history') return 'No videos in your history yet.'; 
            if (this.fullFilteredList.length === 0) return 'No videos found for this view.';
            return 'No videos found.'; 
        },

        // --- UI Actions ---
        setView(type, id = null, author = null) {
            // Write to the global state (FolderTree uses this)
            Alpine.store('globalState').currentView = { type: type, id: id, author: author };
            
            // Update local state and title (for reactivity inside this component)
            this.currentView = Alpine.store('globalState').currentView; 
            
            this.updateTitle();
            this.isMobileMenuOpen = false;
            this.videosToShow = 75; // Reset count
        },

        updateTitle() {
            // Read from the global state
            const { type, id, author } = Alpine.store('globalState').currentView;
            if (type === 'all') { this.currentTitle = 'All Videos'; }
            else if (type === 'favorites') { this.currentTitle = 'Favorites'; }
            else if (type === 'watchLater') { this.currentTitle = 'Watch Later'; }
            else if (type === 'history') { this.currentTitle = 'History'; }
            else if (type === 'author') { this.currentTitle = `Author: ${author || 'Unknown'}`; }
            else if (type === 'folder') { this.currentTitle = `Folder: ${id.split('/').pop() || '...'}`; }
            else { this.currentTitle = 'All Videos'; }
        },

        loadMoreVideos() {
            this.videosToShow += 75;
        },

        openModal(video) {
            this.modalVideo = video;
            this.isModalOpen = true;
            
            this.$nextTick(() => {
                if (this.$refs.videoPlayer) {
                    const lastDuration = video.watched_duration || 0;
                    // Start video at last watched duration if it's more than 10 seconds in
                    if (lastDuration > 10) {
                        this.$refs.videoPlayer.currentTime = lastDuration;
                    }
                }
            });
        },

        closeModal() {
            // CRITICAL: Stop video playback and save progress
            if (this.modalVideo && this.$refs.videoPlayer) {
                const videoElement = this.$refs.videoPlayer;
                const durationWatched = videoElement.currentTime;

                videoElement.pause();
                videoElement.src = ''; 
                
                this.updateVideoProgress(this.modalVideo, durationWatched);
            }
            
            this.isModalOpen = false;
            this.modalVideo = null;
        },

        // --- Content Rendering ---
        
        unescapeHTML(text) {
            if (!text) return '';
            const doc = new DOMParser().parseFromString(text, 'text/html');
            return doc.documentElement.textContent;
        },
        
        formatVideoDescription(text) {
            if (!text) return 'No summary available.';

            let cleanText = this.unescapeHTML(text);

            const urlRegex = /(\b(https?|ftp):\/\/[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/ig;
            const atRegex = /@([\w\d_.-]+)/g;
            const hashRegex = /#([\w\d_.-]+)/g;

            let formattedText = cleanText
                .replace(urlRegex, (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`)
                .replace(atRegex, (match, username) => `<a href="https://www.youtube.com/@${username}" target="_blank" rel="noopener noreferrer">${match}</a>`)
                .replace(hashRegex, (match, tag) => `<a href="https://www.youtube.com/hashtag/${tag}" target="_blank" rel="noopener noreferrer">${match}</a>`)
                .replace(/\n/g, '<br>');

            return formattedText;
        },

        formatDateAgo(publishedDateISO, uploadedDateISO) {
            if (!publishedDateISO && !uploadedDateISO) return '';

            const now = new Date();
            const today = now.toDateString(); 
            
            let dateToCompare;
            const publishedDate = new Date(publishedDateISO);

            if (publishedDate.toDateString() === today) {
                dateToCompare = new Date(uploadedDateISO);
            } else {
                dateToCompare = publishedDate;
            }

            if (isNaN(dateToCompare.getTime())) {
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
            return 'just now'; 
        },

        toggleFolder(path) {
            // Manages the open/closed state of folders in the sidebar by writing to the global store
            const openPaths = Alpine.store('globalState').openFolderPaths;
            const index = openPaths.indexOf(path);

            if (index === -1) {
                Alpine.store('globalState').openFolderPaths.push(path);
            } else {
                Alpine.store('globalState').openFolderPaths.splice(index, 1);
            }
        },

        // --- Data Modification ---
        
        async updateVideoProgress(video, duration) {
            if (duration < 4) return; 

            try {
                const response = await fetch(`/api/video/${video.id}/progress`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ duration_watched: Math.floor(duration) })
                });
                const result = await response.json();
                if (response.ok) {
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
                await this.fetchData(); 
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
            const originalState = video.is_read_later;
            video.is_read_later = !video.is_read_later;
            try {
                const response = await fetch(`/api/article/${video.id}/bookmark`, { method: 'POST' });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error);
                video.is_read_later = result.is_read_later;
            } catch(e) { 
                video.is_read_later = originalState; 
                console.error('Bookmark toggle failed:', e); 
            }
        },
    };
}

/**
 * Alpine.js component for the recursive folder tree.
 * It interacts with the global Alpine Store for state.
 */
function folderTree(tree, basePath = '') {
    return {
        tree: tree,
        basePath: basePath,
        
        // State is read directly from the global store
        isOpen(path) { return Alpine.store('globalState').openFolderPaths.includes(path); },
        isCurrentView(path) { 
            const current = Alpine.store('globalState').currentView;
            return current.type === 'folder' && current.id === path; 
        },
        
        // Actions must delegate to the main videoApp functions by grabbing its instance
        // $root.parentElement.__x.$data refers to the videoApp instance
        toggle(path) { 
            this.$root.parentElement.__x.$data.toggleFolder(path); 
        },
        setView(type, path) { 
            this.$root.parentElement.__x.$data.setView(type, path, null); 
        },
        
        fullPath(name) { return this.basePath + name; },
        // CRITICAL FIX: Only consider a node as having children if the children object 
        // contains keys that are NOT just video ID parents (which are 4th level deep in your data)
        hasChildren(children) {
            if (!children) return false;
            const keys = Object.keys(children);
            if (keys.length === 0) return false;
            
            // Check if ANY child node has content (i.e., is a folder)
            // If the key looks like your specific video parent folder name (e.g., s2025e102699), 
            // it's the end of the line, but we must return true if there are deeper levels
            
            // Simplified check: If any child key's value is non-empty, it's a folder.
            // In your current data model, an empty object `{}` means the node is a video's parent folder.
            // If the object is not empty, it contains sub-folders. 
            // Since the backend gives *every* level an object, we just check if it has keys.
            return keys.length > 0;
        },
        
        sortedEntries(obj) { 
            if (!obj) return [];
            return Object.entries(obj).sort((a, b) => a[0].localeCompare(b[0])); 
        }
    }
}

// --- ALPINE INITIALIZATION ---
document.addEventListener('alpine:init', () => {
    // Define the components
    Alpine.data('videoApp', videoApp);
    Alpine.data('folderTree', folderTree);
    
    // Create a central, reactive store for state shared between videoApp and folderTree
    Alpine.store('globalState', {
        openFolderPaths: [],
        currentView: { type: 'all', id: null, author: null },
    });
});
