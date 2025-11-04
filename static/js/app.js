/**
 * Main Alpine.js component for the VolumePlay video application.
 */
function videoApp() {
    return {
        // --- State Variables (Managed by this component) ---
        isMobileMenuOpen: false,
        isModalOpen: false,
        isScanning: false,
        isGeneratingThumbnails: false, // This will still control the spinner/disabled state
        
        // --- NEW STATUS VARS ---
        thumbnailStatus: { status: 'idle', message: '', progress: 0, total: 0 },
        thumbnailPollInterval: null,
        // --- END NEW ---

        isAutoplayEnabled: true, // NEW: Autoplay state (default on)
        isInfoPanelOpen: false, // NEW: For the "More Info" panel
        currentPlaybackSpeed: 1.0,
        playbackRates: [0.5, 0.75, 1.0, 1.25, 1.5, 2.0],
        // currentView is locally shadowed for reactive use in the component
        currentView: { type: 'all', id: null, author: null },
        currentTitle: 'All Videos',
        modalVideo: null,
        searchQuery: '',
        sortOrder: 'aired_newest',
        appData: {
            videos: [],
            folder_tree: {},
            smartPlaylists: []
        },
        videosToShow: 75,
        filterHistory: [], // Filter history for back button

        // --- Init ---
        init() {
            // Initialize the currentView and openFolderPaths in the global store
            Alpine.store('globalState').currentView = this.currentView;
            // Ensure openFolderPaths exists for the global store
            Alpine.store('globalState').openFolderPaths = [];

            this.fetchData();

            this.startScanPolling(); // Check if a scan is running on load
        },

        async fetchData() {
            try {
                const response = await fetch('/api/data');
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                const data = await response.json();

                this.appData.videos = data.articles || [];
                this.appData.folder_tree = data.folder_tree || {};
                this.appData.smartPlaylists = data.smartPlayLists || []; // Load playlists

                // CRITICAL FIX: If the view type is 'folder' but the folder tree is now empty, reset view
                if (this.currentView.type === 'folder' && Object.keys(this.appData.folder_tree).length === 0) {
                    this.setView('all');
                }

                if (!this.currentTitle || this.currentTitle === 'All Videos') {
                    this.setView('all');
                }
            } catch (e) {
                console.error('Error fetching data:', e);
                this.appData = { videos: [], folder_tree: {}, smartPlaylists: [] };
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
            } else if (viewType === 'shorts') {
                videos = this.appData.videos.filter(v => v.is_short);
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
            // NEW: Playlist filtering
            else if (viewType === 'smart_playlist') {
                const playlistId = viewState.id;
                const playlist = this.appData.smartPlaylists.find(p => p.id === playlistId);

                if (!playlist) {
                    videos = []; // Playlist not found
                } else {
                    // Start with all videos and filter them down
                    videos = this.appData.videos;

                    playlist.filters.forEach(filter => {
                        if (filter.type === 'title') {
                            const filterValue = String(filter.value || '');

                            if (filterValue.startsWith('"') && filterValue.endsWith('"')) {
                                // Exact phrase match (case-sensitive)
                                const searchTerm = filterValue.substring(1, filterValue.length - 1);
                                if (searchTerm) {
                                    videos = videos.filter(v => (v.title || '').includes(searchTerm));
                                }
                            } else {
                                // Flexible match (case-insensitive)
                                const searchTerm = filterValue.toLowerCase();
                                videos = videos.filter(v => (v.title || '').toLowerCase().includes(searchTerm));
                            }
                        }
                        // --- NEW: Handle Author Filter ---
                        else if (filter.type === 'author') {
                            // filter.value will be an array of author names, e.g., ['Davie', 'Julio']
                            const allowedAuthors = filter.value; 
                            if (allowedAuthors && allowedAuthors.length > 0) {
                                // Keep videos where the author is in the allowed list
                                videos = videos.filter(v => allowedAuthors.includes(v.author));
                            }
                        }
                    });
                }
            }
            // END NEW

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
                    return dateB - dateA; // Newest watched first
                }

                let dateA, dateB;
                const MAX_DATE = new Date(8640000000000000); // Far future
                const MIN_DATE = new Date(0); // The_past

                switch (this.sortOrder) {
                    case 'aired_oldest':
                        // Use max date for nulls to send them to the end
                        dateA = a.aired_date ? new Date(a.aired_date) : MAX_DATE;
                        dateB = b.aired_date ? new Date(b.aired_date) : MAX_DATE;
                        return dateA - dateB; 
                    
                    case 'uploaded_newest':
                        dateA = a.uploaded ? new Date(a.uploaded) : MIN_DATE;
                        dateB = b.uploaded ? new Date(b.uploaded) : MIN_DATE;
                        return dateB - dateA;

                    case 'uploaded_oldest':
                        dateA = a.uploaded ? new Date(a.uploaded) : MAX_DATE;
                        dateB = b.uploaded ? new Date(b.uploaded) : MAX_DATE;
                        return dateA - dateB; 

                    case 'aired_newest': // Default case
                    default:
                        // Use min date for nulls to send them to the end
                        dateA = a.aired_date ? new Date(a.aired_date) : MIN_DATE;
                        dateB = b.aired_date ? new Date(b.aired_date) : MIN_DATE;
                        return dateB - dateA; 
                }
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
            // NEW: Add message for smart playlists
            if (viewType === 'smart_playlist') return 'No videos match this playlist\'s filters.';
            if (this.fullFilteredList.length === 0) return 'No videos found for this view.';
            return 'No videos found.';
        },

        // --- NEW COMPUTED PROPERTY ---
        get thumbnailButtonText() {
            if (!this.isGeneratingThumbnails) {
                return 'Gen. Missing Thumbs';
            }
            
            const status = this.thumbnailStatus.status;
            
            if (status === 'starting') {
                return 'Starting...';
            }
            
            if (status === 'generating') {
                if (this.thumbnailStatus.total === 0) {
                    return 'Generating... (Scanning)';
                }
                return `Generating... ${this.thumbnailStatus.progress} / ${this.thumbnailStatus.total}`;
            }

            if (status === 'error') {
                return 'Error (Retry?)';
            }

            if (status === 'idle') {
                return 'Finishing...';
            }
            
            return 'Working...'; // Fallback
        },
        // --- END NEW ---

        // --- Dynamic Tag Filtering Logic ---

        get currentFilterPath() {
            const viewState = Alpine.store('globalState').currentView;
            // Return null or empty string if not filtered by folder or if searching
            if (viewState.type !== 'folder' || this.searchQuery.trim() !== '') return null;
            // Ensure no trailing slash for easier path logic comparison
            return viewState.id.endsWith('/') ? viewState.id.slice(0, -1) : viewState.id;
        },

        get getDynamicTags() {
            // Only show tags when viewing 'all' or filtering by 'folder' without a search query
            const viewType = Alpine.store('globalState').currentView.type;
            if (viewType !== 'all' && viewType !== 'folder') return [];
            if (this.searchQuery.trim() !== '') return [];

            const videos = this.fullFilteredList;
            if (videos.length === 0) return [];

            // Determine the base path for filtering
            const currentPath = viewType === 'folder' ? this.currentFilterPath : '';
            const pathPrefix = currentPath ? currentPath + '/' : '';

            // Map to store unique next segments (tags)
            const segments = new Map();

            // 1. Identify all unique next segments from the remaining videos
            videos.forEach(v => {
                const path = v.relative_path || '';

                if (path.startsWith(pathPrefix)) {
                    let remainingPath = path.substring(pathPrefix.length);
                    const nextSegment = remainingPath.split('/')[0];

                    if (nextSegment && nextSegment !== currentPath) {
                        segments.set(nextSegment, (segments.get(nextSegment) || 0) + 1);
                    }
                }
            });

            const validTags = new Set();
            const potentialTags = Array.from(segments.keys());

            // 2. Apply stopping condition: Avoid tags that result in clutter (single-video terminal folders)
            potentialTags.forEach(tag => {
                const nextPath = pathPrefix + tag;

                // Find all videos that would be shown if this tag were clicked
                const videosUnderNextPath = this.appData.videos.filter(v =>
                    (v.relative_path || '').startsWith(nextPath)
                );

                // Optimization: If filtering by this tag results in 0 or 1 video, skip complex check.
                if (videosUnderNextPath.length <= 1) {
                    validTags.add(tag);
                    return;
                }

                // Check for clutter: Does this tag lead to a view where every resulting video is in its own unique, immediate subfolder?
                let uniqueImmediateSubFolders = new Set();

                videosUnderNextPath.forEach(v => {
                    let pathRemainder = v.relative_path.substring(nextPath.length);
                    if (pathRemainder.startsWith('/')) {
                        pathRemainder = pathRemainder.substring(1); // Remove leading '/'
                    }

                    // Get the next level segment (e.g., 'S1' from 'S1/vid1.mp4')
                    const nextSubFolder = pathRemainder.split('/')[0];

                    if (nextSubFolder) {
                        uniqueImmediateSubFolders.add(nextSubFolder);
                    }
                });

                // If the number of videos is equal to the number of unique next-level folders,
                // and there's more than one video, it means all videos are separated one level down
                // into unique folders (clutter). We skip this tag.
                if (videosUnderNextPath.length > 1 && uniqueImmediateSubFolders.size === videosUnderNextPath.length) {
                    return; // Skip this tag: Clutter detected.
                }

                validTags.add(tag);
            });

            // 3. Final single-tag check: If only one tag remains, and it results in 1 video, don't show it.
            if (validTags.size === 1) {
                const singleTag = Array.from(validTags)[0];
                const nextPath = pathPrefix + singleTag;
                const videosUnderNextPath = this.appData.videos.filter(v => (v.relative_path || '').startsWith(nextPath));

                if (videosUnderNextPath.length <= 1) {
                    return [];
                }
            }

            return Array.from(validTags).sort();
        },

        filterByFolderTag(tag) {
            if (tag === 'clear_all') {
                this.setView('all');
                return;
            }

            const currentPath = this.currentFilterPath;
            // The path must be constructed without the leading slash if starting at root
            const newPath = currentPath ? currentPath + '/' + tag : tag;

            // Close the modal if open, then change the view
            if (this.isModalOpen) this.closeModal();
            this.setView('folder', newPath, null);
        },

        // --- Smart Playlist Actions ---
        async createPlaylist(playlistName) {
            if (!playlistName || playlistName.trim() === '') return;

            try {
                // API call to backend
                const response = await fetch('/api/playlist/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: playlistName.trim() })
                });
                const newPlaylist = await response.json();

                if (response.ok) {
                    // Prepend the new playlist to the list for immediate display
                    this.appData.smartPlaylists.unshift(newPlaylist);
                } else {
                    console.error('Failed to create playlist:', newPlaylist.error);
                }
            } catch (e) {
                console.error('Error creating playlist:', e);
            }
        },

        // PLACEHOLDER: We will implement renaming in a future step
        async renamePlaylist(playlist) {
            const newName = prompt(`Rename playlist '${playlist.name}':`, playlist.name);
            
            if (!newName || newName.trim() === '' || newName.trim() === playlist.name) {
                return; // User cancelled or name is unchanged
            }

            try {
                const response = await fetch(`/api/playlist/${playlist.id}/rename`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: newName.trim() })
                });

                if (response.ok) {
                    const updatedPlaylist = await response.json();
                    // Update the name on the original object for reactivity
                    playlist.name = updatedPlaylist.name;
                    // Update the main title if we are currently viewing this playlist
                    if (this.currentView.type === 'smart_playlist' && this.currentView.id === playlist.id) {
                        this.updateTitle();
                    }
                } else {
                    const result = await response.json();
                    console.error('Failed to rename playlist:', result.error);
                }
            } catch (e) {
                console.error('Error renaming playlist:', e);
            }
        },

        // IMPLEMENTED: Delete Playlist functionality
        async deletePlaylist(playlistId) {
            // Use existing browser confirm() as per placeholder structure
            if (confirm('Are you sure you want to permanently delete this playlist?')) {
                try {
                    const response = await fetch(`/api/playlist/${playlistId}/delete`, {
                        method: 'POST'
                    });

                    if (response.ok) {
                        // Remove from local state
                        this.appData.smartPlaylists = this.appData.smartPlaylists.filter(p => p.id !== playlistId);
                        // If the deleted playlist was the current view, reset to 'all'
                        if (this.currentView.type === 'smart_playlist' && this.currentView.id === playlistId) {
                            this.setView('all');
                        }
                    } else {
                        const result = await response.json();
                        console.error('Failed to delete playlist:', result.error);
                    }
                } catch (e) {
                    console.error('Error deleting playlist:', e);
                }
            }
        },

        // PLACEHOLDER: We will implement drag and drop later
        handlePlaylistDrop(playlistId, event) {
            console.log(`Placeholder: Dropped on playlist ${playlistId}`);
        },

        // PLACEHOLDER: We will implement tag removal later
        removeTagFromPlaylist(playlistId, tag) {
            console.log(`Placeholder: Removing tag '${tag}' from playlist ${playlistId}`);
        },

        async removeFilterFromPlaylist(playlistId, filterId) {
            
            try {
                const response = await fetch(`/api/playlist/${playlistId}/filter/remove`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filterId: filterId })
                });

                if (response.ok) {
                    const updatedPlaylist = await response.json();
                    // Find the local playlist object
                    const index = this.appData.smartPlaylists.findIndex(p => p.id === playlistId);
                    if (index !== -1) {
                        // --- THIS IS THE FIX ---
                        // Surgically update the filters array, just like in saveFilterToPlaylist
                        this.appData.smartPlaylists[index].filters = updatedPlaylist.filters;
                    }
                } else {
                    const result = await response.json();
                    console.error('Failed to remove filter:', result.error);
                }
            } catch (e) {
                console.error('Error removing filter:', e);
            }
        },

        // Function to apply filter criteria (called by filterEditor)
        // FIXED: Added missing function definition wrapper
        async saveFilterToPlaylist(playlistId, filter) {
            try {
                const response = await fetch(`/api/playlist/${playlistId}/filter`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filter: filter })
                });
                const updatedPlaylist = await response.json(); // This contains the new list of filters

                if (response.ok) {
                    // Find the *local* playlist object
                    const index = this.appData.smartPlaylists.findIndex(p => p.id === playlistId);
                    if (index !== -1) {
                        // --- THIS IS THE KEY ---
                        // Instead of replacing the whole object with splice,
                        // just update the 'filters' array on the *existing* object.
                        // This preserves the object reference and makes reactivity simple.
                        this.appData.smartPlaylists[index].filters = updatedPlaylist.filters;
                    }
                } else {
                    console.error('Failed to save filter to playlist:', updatedPlaylist.error);
                }
            } catch (e) {
                console.error('Error saving filter to playlist:', e);
            }
        },


        // --- UI Actions ---
        goBackOneFilter() {
            if (this.filterHistory.length === 0) return;

            // Pop the last saved state
            const lastView = this.filterHistory.pop();

            // 1. Apply the previous view state directly
            Alpine.store('globalState').currentView = {
                type: lastView.type,
                id: lastView.id,
                author: lastView.author
            };

            // 2. Update local state and title
            this.currentView = Alpine.store('globalState').currentView;
            this.currentTitle = lastView.title; // Use the saved title

            // 3. Reset display count
            this.videosToShow = 75;
        },

        setView(type, id = null, author = null) {

            // 1. Check if the current view should be saved to history
            const currentView = Alpine.store('globalState').currentView;

            // Save the previous state if we are moving from one 'folder' view to a NEW 'folder' view
            if (currentView.type === 'folder' && type === 'folder' && currentView.id !== id) {
                // Save only the essential view state and current title
                this.filterHistory.push({
                    type: currentView.type,
                    id: currentView.id,
                    author: currentView.author,
                    title: this.currentTitle
                });
            }

            // Clear history if navigating to 'all' or another primary view type
            if (type !== 'folder') {
                this.filterHistory = [];
            }

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
            else if (type === 'shorts') { this.currentTitle = 'Shorts'; }
            else if (type === 'author') { this.currentTitle = `Author: ${author || 'Unknown'}`; }
            else if (type === 'folder') {
                const pathSegments = id ? id.split('/').filter(Boolean) : [];
                this.currentTitle = `Folder: ${pathSegments.pop() || 'Root'}`;
            }
            else if (type === 'smart_playlist') {
                const playlist = this.appData.smartPlaylists.find(p => p.id === id);
                this.currentTitle = `Playlist: ${playlist ? playlist.name : 'Unknown'}`;
            }
            else { this.currentTitle = 'All Videos'; }
        },

        loadMoreVideos() {
            this.videosToShow += 75;
        },

        openModal(video) {
            this.modalVideo = video;
            this.isModalOpen = true;
            this.isInfoPanelOpen = false; // NEW: Reset panel on open

            this.$nextTick(() => {
                if (this.$refs.videoPlayer) {
                    const lastDuration = video.watched_duration || 0;
                    // Start video at last watched duration if it's more than 10 seconds in
                    if (lastDuration > 10) {
                        this.$refs.videoPlayer.currentTime = lastDuration;
                    }
                    // Apply the current playback speed
                    this.$refs.videoPlayer.playbackRate = this.currentPlaybackSpeed;
                }
            });
        },

        // START: AUTOPLAY REFACTOR
        stopAndSaveVideo() {
            // Helper function to stop playback and save progress
            if (this.modalVideo && this.$refs.videoPlayer) {
                const videoElement = this.$refs.videoPlayer;
                const durationWatched = videoElement.currentTime;

                videoElement.pause();
                videoElement.src = ''; // Detach src

                this.updateVideoProgress(this.modalVideo, durationWatched);
            }
        },

        closeModal() {
            // CRITICAL: Stop video playback and save progress
            this.stopAndSaveVideo();

            this.isInfoPanelOpen = false; // NEW: Reset panel on close
            this.isModalOpen = false;
            this.modalVideo = null;
        },

        navigateToAuthorFilter(author) {
            this.closeModal();
            // Use $nextTick to ensure modal is fully closed before changing view
            this.$nextTick(() => {
                this.setView('author', null, author);
            });
        },

        handleVideoEnd() {
            // 1. Save progress of the video that just finished
            this.stopAndSaveVideo();

            // 2. Check if autoplay is on
            if (this.isAutoplayEnabled) {
                // 3. Find the next video in the *currently visible* list (filteredVideos)
                const currentIndex = this.filteredVideos.findIndex(v => v.id === this.modalVideo.id);

                if (currentIndex !== -1 && currentIndex + 1 < this.filteredVideos.length) {
                    // 4. If next video exists, set it as the new modalVideo
                    // and manually restart the player
                    const nextVideo = this.filteredVideos[currentIndex + 1];
                    this.modalVideo = nextVideo; // This updates the modal content

                    this.$nextTick(() => {
                        if (this.$refs.videoPlayer) {
                            const player = this.$refs.videoPlayer;
                            player.src = this.modalVideo.video_url; // Set new source

                            const lastDuration = this.modalVideo.watched_duration || 0;
                            // Start video at last watched duration if it's more than 10 seconds in
                            if (lastDuration > 10) {
                                player.currentTime = lastDuration;
                            } else {
                                player.currentTime = 0;
                            }
                            player.play(); // Start the next video
                            // Apply the current playback speed
                            player.playbackRate = this.currentPlaybackSpeed;
                        }
                    });

                    // We DO NOT close the modal
                    return;
                }
            }

            // 5. If autoplay is off, or it was the last video, close the modal.
            this.isModalOpen = false;
            this.modalVideo = null;
        },
        // END: AUTOPLAY REFACTOR

        // --- NEW: Playback Speed Controls ---
        setPlaybackSpeed(speed) {
            const newSpeed = parseFloat(speed);
            if (isNaN(newSpeed)) return;

            this.currentPlaybackSpeed = newSpeed;
            if (this.$refs.videoPlayer) {
                this.$refs.videoPlayer.playbackRate = newSpeed;
            }
        },

        cyclePlaybackSpeed() {
            const currentIndex = this.playbackRates.indexOf(this.currentPlaybackSpeed);
            let nextIndex = currentIndex + 1;
            if (nextIndex >= this.playbackRates.length) {
                nextIndex = 0; // Loop back to the start
            }
            this.setPlaybackSpeed(this.playbackRates[nextIndex]);
        },

        // --- Content Rendering ---

        // NEW: Helper function for modal video count
        getAuthorVideoCount(author) {
            if (!author || !this.appData.videos) return 0;
            return this.appData.videos.filter(v => v.author === author).length;
        },

        unescapeHTML(text) {
            if (!text) return '';
            const doc = new DOMParser().parseFromString(text, 'text/html');
            return doc.documentElement.textContent;
        },

        // --- MODIFIED FUNCTION ---
        formatVideoDescription(text) {
            if (!text) return 'No summary available.';

            let cleanText = this.unescapeHTML(text);

            // Define all regex patterns
            const urlRegex = /(\b(https?|ftp):\/\/[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/ig;
            const atRegex = /@([\w\d_.-]+)/g;
            const hashRegex = /#([\w\d_.-]+)/g;
            
            // This regex looks for a timestamp that is EITHER at the start of the string OR preceded by whitespace.
            // It captures the whitespace (or start) in $1, and the timestamp in $2.
            // Handles formats like H:MM:SS, HH:MM:SS, M:SS, or MM:SS
            const timecodeRegex = /(^|\s)((?:(?:\d{1,2}):)?\d{1,2}:\d{2})\b/g;

            // Run replacements. Timecode FIRST, to avoid conflicts with other link types.
            let formattedText = cleanText
                .replace(timecodeRegex, (match, p1, p2) => {
                    // p1 is the whitespace/start, p2 is the timecode
                    return `${p1}<a href="#" onclick="seekVideo(event, '${p2}')" class="text-[var(--text-highlight)] hover:underline font-semibold" title="Seek to ${p2}">${p2}</a>`;
                })
                .replace(urlRegex, (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`)
                .replace(atRegex, (match, username) => `<a href="https://www.youtube.com/@${username}" target="_blank" rel="noopener noreferrer">${match}</a>`)
                .replace(hashRegex, (match, tag) => `<a href="https://www.youtube.com/hashtag/${tag}" target="_blank" rel="noopener noreferrer">${match}</a>`)
                .replace(/\n/g, '<br>'); // Newlines last

            return formattedText;
        },
        // --- END MODIFIED FUNCTION ---

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

            const seconds = Math.round((now - dateToCompare) / 1E3);

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

        // NEW: Helper function for "More Info" panel
        formatFileSize(bytes) {
            if (!bytes || bytes === 0) return '0 Bytes';
            const k = 1024;
            const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        },

        formatDuration(totalSeconds) {
            if (!totalSeconds || totalSeconds < 1) {
                return '0:00';
            }
            totalSeconds = Math.round(totalSeconds);
            
            const hours = Math.floor(totalSeconds / 3600);
            const minutes = Math.floor((totalSeconds % 3600) / 60);
            const seconds = totalSeconds % 60;

            const pad = (num) => num.toString().padStart(2, '0');

            if (hours > 0) {
                return `${hours}:${pad(minutes)}:${pad(seconds)}`;
            }
            return `${minutes}:${pad(seconds)}`;
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

        async scanVideoLibrary() {
            if (this.isScanning) return; // Don't click if a scan is already polling
            
            try {
                const response = await fetch('/api/scan_videos', { method: 'POST' });
                
                // On a 202 (started) or 409 (already running), start polling
                if (response.status === 202 || response.status === 409) {
                    this.startScanPolling();
                } else {
                    const result = await response.json();
                    console.error('Failed to start scan:', result.error);
                }
            } catch (e) {
                console.error('Error starting scan:', e);
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
            } catch (e) {
                video.is_read_later = originalState;
                console.error('Bookmark toggle failed:', e);
            }
        },

        // --- NEW POLLING FUNCTIONS ---
        startThumbnailPolling() {
            if (this.thumbnailPollInterval) {
                clearInterval(this.thumbnailPollInterval); // Clear any old pollers
            }
            
            this.isGeneratingThumbnails = true; // Disable button, start spin

            this.thumbnailPollInterval = setInterval(async () => {
                try {
                    const response = await fetch('/api/thumbnails/status');
                    if (!response.ok) throw new Error('Status poll failed');
                    
                    const data = await response.json();
                    this.thumbnailStatus = data;

                    // --- MODIFIED: Refresh data *while* generating ---
                    // This will show new thumbnails as they are committed in batches.
                    if (data.status === 'generating') {
                        this.fetchData();
                    }
                    // --- END MODIFIED ---

                    // If the job is done (idle) or errored, stop polling
                    if (data.status === 'idle' || data.status === 'error') {
                        this.stopThumbnailPolling();
                    }
                } catch (e) {
                    console.error(e);
                    this.thumbnailStatus.status = 'error';
                    this.stopThumbnailPolling(); // Stop if we can't even poll
                }
            }, 2000); // Poll every 2 seconds
        },

        stopThumbnailPolling() {
            if (this.thumbnailPollInterval) {
                clearInterval(this.thumbnailPollInterval);
                this.thumbnailPollInterval = null;
            }
            
            this.isGeneratingThumbnails = false; // Re-enable button, stop spin
            
            // If it finished successfully, refresh the main video data
            if (this.thumbnailStatus.status === 'idle') {
                console.log('Thumbnail generation complete, fetching new data.');
                this.fetchData();
            }
        },

        startScanPolling() {
            if (this.scanPollInterval) {
                clearInterval(this.scanPollInterval); // Clear any old pollers
            }
            
            this.isScanning = true; // This will make the refresh icon spin
            
            this.scanPollInterval = setInterval(async () => {
                try {
                    const response = await fetch('/api/scan/status');
                    if (!response.ok) throw new Error('Scan status poll failed');
                    
                    const data = await response.json();
                    this.scanStatus = data;

                    // If the job is done (idle) or errored, stop polling
                    if (data.status === 'idle' || data.status === 'error') {
                        this.stopScanPolling();
                    }
                } catch (e) {
                    console.error(e);
                    this.scanStatus = { status: 'error', message: e.message };
                    this.stopScanPolling(); // Stop if we can't even poll
                }
            }, 3000); // Poll every 3 seconds
        },

        stopScanPolling() {
            if (this.scanPollInterval) {
                clearInterval(this.scanPollInterval);
                this.scanPollInterval = null;
            }
            
            this.isScanning = false; // Stop the refresh icon from spinning
            
            // If it finished successfully, refresh the main video data
            if (this.scanStatus.status === 'idle') {
                console.log('Video scan complete, fetching new data.');
                this.fetchData();
            }
        },
        // --- END NEW ---

        // NEW: Function to call the background thumbnail generator
        async generateMissingThumbnails() {
            if (this.isGeneratingThumbnails) return; // Prevent double-clicks

            this.isGeneratingThumbnails = true; // Immediately disable
            
            try {
                const response = await fetch('/api/thumbnails/generate_missing', { method: 'POST' });
                
                // On a 200 (already running) or 202 (just started), start polling
                if (response.status === 200 || response.status === 202) {
                    this.startThumbnailPolling();
                } else {
                    // Handle an immediate failure to start the task
                    const result = await response.json();
                    console.error('Failed to start thumbnail generation:', result.error);
                    this.thumbnailStatus.status = 'error';
                    this.isGeneratingThumbnails = false; // Re-enable on failure
                }
                
            } catch (e) {
                console.error('Error starting thumbnail generation:', e);
                this.thumbnailStatus.status = 'error';
                this.isGeneratingThumbnails = false; // Re-enable on failure
            }
            // --- REMOVED THE 'finally' BLOCK ---
        },
    };
}

// --- NEW HELPER FUNCTION ---
/**
 * Globally accessible function to seek the main video player.
 * @param {Event} event - The click event, to prevent default link behavior.
 * @param {string} timestampString - The timestamp (e.g., "0:10:26" or "10:26").
 */
function seekVideo(event, timestampString) {
    // Prevent the <a> tag's default behavior (jumping to top of page)
    event.preventDefault();
    
    // Use the ID you added to your HTML
    const player = document.getElementById('main-video-player');
    if (!player) {
        console.error('Video player element not found.');
        return;
    }

    // Split the timestamp into parts (e.g., ["0", "10", "26"] or ["10", "26"])
    const parts = timestampString.split(':').map(Number);
    let seconds = 0;

    try {
        if (parts.length === 3) {
            // Format: H:MM:SS
            seconds = (parts[0] * 3600) + (parts[1] * 60) + parts[2];
        } else if (parts.length === 2) {
            // Format: M:SS or MM:SS
            seconds = (parts[0] * 60) + parts[1];
        } else {
            console.warn('Unrecognized timestamp format:', timestampString);
            return;
        }

        // Set the player's time and ensure it's playing
        player.currentTime = seconds;
        player.play();

    } catch (e) {
        console.error('Error parsing timestamp:', timestampString, e);
    }
}
// --- END NEW HELPER FUNCTION ---


/**
 * UPDATED: Alpine.js component for managing the filter selection and application.
 * Now handles 'title' and 'author' filter types.
 */
function filterEditor(playlistId, appData) {
    return {
        playlistId: playlistId,
        selectedType: 'title', // Default filter type
        textValue: '',         // For title filter
        selectedAuthors: [],   // For author filter
        // allAuthors: [],     // <-- REMOVED
        typeLabels: {
            'title': 'Title Content',
            'author': 'Author'
        },

        // --- REMOVED THE init() FUNCTION ---

        // --- NEW: Replaced allAuthors with a GETTER ---
        get allAuthors() {
            // This now runs every time the dropdown needs the list,
            // so it will always have the freshest data.
            const videos = appData.videos || [];
            const authors = new Set(videos.map(v => v.author || 'Unknown Author'));
            return Array.from(authors).sort();
        },
        resetValues() {
            this.textValue = '';
            this.selectedAuthors = [];
        },

        isFilterValid() {
            if (this.selectedType === 'title') {
                return typeof this.textValue === 'string' && this.textValue.trim().length > 0;
            }
            if (this.selectedType === 'author') {
                return this.selectedAuthors.length > 0;
            }
            return false;
        },

        applyFilter() {
            if (!this.isFilterValid()) {
                console.warn('Filter submission ignored: Input is invalid.');
                return;
            }

            let filterValue;
            if (this.selectedType === 'title') {
                filterValue = this.textValue.trim();
            } else if (this.selectedType === 'author') {
                filterValue = this.selectedAuthors; // This will be an array
            }

            let filter = {
                id: `${Date.now()}-${this.selectedType}`,
                type: this.selectedType,
                label: this.typeLabels[this.selectedType],
                value: filterValue
            };

            this.$dispatch('save-filter', {
                playlistId: this.playlistId,
                filter: filter
            });

            // Close the editor UI
            this.$root.parentElement.__x.$data.isEditingFilters = false;
        }
    };
}

/**
 * Alpine.js component for the recursive folder tree (now unused but required for folderTree template).
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
    Alpine.data('filterEditor', filterEditor); // NEW: Register filterEditor

    // Create a central, reactive store for state shared between videoApp and folderTree
    Alpine.store('globalState', {
        openFolderPaths: [],
        currentView: { type: 'all', id: null, author: null },
    });
});