/**
 * Main Alpine.js component for the VolumePlay video application.
 */
function videoApp() {
    return {
        // --- State Variables ---
        isMobileMenuOpen: false,
        isModalOpen: false,
        isPlaylistModalOpen: false,
        isSmartPlaylistModalOpen: false,
        currentSmartPlaylist: null,
        // allAuthorsForFilter: [], // REMOVED: Replaced with a getter
        isLoading: false,

        // --- Task Status ---
        scanType: 'idle', // 'idle', 'new', 'full'
        scanStatus: { status: 'idle', message: '', progress: 0 },
        scanPollInterval: null,
        isGeneratingThumbnails: false,
        thumbnailStatus: { status: 'idle', message: '', progress: 0, total: 0 },
        thumbnailPollInterval: null,
        isCleaningUp: false,
        cleanupStatus: { status: 'idle', message: '', progress: 0 },
        cleanupPollInterval: null,
        transcodeQueue: [],
        transcodeStatus: { status: 'idle', message: '', video_id: null },
        transcodePollInterval: null,
        isCreatingThumb: false,

        // --- Player State ---
        isAutoplayEnabled: true,
        currentPlaybackSpeed: 1.0,
        playbackRates: [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0],

        // --- View & Data State ---
        currentView: { type: 'all', id: null, author: null },
        currentTitle: 'All Videos',
        modalVideo: null,
        currentVideoSrc: '',
        searchQuery: '',
        sortOrder: 'aired_newest',
        appData: {
            videos: [],
            // allVideos: [], // REMOVED: This was the source of the performance bug
            folder_tree: {},
            smartPlaylists: [],
            standardPlaylists: [],
            authorCounts: {},
            currentAuthorPosterUrl: null // For author poster in header
        },
        filterHistory: [],

        // --- Sidebar Collapse State ---
        sidebarState: {
            filterTags: true,
            standardPlaylists: true,
            smartPlaylists: true,
            globalFilters: true,
            libraryTools: true
        },

        // --- Pagination State ---
        currentPage: 1,
        totalPages: 1,
        totalItems: 0,

        // --- Global Filter Settings ---
        filterSettings: {
            shorts: 'normal',
            vr: 'normal',
            optimized: 'normal',
            showTitles: true,
            showFooter: true,
            showDuration: true
        },

        // --- Init ---
        init() {
            Alpine.store('globalState').currentView = this.currentView;
            Alpine.store('globalState').openFolderPaths = [];

            // Load saved global filters
            const defaultFilters = { shorts: 'normal', vr: 'normal', optimized: 'normal', showTitles: true, showFooter: true, showDuration: true };
            const savedFilters = localStorage.getItem('filterSettings');
            if (savedFilters) {
                // Merge saved settings on top of the defaults
                this.filterSettings = Object.assign({}, defaultFilters, JSON.parse(savedFilters));
            } else {
                this.filterSettings = defaultFilters;
            }
            // Save global filters on change
            this.$watch('filterSettings', () => {
                localStorage.setItem('filterSettings', JSON.stringify(this.filterSettings));
            }, { deep: true });


            // Load saved sidebar state
            const savedSidebarState = localStorage.getItem('sidebarState');
            if (savedSidebarState) {
                this.sidebarState = Object.assign({}, this.sidebarState, JSON.parse(savedSidebarState));
            }
            // Save sidebar state on change
            this.$watch('sidebarState', () => {
                localStorage.setItem('sidebarState', JSON.stringify(this.sidebarState));
            }, { deep: true });

            // Watch core view filters
            this.$watch('searchQuery', () => this.fetchVideos(true));
            this.$watch('sortOrder', () => this.fetchVideos(true));
            this.$watch('currentView', () => this.fetchVideos(true), { deep: true });
            this.$watch('filterSettings', () => this.fetchVideos(true), { deep: true }); // Re-fetch on global filter change

            this.fetchMetadata();
            this.fetchVideos(true);
            // this.fetchAllVideosForCache(); // REMOVED: This was the performance bottleneck
            
            // Check for running tasks *once* on load
            this.checkAllTaskStatuses();
            
            // The transcode poller is the only one that runs 24/7 (to manage the queue)
            this.startTranscodePolling(); 

            window.addEventListener('focus', () => {
                if (!this.isAnyTaskRunning()) {
                    this.fetchVideos(true);
                }
            });

            setInterval(() => {
                if (!this.isModalOpen && !this.isPlaylistModalOpen && !this.isSmartPlaylistModalOpen && !this.isAnyTaskRunning()) {
                    this.fetchVideos(true);
                }
            }, 3600000);
        },

        // --- Check status on boot ---
        async checkAllTaskStatuses() {
            try {
                // Check for running scans
                let response = await fetch('/api/scan/status');
                let data = await response.json();
                if (data.status !== 'idle') {
                    console.log("Found a running scan on load. Starting poller.");
                    this.isScanning = true;
                    this.scanType = data.message.includes('Full') ? 'full' : 'new';
                    this.scanStatus = data;
                    this.startScanPolling();
                }

                // Check for running thumbnail generation
                response = await fetch('/api/thumbnails/status');
                data = await response.json();
                if (data.status !== 'idle') {
                    console.log("Found a running thumbnail job on load. Starting poller.");
                    this.isGeneratingThumbnails = true;
                    this.thumbnailStatus = data;
                    this.startThumbnailPolling();
                }

                // Check for running cleanup
                response = await fetch('/api/library/cleanup/status');
                data = await response.json();
                if (data.status !== 'idle') {
                    console.log("Found a running cleanup job on load. Starting poller.");
                    this.isCleaningUp = true;
                    this.cleanupStatus = data;
                    this.startCleanupPolling();
                }
            } catch (e) {
                console.error("Error checking task statuses on boot:", e);
            }
        },

        // --- API Fetching ---
        async fetchMetadata() {
            try {
                const response = await fetch('/api/metadata');
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                const data = await response.json();
                this.appData.folder_tree = data.folder_tree || {};
                this.appData.smartPlaylists = data.smartPlaylists || [];
                this.appData.standardPlaylists = data.standardPlaylists || [];
                this.appData.authorCounts = data.author_counts || {};
            } catch (e) {
                console.error('Error fetching metadata:', e);
                this.appData.folder_tree = {};
                this.appData.smartPlaylists = [];
                this.appData.standardPlaylists = [];
            }
        },

        // REMOVED: fetchAllVideosForCache() was deleted to fix performance.

        async fetchVideos(isNewQuery = false) {
            if (this.isLoading) return;

            if (isNewQuery) {
                this.currentPage = 1;
                this.totalPages = 1;
                this.appData.videos = [];
            }

            if (this.currentPage > this.totalPages && !isNewQuery) {
                return;
            }

            this.isLoading = true;

            // REMOVED: fetchAllVideosForCache() call for smart playlists
            // if (this.currentView.type === 'smart_playlist' && isNewQuery) { ... }

            // Standard Playlist Exception: Not paginated
            if (this.currentView.type === 'standard_playlist') {
                this.isLoading = false; 
                if (isNewQuery) {
                    this.isLoading = true;
                    const params = new URLSearchParams({
                        viewType: this.currentView.type,
                        viewId: this.currentView.id,
                        sortOrder: this.sortOrder
                    });
                    try {
                        const response = await fetch(`/api/videos?${params.toString()}`);
                        const data = await response.json();
                        this.appData.videos = data.articles;
                        this.totalItems = data.total_items;
                        this.totalPages = data.total_pages;
                    } catch (e) {
                        console.error('Error fetching standard playlist videos:', e);
                    } finally {
                        this.isLoading = false;
                    }
                }
                return;
            }

            // Standard Paginated Fetch
            const params = new URLSearchParams({
                page: this.currentPage,
                per_page: 30,
                searchQuery: this.searchQuery || '',
                sortOrder: this.sortOrder,
                viewType: this.currentView.type || 'all',
                viewId: this.currentView.id || '',
                viewAuthor: this.currentView.author || '',
                filterShorts: this.filterSettings.shorts,
                filterVR: this.filterSettings.vr,
                filterOptimized: this.filterSettings.optimized,
            });

            // If it's a smart playlist, add the filters to the request
            if (this.currentView.type === 'smart_playlist' && this.currentView.id) {
                const playlist = this.appData.smartPlaylists.find(p => p.id === this.currentView.id);
                if (playlist && playlist.filters) {
                    params.append('smart_filters', JSON.stringify(playlist.filters));
                }
            }

            try {
                const response = await fetch(`/api/videos?${params.toString()}`);
                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                const data = await response.json();

                this.appData.videos.push(...data.articles);
                this.totalItems = data.total_items;
                this.totalPages = data.total_pages;
                this.currentPage++;
            } catch (e) {
                console.error('Error fetching videos:', e);
                this.appData.videos = [];
                this.totalItems = 0;
            } finally {
                this.isLoading = false;
            }
            
            // After a new author query, find the poster from the first video
            if (isNewQuery && this.currentView.type === 'author') {
                if (this.appData.videos.length > 0) {
                    this.appData.currentAuthorPosterUrl = this.appData.videos[0].show_poster_url;
                } else {
                    this.appData.currentAuthorPosterUrl = null;
                }
            } else if (isNewQuery && this.currentView.type !== 'author') {
                this.appData.currentAuthorPosterUrl = null;
            }
        },

        // --- Computed Properties (Getters) ---
        get filteredVideos() {
            // All filtering is now server-side. This getter is just a pass-through.
            return this.appData.videos;
        },

        // ADDED: New getter to replace the fetchAllVideosForCache() logic
        get allAuthorsForFilter() {
            if (!this.appData.authorCounts) return [];
            return Object.keys(this.appData.authorCounts)
                         .sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
        },

        getEmptyMessage() {
            if (this.isLoading && this.appData.videos.length === 0) {
                return 'Loading videos...';
            }
            if (this.scanType !== 'idle') {
                if (this.scanStatus.progress > 0) {
                    return `Scanning library... (${this.scanStatus.progress} videos processed)`;
                }
                return 'Scanning library... (Starting)';
            }
            if (this.isCleaningUp) {
                return 'Pruning library...';
            }
            
            if (this.appData.videos.length === 0 && this.currentView.type !== 'smart_playlist' && this.currentView.type !== 'standard_playlist') {
                if (this.searchQuery.trim() !== '') return 'No videos match your search.';
                if (!this.appData.videos || this.totalItems === 0) {
                    return 'No videos found. Click the refresh icon to scan your library.';
                }
                return 'No videos found for this filter.';
            }

            if (this.appData.videos.length === 0 && this.currentView.type === 'smart_playlist') {
                return 'No videos match this playlist\'s filters.';
            }
            
            if (this.appData.videos.length === 0 && this.currentView.type === 'standard_playlist') {
                return 'This playlist is empty.';
            }

            return 'No videos found.';
        },

        isAnyTaskRunning() {
            return (this.scanType !== 'idle') || this.isGeneratingThumbnails || this.isCleaningUp;
        },

        get libraryTaskButtonText() {
            if (this.isGeneratingThumbnails) {
                return this.thumbnailStatus.total > 0 ? `Generating... ${this.thumbnailStatus.progress} / ${this.thumbnailStatus.total}` : 'Generating...';
            }
            return 'Gen. Missing Thumbs';
        },

        get currentFilterPath() {
            const viewState = Alpine.store('globalState').currentView;
            if (viewState.type !== 'folder' || this.searchQuery.trim() !== '') return null;
            return viewState.id.endsWith('/') ? viewState.id.slice(0, -1) : viewState.id;
        },

        get getDynamicTags() {
            if (this.currentView.type !== 'all' && this.currentView.type !== 'folder') return [];
            if (this.searchQuery.trim() !== '') return [];

            let currentLevel = this.appData.folder_tree;
            if (!currentLevel || Object.keys(currentLevel).length === 0) {
                return [];
            }

            if (this.currentView.type === 'folder' && this.currentView.id) {
                const pathParts = this.currentView.id.split('/');
                for (const part of pathParts) {
                    if (currentLevel && typeof currentLevel === 'object' && currentLevel[part]) {
                        currentLevel = currentLevel[part];
                    } else {
                        currentLevel = null;
                        break;
                    }
                }
            }

            if (currentLevel && typeof currentLevel === 'object') {
                return Object.keys(currentLevel).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
            }

            return [];
        },

        // --- NEW: Sidebar Toggle Helper ---
        toggleSidebarSection(section) {
            this.sidebarState[section] = !this.sidebarState[section];
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

        startDrag(tag) {
            console.log("Dragging tag:", tag);
        },

        // --- Smart Playlist Actions ---
        async createSmartPlaylist(playlistName) {
            if (!playlistName || playlistName.trim() === '') return;
            try {
                const response = await fetch('/api/playlist/smart/create', {
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

        async renameSmartPlaylist(playlist) {
            const newName = prompt(`Rename playlist '${playlist.name}':`, playlist.name);
            if (!newName || newName.trim() === '' || newName.trim() === playlist.name) {
                return;
            }
            try {
                const response = await fetch(`/api/playlist/smart/${playlist.id}/rename`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: newName.trim() })
                });
                if (response.ok) {
                    const updatedPlaylist = await response.json();
                    const index = this.appData.smartPlaylists.findIndex(p => p.id === playlist.id);
                    if (index !== -1) {
                        this.appData.smartPlaylists[index] = updatedPlaylist;
                    }
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

        async deleteSmartPlaylist(playlistId) {
            if (confirm('Are you sure you want to permanently delete this playlist?')) {
                try {
                    const response = await fetch(`/api/playlist/smart/${playlistId}/delete`, {
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
        
        openSmartPlaylistSettings(playlist) {
            // REMOVED: Check for allVideos length
            /*
            if (this.appData.allVideos.length === 0) {
                alert("Please wait for all videos to load before editing filters.");
                this.fetchAllVideosForCache();
                return;
            }
            */
            this.currentSmartPlaylist = JSON.parse(JSON.stringify(playlist));
            this.isSmartPlaylistModalOpen = true;
        },

        closeSmartPlaylistSettings() {
            this.isSmartPlaylistModalOpen = false;
            this.currentSmartPlaylist = null;
        },

        async saveSmartPlaylistSettings(newFilters) {
            if (!this.currentSmartPlaylist) return;
            
            const playlistId = this.currentSmartPlaylist.id;
            
            try {
                const response = await fetch(`/api/playlist/smart/${playlistId}/update_filters`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filters: newFilters })
                });

                const updatedPlaylist = await response.json();
                if (!response.ok) {
                    throw new Error(updatedPlaylist.error || 'Failed to save filters');
                }

                const index = this.appData.smartPlaylists.findIndex(p => p.id === playlistId);
                if (index !== -1) {
                    this.appData.smartPlaylists[index] = updatedPlaylist;
                }
                
                this.closeSmartPlaylistSettings();
                
                if (this.currentView.type === 'smart_playlist' && this.currentView.id === playlistId) {
                    this.fetchVideos(true);
                }

            } catch (e) {
                console.error('Error saving smart playlist settings:', e);
                alert(`Error: ${e.message}`);
            }
        },
        
        // --- Standard Playlist Functions ---
        async openPlaylistModal(videoId) {
            try {
                const response = await fetch(`/api/video/${videoId}/playlists`);
                if (!response.ok) throw new Error('Failed to fetch video playlist status');
                const data = await response.json();
                this.appData.standardPlaylists = data;
                this.isPlaylistModalOpen = true;
            } catch (e) {
                console.error("Error opening playlist modal:", e);
            }
        },

        async createStandardPlaylist(name, videoId = null) {
            if (!name || name.trim() === '') return;
            try {
                const response = await fetch('/api/playlist/standard/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: name.trim(), video_id: videoId })
                });
                
                const newPlaylists = await response.json();
                if (!response.ok) {
                    throw new Error(newPlaylists.error || 'Failed to create playlist');
                }
                
                this.appData.standardPlaylists = newPlaylists;
                
            } catch (e) {
                console.error('Error creating playlist:', e);
                alert(`Error: ${e.message}`);
            }
        },

        async toggleVideoInPlaylist(playlistId, videoId) {
            try {
                const response = await fetch('/api/playlist/toggle_video', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ playlist_id: playlistId, video_id: videoId })
                });
                const updatedPlaylists = await response.json();
                if (!response.ok) {
                    throw new Error(updatedPlaylists.error || 'Failed to update playlist');
                }
                this.appData.standardPlaylists = updatedPlaylists;
            } catch (e) {
                console.error('Error toggling video in playlist:', e);
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
            else if (type === 'standard_playlist') { 
                const playlist = this.appData.standardPlaylists.find(p => p.id === id);
                this.currentTitle = `Playlist: ${playlist ? playlist.name : 'Unknown'}`;
            }
            else { this.currentTitle = 'All Videos'; }
        },

        loadMoreVideos() {
            if (this.currentView.type !== 'smart_playlist' && this.currentView.type !== 'standard_playlist') {
                this.fetchVideos(false);
            }
        },

        async openModal(video) {
            if (document.pictureInPictureElement) {
                await document.exitPictureInPicture();
            }
            this.modalVideo = video;
            this.isModalOpen = true;
            this.currentVideoSrc = video.has_transcode ? video.transcode_url : video.video_url;

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
            if (!document.pictureInPictureElement) {
                this.stopAndSaveVideo();
                this.modalVideo = null;
            }
            this.isModalOpen = false;
        },

        handleEnterPiP() {
            console.log("Entering PiP, hiding modal.");
            this.isModalOpen = false;
        },

        handleLeavePiP() {
            console.log("Leaving PiP, stopping and clearing video.");
            this.stopAndSaveVideo();
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
            if (video.is_short) return 'Tag: Short';
            if (video.video_type === 'VR180_SBS' || video.video_type === 'VR180_TB') return 'Tag: VR180';
            if (video.video_type === 'VR360') return 'Tag: VR360';
            return 'Set Tag';
        },

        getVideoTagIcon(video) {
            if (video.is_short) return 'phone_android';
            if (video.video_type === 'VR180_SBS' || video.video_type === 'VR180_TB') return 'view_in_ar';
            if (video.video_type === 'VR360') return 'vrpano';
            return 'label';
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

            const cycle = { 'none': 'short', 'short': 'vr180', 'vr180': 'vr360', 'vr360': 'none' };
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
                    this.modalVideo = { ...this.modalVideo, ...updatedVideo };
                    this.updateVideoData(updatedVideo);
                })
                .catch(err => {
                    console.error('Failed to update video tag:', err);
                });
        },

        // --- Content Rendering ---
        getAuthorVideoCount(author) {
            const authorName = author || 'Unknown Show';
            const count = this.appData.authorCounts[authorName] || 0;
            if (count === 1) return '1 Video';
            return `${count} Videos`;
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
            const intervals = { year: 31536000, month: 2592000, week: 604800, day: 86400, hour: 3600, minute: 60 };
            if (seconds < 60) return 'just now';
            let counter;
            for (const unit in intervals) {
                counter = Math.floor(seconds / intervals[unit]);
                if (counter > 0) return `${counter} ${unit}${counter !== 1 ? 's' : ''} ago`;
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
            if (!totalSeconds || totalSeconds < 1) return '0:00';
            totalSeconds = Math.round(totalSeconds);
            const hours = Math.floor(totalSeconds / 3600);
            const minutes = Math.floor((totalSeconds % 3600) / 60);
            const seconds = totalSeconds % 60;
            const pad = (num) => num.toString().padStart(2, '0');
            if (hours > 0) return `${hours}:${pad(minutes)}:${pad(seconds)}`;
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
        async scanNewVideos() {
            if (this.isAnyTaskRunning()) return;
            this.scanType = 'new';
            this.isScanning = true; 
            try {
                const response = await fetch('/api/scan_videos', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ full_scan: false })
                });
                if (response.status === 202 || response.status === 409) {
                    this.startScanPolling();
                } else {
                    const result = await response.json();
                    console.error('Failed to start scan:', result.error);
                    this.scanType = 'idle';
                    this.isScanning = false;
                }
            } catch (e) {
                console.error('Error starting scan:', e);
                this.scanType = 'idle';
                this.isScanning = false;
            }
        },

        async scanFullLibrary() {
            if (this.isAnyTaskRunning()) return;
            if (!confirm('A full scan will check every file in your library and can take a long time. Continue?')) {
                return;
            }
            this.scanType = 'full';
            this.isScanning = true;
            try {
                const response = await fetch('/api/scan_videos', { 
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ full_scan: true })
                });
                if (response.status === 202 || response.status === 409) {
                    this.startScanPolling();
                } else {
                    const result = await response.json();
                    console.error('Failed to start scan:', result.error);
                    this.scanType = 'idle';
                    this.isScanning = false;
                }
            } catch (e) {
                console.error('Error starting scan:', e);
                this.scanType = 'idle';
                this.isScanning = false;
            }
        },

        startScanPolling() {
            if (this.scanPollInterval) return;
            console.log("Starting scan poller.");
            
            const poll = async () => {
                try {
                    const response = await fetch('/api/scan/status');
                    if (!response.ok) throw new Error('Scan status poll failed');
                    const data = await response.json();
                    this.scanStatus = data;

                    if (data.status === 'idle' || data.status === 'error') {
                        const wasSuccessful = data.status === 'idle';
                        this.stopScanPolling(); 
                        if (wasSuccessful) {
                            console.log('Video scan complete, fetching new data.');
                            this.fetchMetadata();
                            this.fetchVideos(true);
                        }
                    } else {
                        this.isScanning = true;
                        this.scanType = data.message.includes('Full') ? 'full' : 'new';
                    }
                } catch (e) {
                    console.error(e);
                    this.scanStatus = { status: 'error', message: e.message };
                    this.stopScanPolling();
                }
            };
            this.scanPollInterval = setInterval(poll, 3000);
        },

        stopScanPolling() {
            if (this.scanPollInterval) {
                clearInterval(this.scanPollInterval);
                this.scanPollInterval = null;
                console.log("Stopped scan poller.");
            }
            this.isScanning = false;
            this.scanType = 'idle';
        },

        async generateMissingThumbnails() {
            if (this.isAnyTaskRunning()) return;
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

        startThumbnailPolling() {
            if (this.thumbnailPollInterval) return;
            console.log("Starting thumbnail poller.");

            const poll = async () => {
                try {
                    const response = await fetch('/api/thumbnails/status');
                    if (!response.ok) throw new Error('Status poll failed');
                    const data = await response.json();
                    this.thumbnailStatus = data;
                    if (data.status === 'idle' || data.status === 'error') {
                        const wasSuccessful = data.status === 'idle';
                        this.stopThumbnailPolling();
                        if (wasSuccessful) {
                            console.log('Thumbnail generation complete, fetching new data.');
                            this.fetchVideos(true);
                        }
                    } else {
                        this.isGeneratingThumbnails = true;
                    }
                } catch (e) {
                    console.error(e);
                    this.thumbnailStatus.status = 'error';
                    this.stopThumbnailPolling();
                }
            };
            this.thumbnailPollInterval = setInterval(poll, 2000);
        },

        stopThumbnailPolling() {
            if (this.thumbnailPollInterval) {
                clearInterval(this.thumbnailPollInterval);
                this.thumbnailPollInterval = null;
                console.log("Stopped thumbnail poller.");
            }
            this.isGeneratingThumbnails = false;
        },

        async cleanupLibrary() {
            if (this.isAnyTaskRunning()) return;
            if (!confirm('Are you sure you want to prune the library? This will permanently remove any videos from the database that are not found on disk.')) {
                return;
            }
            this.isCleaningUp = true;
            try {
                const response = await fetch('/api/library/cleanup', { method: 'POST' });
                if (response.status === 202 || response.status === 409) {
                    this.startCleanupPolling();
                } else {
                    const result = await response.json();
                    console.error('Failed to start cleanup:', result.error);
                    this.isCleaningUp = false;
                }
            } catch (e) {
                console.error('Error starting cleanup:', e);
                this.isCleaningUp = false;
            }
        },

        startCleanupPolling() {
            if (this.cleanupPollInterval) return;
            console.log("Starting cleanup poller.");
            
            const poll = async () => {
                try {
                    const response = await fetch('/api/library/cleanup/status');
                    if (!response.ok) throw new Error('Cleanup status poll failed');
                    const data = await response.json();
                    this.cleanupStatus = data;

                    if (data.status === 'idle' || data.status === 'error') {
                        const wasSuccessful = data.status === 'idle';
                        this.stopCleanupPolling();
                        if (wasSuccessful) {
                            console.log('Library cleanup complete, fetching new data.');
                            this.fetchMetadata();
                            this.fetchVideos(true);
                        }
                    } else {
                        this.isCleaningUp = true;
                    }
                } catch (e) {
                    console.error(e);
                    this.cleanupStatus = { status: 'error', message: e.message };
                    this.stopCleanupPolling();
                }
            };
            this.cleanupPollInterval = setInterval(poll, 3000);
        },

        stopCleanupPolling() {
            if (this.cleanupPollInterval) {
                clearInterval(this.cleanupPollInterval);
                this.cleanupPollInterval = null;
                console.log("Stopped cleanup poller.");
            }
            this.isCleaningUp = false;
        },

        // --- Transcode Queue Logic ---
        isQueued(videoId) {
            return this.transcodeQueue.includes(videoId);
        },
        isCurrentlyTranscoding(videoId) {
            return this.transcodeStatus.video_id === videoId && this.transcodeStatus.status !== 'idle';
        },
        isOptimizing(videoId) {
            return this.isQueued(videoId) || this.isCurrentlyTranscoding(videoId);
        },

        async startTranscode(video) {
            if (this.isOptimizing(video.id)) return;
            this.transcodeQueue.push(video.id);
            this.processTranscodeQueue();
            this.startTranscodePolling(); // Start the poller *when a job is added*
        },

        async deleteTranscode(video) {
            if (this.isOptimizing(video.id)) return; 

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
            }
        },
        
        async processTranscodeQueue() {
            if (this.transcodeStatus.status !== 'idle') return;
            if (this.transcodeQueue.length === 0) return;

            const nextVideoId = this.transcodeQueue[0];
            
            try {
                const response = await fetch(`/api/video/${nextVideoId}/transcode/start`, { method: 'POST' });
                
                if (response.status === 202) { 
                    this.transcodeQueue.shift(); 
                    this.transcodeStatus = { status: 'starting', message: 'Starting...', video_id: nextVideoId };
                } else if (response.status === 409) { 
                    console.warn('Transcode already running, poller will take over.');
                } else {
                    const result = await response.json();
                    console.error('Failed to start transcode:', result.error);
                    this.transcodeQueue.shift();
                }
            } catch (e) {
                console.error('Error starting transcode:', e);
                this.transcodeQueue.shift();
            }
        },

        stopTranscodePolling() {
            if (this.transcodePollInterval) {
                clearInterval(this.transcodePollInterval);
                this.transcodePollInterval = null;
                console.log("Stopped transcode poller (queue empty and server idle).");
            }
        },

        startTranscodePolling() {
            // This is the queue manager. Start it if it's not already running.
            if (this.transcodePollInterval) return; 
            
            console.log("Starting transcode queue poller.");

            this.transcodePollInterval = setInterval(async () => {
                try {
                    const response = await fetch('/api/transcode/status');
                    if (!response.ok) { throw new Error('Transcode poll failed'); }
                    
                    const data = await response.json();
                    const oldStatus = this.transcodeStatus.status;
                    const oldVideoId = this.transcodeStatus.video_id;
                    this.transcodeStatus = data;

                    if (oldStatus !== 'idle' && data.status === 'idle') {
                        // A job just finished
                        console.log(`Transcode job ${oldVideoId} finished. Checking queue.`);
                        this.refreshModalVideoData(oldVideoId);
                        this.processTranscodeQueue();
                    }
                    else if (data.status === 'idle' && this.transcodeQueue.length > 0) {
                        // Server is idle, but queue has items
                        this.processTranscodeQueue();
                    }
                    else if (data.status === 'idle' && this.transcodeQueue.length === 0) {
                        // Server is idle AND queue is empty. We can stop polling.
                        this.stopTranscodePolling();
                    }
                    // else: a job is running, do nothing and wait for next poll
                } catch (e) {
                    console.error("Transcode poller error:", e);
                    this.stopTranscodePolling(); // Stop polling on error
                }
            }, 3000);
        },

        async refreshModalVideoData(videoId) {
            if (!videoId) return;
            try {
                const params = new URLSearchParams({ 
                    viewType: 'video', 
                    viewId: videoId 
                });
                const response = await fetch(`/api/videos?${params.toString()}`);
                const data = await response.json();
                
                if (data.articles && data.articles.length > 0) {
                    const newVideoData = data.articles[0];
                    this.updateVideoData(newVideoData); 
                    if (this.modalVideo && this.modalVideo.id === videoId) {
                        this.modalVideo = newVideoData; 
                        this.currentVideoSrc = this.modalVideo.has_transcode ? this.modalVideo.transcode_url : this.modalVideo.video_url;
                        this.refreshPlayerData();
                    }
                    console.log('Player source updated.');
                } else {
                    this.fetchVideos(true); // Fallback
                }
            } catch (e) {
                console.error("Failed to refresh single video data", e);
                this.fetchVideos(true); // Fallback
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
            let index = this.appData.videos.findIndex(v => v.id === updatedVideo.id);
            if (index !== -1) {
                this.appData.videos[index] = updatedVideo;
            }
            // REMOVED: allVideos update is no longer needed
            // index = this.appData.allVideos.findIndex(v => v.id === updatedVideo.id);
            // if (index !== -1) {
            //     this.appData.allVideos[index] = updatedVideo;
            // }
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

/**
 * Alpine.js component for the Smart Playlist Settings modal.
 * THIS FUNCTION MUST BE DEFINED *BEFORE* THE 'alpine:init' LISTENER
 */
function smartPlaylistEditor(playlist, allAuthors) {
    return {
        // --- State ---
        allAuthors: allAuthors || [],
        checkedAuthors: [],  // Holds the names of authors to filter by
        otherFilters: [],    // Holds non-author filters (title, duration)
        
        // State for adding a new *advanced* rule
        newRuleType: 'title', // Default to title, since author is separate
        newRuleOperator: 'gt',
        newRuleValue: '',
        newRuleDuration: 0,
        newRuleDurationUnit: 'minutes',

        init() {
            // Deconstruct the saved filters into our two states
            const authorRule = playlist.filters.find(f => f.type === 'author');
            if (authorRule && Array.isArray(authorRule.value)) {
                this.checkedAuthors = [...authorRule.value];
            }
            this.otherFilters = playlist.filters.filter(f => f.type !== 'author');
        },

        addRule() {
            let newFilter = {
                id: `filter_${Date.now()}`,
                type: this.newRuleType,
            };

            if (this.newRuleType === 'title') {
                if (this.newRuleValue.trim() === '') return;
                // Split by comma, trim whitespace, remove blanks
                newFilter.value = this.newRuleValue.split(',')
                    .map(s => s.trim())
                    .filter(s => s);
                if (newFilter.value.length === 0) return;
            } 
            else if (this.newRuleType === 'duration') {
                let durationInSeconds = 0;
                const duration = parseInt(this.newRuleDuration);
                if (isNaN(duration) || duration <= 0) return;
                
                if (this.newRuleDurationUnit === 'minutes') {
                    durationInSeconds = duration * 60;
                } else if (this.newRuleDurationUnit === 'hours') {
                    durationInSeconds = duration * 3600;
                } else {
                    durationInSeconds = duration; // Fallback for seconds
                }
                
                newFilter.operator = this.newRuleOperator;
                newFilter.value = durationInSeconds;
            }

            this.otherFilters.push(newFilter);
            this.resetNewRuleForm();
        },

        removeRule(filterId) {
            this.otherFilters = this.otherFilters.filter(f => f.id !== filterId);
        },

        resetNewRuleForm() {
            this.newRuleType = 'title';
            this.newRuleOperator = 'gt';
            this.newRuleValue = '';
            this.newRuleDuration = 0;
            this.newRuleDurationUnit = 'minutes';
        },

        getRuleLabel(filter) {
            switch (filter.type) {
                case 'title':
                    return `Title contains (any of): [${filter.value.join(', ')}]`;
                case 'duration':
                    const operator = filter.operator === 'gt' ? '>' : '<';
                    // Convert seconds back to minutes/hours for display
                    const value = filter.value;
                    let displayValue = value;
                    let displayUnit = 'seconds';
                    if (value > 0 && value % 3600 === 0) {
                        displayValue = value / 3600;
                        displayUnit = 'hours';
                    } else if (value > 0 && value % 60 === 0) {
                        displayValue = value / 60;
                        displayUnit = 'minutes';
                    }
                    return `Duration is ${operator} ${displayValue} ${displayUnit}`;
                default:
                    return 'Unknown filter';
            }
        },

        async saveSettings() {
            // Reconstruct the final filters list
            let finalFilters = [...this.otherFilters];
            
            // Add the single, combined author rule IF any are checked
            if (this.checkedAuthors.length > 0) {
                finalFilters.push({
                    id: 'author_filter_group', // Use a consistent ID
                    type: 'author',
                    value: this.checkedAuthors
                });
            }
            
            // Dispatch the event with the complete, new filter list
            this.$dispatch('save-smart-playlist-filters', finalFilters);
        }
    };
}

// --- ALPINE INITIALIZATION ---
document.addEventListener('alpine:init', () => {
    Alpine.data('videoApp', videoApp);
    Alpine.data('folderTree', folderTree);
    Alpine.data('smartPlaylistEditor', smartPlaylistEditor); // Register the new editor

    Alpine.store('globalState', {
        openFolderPaths: [],
        currentView: { type: 'all', id: null, author: null },
    });
});