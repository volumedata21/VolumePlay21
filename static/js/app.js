/**
 * Main Alpine.js component for the VolumePlay video application.
 */
function videoApp() {
    return {
        // --- State Variables (Managed by this component) ---
        isMobileMenuOpen: false,
        isModalOpen: false,
        isScanning: false, // Flag for scan polling
        isGeneratingThumbnails: false, // Flag for thumbnail polling
        isLoading: false, // For loading video pages

        // Status objects for background tasks
        thumbnailStatus: { status: 'idle', message: '', progress: 0, total: 0 },
        thumbnailPollInterval: null,
        scanStatus: { status: 'idle', message: '', progress: 0 },
        scanPollInterval: null,
        isTranscoding: false,
        transcodeStatus: { status: 'idle', message: '', video_id: null },
        transcodePollInterval: null,
        isCreatingThumb: false,

        // Player state
        isAutoplayEnabled: true,
        currentPlaybackSpeed: 1.0,
        playbackRates: [0.5, 0.75, 1.0, 1.25, 1.5, 2.0],

        // View & Data state
        currentView: { type: 'all', id: null, author: null },
        currentTitle: 'All Videos',
        modalVideo: null,
        currentVideoSrc: '', // Holds the URL for the player (original or transcode)
        searchQuery: '',
        sortOrder: 'aired_newest',
        appData: {
            videos: [], // This will be filled by pagination
            allVideos: [], // This is a new cache for smart playlists
            folder_tree: {},
            smartPlaylists: []
        },
        filterHistory: [], // Filter history for back button

        // --- NEW: Pagination State ---
        currentPage: 1,
        totalPages: 1,
        totalItems: 0,
        // --- END NEW ---

        // --- Global Filter Settings ---
        filterSettings: {
            shorts: 'normal', // 'normal', 'solo', 'hide'
            vr: 'normal',     // 'normal', 'solo', 'hide'
            optimized: 'normal' // 'normal', 'solo', 'hide'
        },

        // --- Init ---
        init() {
            // Initialize the currentView in the global store
            Alpine.store('globalState').currentView = this.currentView;
            Alpine.store('globalState').openFolderPaths = [];

            // Load filter settings from localStorage
            const savedFilters = localStorage.getItem('filterSettings');
            if (savedFilters) {
                this.filterSettings = Object.assign({}, this.filterSettings, JSON.parse(savedFilters));
            }

            // --- NEW: Watch all filters and trigger a new search ---
            this.$watch('searchQuery', () => this.fetchVideos(true));
            this.$watch('sortOrder', () => this.fetchVideos(true));
            this.$watch('currentView', () => this.fetchVideos(true), { deep: true });
            this.$watch('filterSettings', () => this.fetchVideos(true), { deep: true });
            // --- END NEW ---

            this.fetchMetadata(); // Load folders/playlists once
            this.fetchVideos(true); // Load first page of videos
            this.startScanPolling(); // Check if a scan is running on load

            // --- NEW: Refresh data on tab focus and on a timer ---
            window.addEventListener('focus', () => {
                if (!this.isScanning) {
                    this.fetchVideos(true); // 'true' = new query
                }
            });

            // Refresh every hour
            setInterval(() => {
                if (!this.isModalOpen && !this.isScanning) {
                    this.fetchVideos(true);
                }
            }, 3600000);
            // --- END NEW ---
        },

        // --- NEW: Fetch Metadata (Folders & Playlists) ---
        async fetchMetadata() {
            try {
                const response = await fetch('/api/metadata');
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                const data = await response.json();

                this.appData.folder_tree = data.folder_tree || {};
                this.appData.smartPlaylists = data.smartPlaylists || [];

            } catch (e) {
                console.error('Error fetching metadata:', e);
                this.appData.folder_tree = {};
                this.appData.smartPlaylists = [];
            }
        },

        // --- NEW: Main Paginated Video Fetcher ---
        async fetchVideos(isNewQuery = false) {
            if (this.isLoading) return; // Prevent multiple requests

            // 1. Reset state if it's a new filter/sort/view
            if (isNewQuery) {
                this.currentPage = 1;
                this.totalPages = 1;
                this.appData.videos = [];
            }

            // 2. Stop if we're already at the end of the list
            if (this.currentPage > this.totalPages) {
                return;
            }

            this.isLoading = true;

            // --- 3. EXCEPTION: Smart Playlists ---
            // Smart playlist logic is too complex to move to the server query.
            // For this view *only*, we load ALL videos and filter on the client.
            if (this.currentView.type === 'smart_playlist') {
                try {
                    // Only fetch all videos if we don't have them cached
                    if (this.appData.allVideos.length === 0) {
                        const response = await fetch('/api/videos_all');
                        if (!response.ok) throw new Error('Failed to load all videos');
                        const data = await response.json();
                        this.appData.allVideos = data.articles || [];
                    }
                    // Client-side filtering for smart playlists will be handled
                    // by the 'filteredVideos' getter. We don't populate appData.videos.
                    this.appData.videos = []; // Clear main list
                    this.totalItems = 0; // Handled by getter
                    this.totalPages = 1; // Not paginated
                } catch (e) {
                    console.error('Error fetching all videos for playlist:', e);
                } finally {
                    this.isLoading = false;
                }
                return; // Stop here
            }
            // --- END EXCEPTION ---


            // --- 4. Build URL for all other paginated views ---
            const params = new URLSearchParams({
                page: this.currentPage,
                searchQuery: this.searchQuery || '',
                sortOrder: this.sortOrder,
                viewType: this.currentView.type || 'all',
                viewId: this.currentView.id || '',
                viewAuthor: this.currentView.author || '',
                filterShorts: this.filterSettings.shorts,
                filterVR: this.filterSettings.vr,
                filterOptimized: this.filterSettings.optimized,
            });

            // --- 5. Fetch Data ---
            try {
                const response = await fetch(`/api/videos?${params.toString()}`);
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                const data = await response.json();

                // Append new videos to the list
                this.appData.videos.push(...data.articles);
                this.totalItems = data.total_items;
                this.totalPages = data.total_pages;
                this.currentPage++; // Increment for the *next* load
            } catch (e) {
                console.error('Error fetching videos:', e);
                this.appData.videos = [];
                this.totalItems = 0;
            } finally {
                this.isLoading = false;
            }
        },

        // --- Computed Properties (Getters) ---

        // OLD fullFilteredList is REMOVED.
        
        // This getter now handles the 'smart_playlist' exception.
        get filteredVideos() {
            // --- Smart Playlist Logic (Client-Side) ---
            if (this.currentView.type === 'smart_playlist') {
                const playlistId = this.currentView.id;
                const playlist = this.appData.smartPlaylists.find(p => p.id === playlistId);
                let videos = this.appData.allVideos; // Filter from the full cache

                if (playlist) {
                    playlist.filters.forEach(filter => {
                        if (filter.type === 'title') {
                            const filterValue = String(filter.value || '');
                            if (filterValue.startsWith('"') && filterValue.endsWith('"')) {
                                const searchTerm = filterValue.substring(1, filterValue.length - 1);
                                if (searchTerm) {
                                    videos = videos.filter(v => (v.title || '').includes(searchTerm));
                                }
                            } else {
                                const searchTerm = filterValue.toLowerCase();
                                videos = videos.filter(v => (v.title || '').toLowerCase().includes(searchTerm));
                            }
                        }
                        else if (filter.type === 'author') {
                            const allowedAuthors = filter.value;
                            if (allowedAuthors && allowedAuthors.length > 0) {
                                videos = videos.filter(v => allowedAuthors.includes(v.author));
                            }
                        }
                    });
                }
                // We still apply search and sort to the smart playlist
                if (this.searchQuery.trim() !== '') {
                    const query = this.searchQuery.toLowerCase();
                    videos = videos.filter(v =>
                        (v.title && v.title.toLowerCase().includes(query)) ||
                        (v.summary && v.summary.toLowerCase().includes(query)) ||
                        (v.author && v.author.toLowerCase().includes(query))
                    );
                }
                videos.sort((a, b) => this.sortLogic(a, b));
                return videos; // Return the client-filtered list
            }

            // --- All Other Views (Server-Paginated) ---
            // The server already filtered and sorted. Just return the list.
            return this.appData.videos;
        },

        // Helper for sorting smart playlists
        sortLogic(a, b) {
            if (this.currentView.type === 'history') {
                const dateA = a.last_watched ? new Date(a.last_watched) : 0;
                const dateB = b.last_watched ? new Date(b.last_watched) : 0;
                return dateB - dateA;
            }
            let dateA, dateB;
            const MAX_DATE = new Date(8640000000000000);
            const MIN_DATE = new Date(0);
            switch (this.sortOrder) {
                case 'aired_oldest':
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
                case 'aired_newest':
                default:
                    dateA = a.aired_date ? new Date(a.aired_date) : MIN_DATE;
                    dateB = b.aired_date ? new Date(b.aired_date) : MIN_DATE;
                    return dateB - dateA;
            }
        },

        getEmptyMessage() {
            if (this.isLoading && this.appData.videos.length === 0) {
                return 'Loading videos...';
            }
            if (this.isScanning) {
                if (this.scanStatus.progress > 0) {
                    return `Scanning library... (${this.scanStatus.progress} videos processed)`;
                }
                return 'Scanning library... (Starting)';
            }
            
            if (this.appData.videos.length === 0 && this.currentView.type !== 'smart_playlist') {
                if (this.searchQuery.trim() !== '') return 'No videos match your search.';
                if (!this.appData.videos || this.totalItems === 0) {
                    return 'No videos found. Click the refresh icon to scan your library.';
                }
                return 'No videos found for this filter.';
            }

            // Smart playlist empty message
            if (this.appData.videos.length === 0 && this.currentView.type === 'smart_playlist') {
                if (this.isLoading) return 'Loading videos for playlist...';
                return 'No videos match this playlist\'s filters.';
            }

            return 'No videos found.';
        },

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

        // --- Dynamic Tag Filtering Logic ---
        get currentFilterPath() {
            const viewState = Alpine.store('globalState').currentView;
            if (viewState.type !== 'folder' || this.searchQuery.trim() !== '') return null;
            return viewState.id.endsWith('/') ? viewState.id.slice(0, -1) : viewState.id;
        },

        get getDynamicTags() {
            // 1. Disable if not in a browsable view or if searching
            if (this.currentView.type !== 'all' && this.currentView.type !== 'folder') return [];
            if (this.searchQuery.trim() !== '') return [];

            let currentLevel = this.appData.folder_tree;
            if (!currentLevel || Object.keys(currentLevel).length === 0) {
                return []; // Folder tree hasn't loaded or is empty
            }

            // 2. Handle 'folder' view (we need to traverse the tree)
            if (this.currentView.type === 'folder' && this.currentView.id) {
                const pathParts = this.currentView.id.split('/');
                for (const part of pathParts) {
                    if (currentLevel && typeof currentLevel === 'object' && currentLevel[part]) {
                        currentLevel = currentLevel[part];
                    } else {
                        currentLevel = null; // Path doesn't exist or is a leaf
                        break;
                    }
                }
            }

            // 3. Return the keys of the current level
            //    (This works for 'all' view [currentLevel = root] and 'folder' view)
            if (currentLevel && typeof currentLevel === 'object') {
                return Object.keys(currentLevel).sort();
            }

            // 4. No sub-tags found
            return [];
        },

        filterByFolderTag(tag) {
            if (tag === 'clear_all') {
                this.setView('all');
                return;
            }
            const currentPath = this.currentFilterPath;
            const newPath = currentPath ? currentPath + '/' + tag : tag;
            if (this.isModalOpen) this.closeModal();
            this.setView('folder', newPath, null);
        },

        // --- Smart Playlist Actions ---
        async createPlaylist(playlistName) {
            if (!playlistName || playlistName.trim() === '') return;
            try {
                const response = await fetch('/api/playlist/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: playlistName.trim() })
                });
                const newPlaylist = await response.json();
                if (response.ok) {
                    this.appData.smartPlaylists.unshift(newPlaylist);
                } else {
                    console.error('Failed to create playlist:', newPlaylist.error);
                }
            } catch (e) {
                console.error('Error creating playlist:', e);
            }
        },

        async renamePlaylist(playlist) {
            const newName = prompt(`Rename playlist '${playlist.name}':`, playlist.name);
            if (!newName || newName.trim() === '' || newName.trim() === playlist.name) {
                return;
            }
            try {
                const response = await fetch(`/api/playlist/${playlist.id}/rename`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: newName.trim() })
                });
                if (response.ok) {
                    const updatedPlaylist = await response.json();
                    playlist.name = updatedPlaylist.name;
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

        async deletePlaylist(playlistId) {
            if (confirm('Are you sure you want to permanently delete this playlist?')) {
                try {
                    const response = await fetch(`/api/playlist/${playlistId}/delete`, {
                        method: 'POST'
                    });
                    if (response.ok) {
                        this.appData.smartPlaylists = this.appData.smartPlaylists.filter(p => p.id !== playlistId);
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

        handlePlaylistDrop(playlistId, event) {
            console.log(`Placeholder: Dropped on playlist ${playlistId}`);
        },

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
                    const index = this.appData.smartPlaylists.findIndex(p => p.id === playlistId);
                    if (index !== -1) {
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

        async saveFilterToPlaylist(playlistId, filter) {
            try {
                const response = await fetch(`/api/playlist/${playlistId}/filter`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filter: filter })
                });
                const updatedPlaylist = await response.json();
                if (response.ok) {
                    const index = this.appData.smartPlaylists.findIndex(p => p.id === playlistId);
                    if (index !== -1) {
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
            const lastView = this.filterHistory.pop();
            Alpine.store('globalState').currentView = {
                type: lastView.type,
                id: lastView.id,
                author: lastView.author
            };
            this.currentView = Alpine.store('globalState').currentView;
            this.currentTitle = lastView.title;
            // fetchVideos() will be triggered by the $watch('currentView')
        },

        setView(type, id = null, author = null) {
            const currentView = Alpine.store('globalState').currentView;
            if (currentView.type === 'folder' && type === 'folder' && currentView.id !== id) {
                this.filterHistory.push({
                    type: currentView.type,
                    id: currentView.id,
                    author: currentView.author,
                    title: this.currentTitle
                });
            }
            if (type !== 'folder') {
                this.filterHistory = [];
            }
            Alpine.store('globalState').currentView = { type: type, id: id, author: author };
            this.currentView = Alpine.store('globalState').currentView;
            this.updateTitle();
            this.isMobileMenuOpen = false;
            // fetchVideos() will be triggered by the $watch('currentView')
        },

        updateTitle() {
            const { type, id, author } = Alpine.store('globalState').currentView;
            if (type === 'all') { this.currentTitle = 'All Videos'; }
            else if (type === 'favorites') { this.currentTitle = 'Favorites'; }
            else if (type === 'watchLater') { this.currentTitle = 'Watch Later'; }
            else if (type === 'history') { this.currentTitle = 'History'; }
            else if (type === 'shorts') { this.currentTitle = 'Shorts'; }
            else if (type === 'optimized') { this.currentTitle = 'Optimized'; }
            else if (type === 'VR180') { this.currentTitle = 'VR 180 Videos'; }
            else if (type === 'VR360') { this.currentTitle = 'VR 360 Videos'; }
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

        // --- UPDATED: loadMoreVideos ---
        loadMoreVideos() {
            // Only load more if it's not a smart playlist
            if (this.currentView.type !== 'smart_playlist') {
                this.fetchVideos(false); // 'false' = append page
            }
        },

        openModal(video) {
            this.modalVideo = video;
            this.isModalOpen = true;

            this.currentVideoSrc = video.has_transcode ? video.transcode_url : video.video_url;
            this.checkTranscodeStatus(video.id);

            this.$nextTick(() => {
                if (this.$refs.videoPlayer) {
                    const lastDuration = video.watched_duration || 0;
                    if (lastDuration > 10) {
                        this.$refs.videoPlayer.currentTime = lastDuration;
                    }
                    this.$refs.videoPlayer.playbackRate = this.currentPlaybackSpeed;
                }
            });
        },

        stopAndSaveVideo() {
            if (this.modalVideo && this.$refs.videoPlayer) {
                const videoElement = this.$refs.videoPlayer;
                const durationWatched = videoElement.currentTime;
                videoElement.pause();
                videoElement.src = '';
                this.updateVideoProgress(this.modalVideo, durationWatched);
            }
        },

        closeModal() {
            this.stopAndSaveVideo();
            this.isModalOpen = false;
            this.modalVideo = null;
        },

        navigateToAuthorFilter(author) {
            this.closeModal();
            this.$nextTick(() => {
                this.setView('author', null, author);
            });
        },

        handleVideoEnd() {
            this.stopAndSaveVideo();
            if (this.isAutoplayEnabled) {
                const currentIndex = this.filteredVideos.findIndex(v => v.id === this.modalVideo.id);
                if (currentIndex !== -1 && currentIndex + 1 < this.filteredVideos.length) {
                    const nextVideo = this.filteredVideos[currentIndex + 1];
                    this.openModal(nextVideo);
                    return;
                }
            }
            this.closeModal();
        },

        playNextVideo() {
            if (!this.modalVideo) return;
            const currentIndex = this.filteredVideos.findIndex(v => v.id === this.modalVideo.id);

            if (currentIndex !== -1 && currentIndex + 1 < this.filteredVideos.length) {
                const nextVideo = this.filteredVideos[currentIndex + 1];
                this.stopAndSaveVideo();
                this.openModal(nextVideo);
            } else {
                console.log('Already at the last video.');
            }
        },

        playPreviousVideo() {
            if (!this.modalVideo) return;
            const currentIndex = this.filteredVideos.findIndex(v => v.id === this.modalVideo.id);

            if (currentIndex > 0) {
                const prevVideo = this.filteredVideos[currentIndex - 1];
                this.stopAndSaveVideo();
                this.openModal(prevVideo);
            } else {
                console.log('Already at the first video.');
            }
        },

        nextFrame() {
            if (!this.$refs.videoPlayer) return;
            const player = this.$refs.videoPlayer;
            player.pause();
            player.currentTime += (1 / 30);
        },

        previousFrame() {
            if (!this.$refs.videoPlayer) return;
            const player = this.$refs.videoPlayer;
            player.pause();
            player.currentTime -= (1 / 30);
        },

        // --- Playback Speed Controls ---
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
                nextIndex = 0;
            }
            this.setPlaybackSpeed(this.playbackRates[nextIndex]);
        },

        // --- Tag Button Functions ---
        getVideoTagText(video) {
            if (video.is_short) {
                return 'Tag: Short';
            }
            if (video.video_type === 'VR180_SBS' || video.video_type === 'VR180_TB') {
                return 'Tag: VR180';
            }
            if (video.video_type === 'VR360') {
                return 'Tag: VR360';
            }
            return 'Set Tag';
        },

        getVideoTagIcon(video) {
            if (video.is_short) {
                return 'phone_android'; // Icon for Shorts
            }
            if (video.video_type === 'VR180_SBS' || video.video_type === 'VR180_TB') {
                return 'view_in_ar'; // Icon for VR180
            }
            if (video.video_type === 'VR360') {
                return 'vrpano'; // Icon for VR360
            }
            return 'label_off'; // Icon for 'None'
        },

        cycleVideoTag(video) {
            let currentTag = 'none';
            if (video.is_short) {
                currentTag = 'short';
            } else if (video.video_type === 'VR180_SBS' || video.video_type === 'VR180_TB') {
                currentTag = 'vr180';
            } else if (video.video_type === 'VR360') {
                currentTag = 'vr360';
            }

            const cycle = {
                'none': 'short',
                'short': 'vr180',
                'vr180': 'vr360',
                'vr360': 'none',
            };
            const nextTag = cycle[currentTag] || 'none';

            fetch(`/api/video/${video.id}/set_tag`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tag: nextTag })
            })
            .then(res => {
                if (!res.ok) throw new Error('Failed to update tag');
                return res.json();
            })
            .then(updatedVideo => {
                // Update the video in the modal
                this.modalVideo = { ...this.modalVideo, ...updatedVideo };
                // Update the video in the main list
                this.updateVideoData(updatedVideo);
            })
            .catch(err => {
                console.error('Failed to update video tag:', err);
            });
        },

        // --- Content Rendering ---
        getAuthorVideoCount(author) {
            // This is no longer accurate as we don't have all videos.
            // A proper fix would be a server endpoint.
            // For now, let's just return a placeholder.
            return '...';
        },

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
            const timecodeRegex = /(^|\s)((?:(?:\d{1,2}):)?\d{1,2}:\d{2})\b/g;

            let formattedText = cleanText
                .replace(timecodeRegex, (match, p1, p2) => {
                    return `${p1}<a href="#" onclick="seekVideo(event, '${p2}')" class="text-[var(--text-highlight)] hover:underline font-semibold" title="Seek to ${p2}">${p2}</a>`;
                })
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

        // --- Background Task Handlers ---
        async scanVideoLibrary() {
            if (this.isScanning) return;
            try {
                const response = await fetch('/api/scan_videos', { method: 'POST' });
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

        startScanPolling() {
            if (this.scanPollInterval) {
                clearInterval(this.scanPollInterval);
            }
            this.isScanning = true;
            this.scanPollInterval = setInterval(async () => {
                try {
                    const response = await fetch('/api/scan/status');
                    if (!response.ok) throw new Error('Scan status poll failed');
                    const data = await response.json();
                    this.scanStatus = data;

                    if (data.status === 'idle' || data.status === 'error') {
                        this.stopScanPolling(data.status === 'idle');
                    }
                } catch (e) {
                    console.error(e);
                    this.scanStatus = { status: 'error', message: e.message };
                    this.stopScanPolling(false);
                }
            }, 3000);
        },

        stopScanPolling(wasSuccessful) {
            if (this.scanPollInterval) {
                clearInterval(this.scanPollInterval);
                this.scanPollInterval = null;
            }
            this.isScanning = false;
            if (wasSuccessful) {
                console.log('Video scan complete, fetching new data.');
                this.fetchMetadata(); // Re-fetch folders
                this.fetchVideos(true); // Re-fetch videos
            }
        },

        startThumbnailPolling() {
            if (this.thumbnailPollInterval) {
                clearInterval(this.thumbnailPollInterval);
            }
            this.isGeneratingThumbnails = true;
            this.thumbnailPollInterval = setInterval(async () => {
                try {
                    const response = await fetch('/api/thumbnails/status');
                    if (!response.ok) throw new Error('Status poll failed');
                    const data = await response.json();
                    this.thumbnailStatus = data;
                    if (data.status === 'idle' || data.status === 'error') {
                        this.stopThumbnailPolling(data.status === 'idle');
                    }
                } catch (e) {
                    console.error(e);
                    this.thumbnailStatus.status = 'error';
                    this.stopThumbnailPolling(false);
                }
            }, 2000);
        },

        stopThumbnailPolling(wasSuccessful) {
            if (this.thumbnailPollInterval) {
                clearInterval(this.thumbnailPollInterval);
                this.thumbnailPollInterval = null;
            }
            this.isGeneratingThumbnails = false;
            if (wasSuccessful) {
                console.log('Thumbnail generation complete, fetching new data.');
                this.fetchVideos(true);
            }
        },

        async generateMissingThumbnails() {
            if (this.isGeneratingThumbnails) return;
            this.isGeneratingThumbnails = true;
            try {
                const response = await fetch('/api/thumbnails/generate_missing', { method: 'POST' });
                if (response.status === 200 || response.status === 202) {
                    this.startThumbnailPolling();
                } else {
                    const result = await response.json();
                    console.error('Failed to start thumbnail generation:', result.error);
                    this.isGeneratingThumbnails = false;
                }
            } catch (e) {
                console.error('Error starting thumbnail generation:', e);
                this.isGeneratingThumbnails = false;
            }
        },

        async startTranscode(video) {
            if (this.isTranscoding) return;
            this.isTranscoding = true;
            this.transcodeStatus = { status: 'starting', message: 'Starting...', video_id: video.id };
            try {
                const response = await fetch(`/api/video/${video.id}/transcode/start`, { method: 'POST' });
                if (response.status === 202 || response.status === 409) {
                    this.startTranscodePolling(video.id);
                } else {
                    const result = await response.json();
                    console.error('Failed to start transcode:', result.error);
                    this.isTranscoding = false;
                }
            } catch (e) {
                console.error('Error starting transcode:', e);
                this.isTranscoding = false;
            }
        },

        async deleteTranscode(video) {
            if (this.isTranscoding) return;
            this.isTranscoding = true;
            try {
                const response = await fetch(`/api/video/${video.id}/transcode/delete`, { method: 'POST' });
                const updatedVideo = await response.json();
                if (response.ok) {
                    this.updateVideoData(updatedVideo);
                    this.currentVideoSrc = updatedVideo.video_url;
                    this.refreshPlayerData();
                } else {
                    console.error('Failed to delete transcode:', updatedVideo.error);
                }
            } catch (e) {
                console.error('Error deleting transcode:', e);
            } finally {
                this.isTranscoding = false;
            }
        },

        async checkTranscodeStatus(videoId) {
            try {
                const response = await fetch('/api/transcode/status');
                const data = await response.json();
                if (data.status !== 'idle' && data.video_id === videoId) {
                    this.isTranscoding = true;
                    this.transcodeStatus = data;
                    this.startTranscodePolling(videoId);
                }
            } catch (e) {
                console.error('Error checking transcode status:', e);
            }
        },

        startTranscodePolling(videoId) {
            if (this.transcodePollInterval) clearInterval(this.transcodePollInterval);
            this.isTranscoding = true;
            this.transcodePollInterval = setInterval(async () => {
                try {
                    const response = await fetch('/api/transcode/status');
                    const data = await response.json();
                    this.transcodeStatus = data;
                    if (data.status === 'idle' || data.status === 'error') {
                        this.stopTranscodePolling(data.status === 'idle');
                    }
                } catch (e) {
                    this.transcodeStatus = { status: 'error', message: 'Poll failed' };
                    this.stopTranscodePolling(false);
                }
            }, 2500);
        },

        stopTranscodePolling(wasSuccessful) {
            if (this.transcodePollInterval) {
                clearInterval(this.transcodePollInterval);
                this.transcodePollInterval = null;
            }
            this.isTranscoding = false;
            this.transcodeStatus = { status: 'idle', message: '', video_id: null };
            if (wasSuccessful) {
                this.refreshModalVideoData(this.modalVideo.id);
            }
        },

        async refreshModalVideoData(videoId) {
            // Re-fetch just this one video's data from the server
            // This is more efficient than fetching all videos again
            try {
                // We re-use the /api/videos endpoint to get one video
                const params = new URLSearchParams({ page: 1, viewType: 'all' });
                const response = await fetch(`/api/videos?${params.toString()}`);
                const data = await response.json();
                
                const newVideoData = data.articles.find(v => v.id === videoId);
                
                if (newVideoData) {
                    this.updateVideoData(newVideoData); // Update it in the list
                    this.modalVideo = newVideoData; // Update it in the modal
                    this.currentVideoSrc = this.modalVideo.has_transcode ? this.modalVideo.transcode_url : this.modalVideo.video_url;
                    this.refreshPlayerData();
                    console.log('Player source updated.');
                }
            } catch(e) {
                console.error("Failed to refresh single video data", e);
            }
        },

        refreshPlayerData() {
            this.$nextTick(() => {
                if (this.$refs.videoPlayer) {
                    this.$refs.videoPlayer.load();
                    this.$refs.videoPlayer.play();
                }
            });
        },

        updateVideoData(updatedVideo) {
            // Helper to update the video in the main list
            let index = this.appData.videos.findIndex(v => v.id === updatedVideo.id);
            if (index !== -1) {
                this.appData.videos[index] = updatedVideo;
            }
            // Also update the smart playlist cache
            index = this.appData.allVideos.findIndex(v => v.id === updatedVideo.id);
            if (index !== -1) {
                this.appData.allVideos[index] = updatedVideo;
            }
            // Update modal video if it's the one being changed
            if (this.modalVideo && this.modalVideo.id === updatedVideo.id) {
                this.modalVideo = updatedVideo;
            }
        },

        // --- Custom Thumbnail Functions ---
        async createCustomThumb() {
            if (this.isCreatingThumb || !this.$refs.videoPlayer) return;

            this.isCreatingThumb = true;
            const currentTime = this.$refs.videoPlayer.currentTime;
            const videoId = this.modalVideo.id;

            try {
                const response = await fetch(`/api/video/${videoId}/thumbnail/create_at_time`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ timestamp: currentTime })
                });

                const updatedVideo = await response.json();
                if (response.ok) {
                    this.updateVideoData(updatedVideo);
                } else {
                    console.error("Failed to create custom thumb:", updatedVideo.error);
                }
            } catch (e) {
                console.error("Error creating custom thumb:", e);
            } finally {
                this.isCreatingThumb = false;
            }
        },

        async deleteCustomThumb() {
            if (this.isCreatingThumb) return;

            this.isCreatingThumb = true;
            const videoId = this.modalVideo.id;

            try {
                const response = await fetch(`/api/video/${videoId}/thumbnail/delete_custom`, {
                    method: 'POST'
                });

                const updatedVideo = await response.json();
                if (response.ok) {
                    this.updateVideoData(updatedVideo);
                } else {
                    console.error("Failed to delete custom thumb:", updatedVideo.error);
                }
            } catch (e) {
                console.error("Error deleting custom thumb:", e);
            } finally {
                this.isCreatingThumb = false;
            }
        },

        // --- Global Filter Button Helpers ---
        cycleShortsFilter() {
            if (this.filterSettings.shorts === 'normal') this.filterSettings.shorts = 'solo';
            else if (this.filterSettings.shorts === 'solo') this.filterSettings.shorts = 'hide';
            else if (this.filterSettings.shorts === 'hide') this.filterSettings.shorts = 'normal';
        },
        cycleVRFilter() {
            if (this.filterSettings.vr === 'normal') this.filterSettings.vr = 'solo';
            else if (this.filterSettings.vr === 'solo') this.filterSettings.vr = 'hide';
            else if (this.filterSettings.vr === 'hide') this.filterSettings.vr = 'normal';
        },
        cycleOptimizedFilter() {
            if (this.filterSettings.optimized === 'normal') this.filterSettings.optimized = 'solo';
            else if (this.filterSettings.optimized === 'solo') this.filterSettings.optimized = 'hide';
            else if (this.filterSettings.optimized === 'hide') this.filterSettings.optimized = 'normal';
        }
    };
}

/**
 * Globally accessible function to seek the main video player.
 */
function seekVideo(event, timestampString) {
    event.preventDefault();
    const player = document.getElementById('main-video-player');
    if (!player) {
        console.error('Video player element not found.');
        return;
    }

    const parts = timestampString.split(':').map(Number);
    let seconds = 0;

    try {
        if (parts.length === 3) {
            seconds = (parts[0] * 3600) + (parts[1] * 60) + parts[2];
        } else if (parts.length === 2) {
            seconds = (parts[0] * 60) + parts[1];
        } else {
            console.warn('Unrecognized timestamp format:', timestampString);
            return;
        }
        player.currentTime = seconds;
        player.play();
    } catch (e) {
        console.error('Error parsing timestamp:', timestampString, e);
    }
}

/**
 * Alpine.js component for managing the filter selection.
 */
function filterEditor(playlistId, appData) {
    return {
        playlistId: playlistId,
        selectedType: 'title',
        textValue: '',
        selectedAuthors: [],
        typeLabels: {
            'title': 'Title Content',
            'author': 'Author'
        },

        get allAuthors() {
            // This now filters from the 'allVideos' cache
            const videos = appData.allVideos || [];
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
                filterValue = this.selectedAuthors;
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

            this.$root.parentElement.__x.$data.isEditingFilters = false;
        }
    };
}

/**
 * Alpine.js component for the recursive folder tree.
 */
function folderTree(tree, basePath = '') {
    return {
        tree: tree,
        basePath: basePath,

        isOpen(path) { return Alpine.store('globalState').openFolderPaths.includes(path); },
        isCurrentView(path) {
            const current = Alpine.store('globalState').currentView;
            return current.type === 'folder' && current.id === path;
        },
        toggle(path) {
            this.$root.parentElement.__x.$data.toggleFolder(path);
        },
        setView(type, path) {
            this.$root.parentElement.__x.$data.setView(type, path, null);
        },
        fullPath(name) { return this.basePath + name; },
        hasChildren(children) {
            if (!children) return false;
            const keys = Object.keys(children);
            if (keys.length === 0) return false;
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
    Alpine.data('videoApp', videoApp);
    Alpine.data('folderTree', folderTree);
    Alpine.data('filterEditor', filterEditor);

    Alpine.store('globalState', {
        openFolderPaths: [],
        currentView: { type: 'all', id: null, author: null },
    });
});