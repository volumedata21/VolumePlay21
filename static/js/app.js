/**
 * Main Alpine.js component for the VolumePlay video application.
 */
function videoApp() {
    return {
        // --- State Variables ---
        isMobileMenuOpen: false,
        isDesktopSidebarOpen: true, // NEW: Desktop toggle state
        isModalOpen: false,
        isPlaylistModalOpen: false,
        isSmartPlaylistModalOpen: false,
        currentSmartPlaylist: null,
        isLoading: false,

        // --- Task Status ---
        scanType: 'idle', 
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
            folder_tree: {},
            smartPlaylists: [],
            standardPlaylists: [],
            authorCounts: {},
            currentAuthorPosterUrl: null 
        },
        filterHistory: [],

        // --- Sidebar Collapse State (Sub-menus) ---
        sidebarState: {
            filterTags: false,
            standardPlaylists: false,
            smartPlaylists: false,
            globalFilters: false,
            libraryTools: false
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
            showDuration: true,
            showImages: false,
            showThumbnails: false
        },

        // --- Init ---
        init() {
            Alpine.store('globalState').currentView = this.currentView;
            Alpine.store('globalState').openFolderPaths = [];

            // Load saved settings
            const savedFilters = localStorage.getItem('filterSettings');
            if (savedFilters) {
                this.filterSettings = Object.assign({}, this.filterSettings, JSON.parse(savedFilters));
            }
            
            // Load Sidebar State (Open/Closed)
            const savedSidebar = localStorage.getItem('isDesktopSidebarOpen');
            if (savedSidebar !== null) {
                this.isDesktopSidebarOpen = savedSidebar === 'true';
            }

            // Watchers
            this.$watch('filterSettings', () => {
                localStorage.setItem('filterSettings', JSON.stringify(this.filterSettings));
                this.fetchVideos(true); 
            }, { deep: true });

            this.$watch('searchQuery', () => this.fetchVideos(true));
            this.$watch('sortOrder', () => this.fetchVideos(true));
            this.$watch('currentView', () => this.fetchVideos(true), { deep: true });

            this.fetchMetadata();
            this.fetchVideos(true);
            this.checkAllTaskStatuses();
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
            }
        },

        async fetchVideos(isNewQuery = false) {
            if (this.isLoading) return;

            if (isNewQuery) {
                this.currentPage = 1;
                this.totalPages = 1;
                this.appData.videos = [];
            }

            if (this.currentPage > this.totalPages && !isNewQuery) return;

            this.isLoading = true;

            // Standard Playlist Exception
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
                    } catch (e) { console.error(e); } finally { this.isLoading = false; }
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
                showImages: this.filterSettings.showImages,
                showThumbnails: this.filterSettings.showThumbnails
            });

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
            
            if (isNewQuery && this.currentView.type === 'author') {
                this.appData.currentAuthorPosterUrl = this.appData.videos.length > 0 ? this.appData.videos[0].show_poster_url : null;
            } else if (isNewQuery) {
                this.appData.currentAuthorPosterUrl = null;
            }
        },

        // --- Getters ---
        get filteredVideos() { return this.appData.videos; },
        
        get allAuthorsForFilter() {
            if (!this.appData.authorCounts) return [];
            return Object.keys(this.appData.authorCounts).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
        },

        getEmptyMessage() {
            if (this.isLoading && this.appData.videos.length === 0) return 'Loading videos...';
            if (this.scanType !== 'idle') return `Scanning library...`;
            if (this.isCleaningUp) return 'Pruning library...';
            if (this.appData.videos.length === 0) return 'No items found.';
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
            if (!currentLevel) return [];

            if (this.currentView.type === 'folder' && this.currentView.id) {
                const pathParts = this.currentView.id.split('/');
                for (const part of pathParts) {
                    if (currentLevel && currentLevel[part]) currentLevel = currentLevel[part];
                    else { currentLevel = null; break; }
                }
            }
            return currentLevel ? Object.keys(currentLevel).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase())) : [];
        },

        // --- UI Actions ---
        toggleSidebarSection(section) { this.sidebarState[section] = !this.sidebarState[section]; },
        
        toggleSidebar() {
            this.isDesktopSidebarOpen = !this.isDesktopSidebarOpen;
            localStorage.setItem('isDesktopSidebarOpen', this.isDesktopSidebarOpen);
        },

        filterByFolderTag(tag) {
            if (tag === 'clear_all') { this.setView('all'); return; }
            const currentPath = this.currentFilterPath;
            const newPath = currentPath ? currentPath + '/' + tag : tag;
            if (this.isModalOpen) this.closeModal();
            this.setView('folder', newPath, null);
        },

        // Smart Playlist CRUD
        async createSmartPlaylist(name) {
            if (!name) return;
            try {
                const res = await fetch('/api/playlist/smart/create', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name}) });
                if(res.ok) this.appData.smartPlaylists.unshift(await res.json());
            } catch(e) { console.error(e); }
        },
        async deleteSmartPlaylist(id) {
            if(confirm('Delete playlist?')) {
                try {
                    await fetch(`/api/playlist/smart/${id}/delete`, {method:'POST'});
                    this.appData.smartPlaylists = this.appData.smartPlaylists.filter(p => p.id !== id);
                    if(this.currentView.type === 'smart_playlist' && this.currentView.id === id) this.setView('all');
                } catch(e) { console.error(e); }
            }
        },
        async renameSmartPlaylist(p) {
            const name = prompt('Rename:', p.name);
            if(name) {
                try {
                    const res = await fetch(`/api/playlist/smart/${p.id}/rename`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name}) });
                    if(res.ok) {
                        const updated = await res.json();
                        const idx = this.appData.smartPlaylists.findIndex(x => x.id === p.id);
                        if(idx !== -1) this.appData.smartPlaylists[idx] = updated;
                        if(this.currentView.id === p.id) this.updateTitle();
                    }
                } catch(e) { console.error(e); }
            }
        },
        openSmartPlaylistSettings(p) { this.currentSmartPlaylist = JSON.parse(JSON.stringify(p)); this.isSmartPlaylistModalOpen = true; },
        closeSmartPlaylistSettings() { this.isSmartPlaylistModalOpen = false; this.currentSmartPlaylist = null; },
        async saveSmartPlaylistSettings(filters) {
            if(!this.currentSmartPlaylist) return;
            try {
                const res = await fetch(`/api/playlist/smart/${this.currentSmartPlaylist.id}/update_filters`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({filters}) });
                if(res.ok) {
                    const updated = await res.json();
                    const idx = this.appData.smartPlaylists.findIndex(p => p.id === this.currentSmartPlaylist.id);
                    if(idx !== -1) this.appData.smartPlaylists[idx] = updated;
                    this.closeSmartPlaylistSettings();
                    if(this.currentView.id === this.currentSmartPlaylist.id) this.fetchVideos(true);
                }
            } catch(e) { console.error(e); }
        },

        // Standard Playlist CRUD
        async openPlaylistModal(vidId) {
            try {
                const res = await fetch(`/api/video/${vidId}/playlists`);
                if(res.ok) { this.appData.standardPlaylists = await res.json(); this.isPlaylistModalOpen = true; }
            } catch(e) { console.error(e); }
        },
        async createStandardPlaylist(name, vidId=null) {
            if(!name) return;
            try {
                const res = await fetch('/api/playlist/standard/create', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name, video_id:vidId}) });
                if(res.ok) this.appData.standardPlaylists = await res.json();
            } catch(e) { console.error(e); }
        },
        async toggleVideoInPlaylist(pId, vId) {
            try {
                const res = await fetch('/api/playlist/toggle_video', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({playlist_id:pId, video_id:vId}) });
                if(res.ok) this.appData.standardPlaylists = await res.json();
            } catch(e) { console.error(e); }
        },

        // Navigation
        goBackOneFilter() {
            if (this.filterHistory.length === 0) return;
            const last = this.filterHistory.pop();
            this.setView(last.type, last.id, last.author);
        },
        setView(type, id = null, author = null) {
            const current = Alpine.store('globalState').currentView;
            if (current.type === 'folder' && type === 'folder' && current.id !== id) {
                this.filterHistory.push({ type: current.type, id: current.id, author: current.author });
            }
            if (type !== 'folder') this.filterHistory = [];
            Alpine.store('globalState').currentView = { type, id, author };
            this.currentView = Alpine.store('globalState').currentView;
            this.updateTitle();
            this.isMobileMenuOpen = false;
        },
        updateTitle() {
            const { type, id, author } = this.currentView;
            if (type === 'author') this.currentTitle = `Author: ${author}`;
            else if (type === 'folder') this.currentTitle = `Folder: ${id ? id.split('/').pop() : 'Root'}`;
            else if (type === 'smart_playlist') {
                const p = this.appData.smartPlaylists.find(x => x.id === id);
                this.currentTitle = `Playlist: ${p ? p.name : 'Unknown'}`;
            }
            else if (type === 'standard_playlist') {
                const p = this.appData.standardPlaylists.find(x => x.id === id);
                this.currentTitle = `Playlist: ${p ? p.name : 'Unknown'}`;
            }
            else this.currentTitle = 'All Videos';
        },
        loadMoreVideos() {
            if (this.currentView.type !== 'smart_playlist' && this.currentView.type !== 'standard_playlist') {
                this.fetchVideos(false);
            }
        },

        // Modal / Player
        async openModal(video) {
            if (document.pictureInPictureElement) await document.exitPictureInPicture();
            this.modalVideo = video;
            this.isModalOpen = true;
            
            if (video.media_type === 'image') {
                this.currentVideoSrc = video.video_url; 
            } else {
                this.currentVideoSrc = video.has_transcode ? video.transcode_url : video.video_url;
            }

            this.$nextTick(() => {
                if (video.media_type === 'video' && this.$refs.videoPlayer) {
                    const lastDuration = video.watched_duration || 0;
                    if (lastDuration > 10) this.$refs.videoPlayer.currentTime = lastDuration;
                    this.$refs.videoPlayer.playbackRate = this.currentPlaybackSpeed;
                }
            });
        },
        stopAndSaveVideo() {
            if (this.modalVideo && this.modalVideo.media_type === 'video' && this.$refs.videoPlayer) {
                const dur = this.$refs.videoPlayer.currentTime;
                this.$refs.videoPlayer.pause();
                this.updateVideoProgress(this.modalVideo, dur);
            }
        },
        closeModal() {
            if (!document.pictureInPictureElement) {
                this.stopAndSaveVideo();
                this.modalVideo = null;
            }
            this.isModalOpen = false;
        },
        handleEnterPiP() { this.isModalOpen = false; },
        handleLeavePiP() { this.stopAndSaveVideo(); this.modalVideo = null; },
        handleVideoEnd() {
            this.stopAndSaveVideo();
            if (this.isAutoplayEnabled) this.playNextVideo();
            else this.closeModal();
        },
        navigateToAuthorFilter(author) {
            this.closeModal();
            this.$nextTick(() => this.setView('author', null, author));
        },
        
        // Playback Controls
        playNextVideo() { this.cycleVideo(1); },
        playPreviousVideo() { this.cycleVideo(-1); },
        cycleVideo(dir) {
            if (!this.modalVideo) return;
            const idx = this.filteredVideos.findIndex(v => v.id === this.modalVideo.id);
            if (idx !== -1 && idx + dir >= 0 && idx + dir < this.filteredVideos.length) {
                this.stopAndSaveVideo();
                this.openModal(this.filteredVideos[idx + dir]);
            }
        },
        nextFrame() { if(this.$refs.videoPlayer) { this.$refs.videoPlayer.pause(); this.$refs.videoPlayer.currentTime += (1/30); }},
        previousFrame() { if(this.$refs.videoPlayer) { this.$refs.videoPlayer.pause(); this.$refs.videoPlayer.currentTime -= (1/30); }},
        setPlaybackSpeed(s) { this.currentPlaybackSpeed = s; if(this.$refs.videoPlayer) this.$refs.videoPlayer.playbackRate = s; },
        cyclePlaybackSpeed() {
            const idx = this.playbackRates.indexOf(this.currentPlaybackSpeed);
            this.setPlaybackSpeed(this.playbackRates[(idx + 1) % this.playbackRates.length]);
        },

        // Tags & Metadata
        getVideoTagText(v) {
            if (v.is_short) return 'Tag: Short';
            if (v.video_type === 'VR180_SBS') return 'Tag: VR180';
            if (v.video_type === 'VR360') return 'Tag: VR360';
            return 'Set Tag';
        },
        getVideoTagIcon(v) {
            if (v.is_short) return 'phone_android';
            if (v.video_type === 'VR180_SBS') return 'view_in_ar';
            if (v.video_type === 'VR360') return 'vrpano';
            return 'label';
        },
        cycleVideoTag(v) {
            const map = { 'none':'short', 'short':'vr180', 'vr180':'vr360', 'vr360':'none' };
            let cur = v.is_short ? 'short' : (v.video_type === 'VR180_SBS' ? 'vr180' : (v.video_type === 'VR360' ? 'vr360' : 'none'));
            fetch(`/api/video/${v.id}/set_tag`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({tag: map[cur]})})
                .then(r => r.json()).then(upd => { this.updateVideoData(upd); });
        },
        
        // Formatting
        getAuthorVideoCount(a) { return `${this.appData.authorCounts[a || 'Unknown Show'] || 0} Videos`; },
        formatFileSize(b) {
            if(!b) return '0 B'; const k=1024; const s=['B','KB','MB','GB','TB'];
            const i=Math.floor(Math.log(b)/Math.log(k)); return parseFloat((b/Math.pow(k,i)).toFixed(2))+' '+s[i];
        },
        formatDuration(s) {
            if(!s) return '0:00'; const h=Math.floor(s/3600); const m=Math.floor((s%3600)/60); const sec=Math.floor(s%60);
            return h>0 ? `${h}:${m.toString().padStart(2,'0')}:${sec.toString().padStart(2,'0')}` : `${m}:${sec.toString().padStart(2,'0')}`;
        },
        formatDateAgo(d1, d2) {
            const d = new Date(d1 || d2); if(isNaN(d)) return '';
            const s = Math.floor((new Date() - d)/1000);
            if(s<60) return 'just now';
            const times = {year:31536000, month:2592000, week:604800, day:86400, hour:3600, minute:60};
            for(let k in times) { const c=Math.floor(s/times[k]); if(c>0) return `${c} ${k}${c>1?'s':''} ago`; }
            return 'just now';
        },
        formatVideoDescription(t) {
            if(!t) return 'No summary.';
            return t.replace(/(\d{1,2}:\d{2})/g, '<a href="#" onclick="seekVideo(event, \'$1\')">$1</a>').replace(/\n/g, '<br>');
        },

        // Tasks & Mutators
        async updateVideoProgress(v, d) {
            if(d<4) return;
            try {
                const res = await fetch(`/api/video/${v.id}/progress`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({duration_watched:Math.floor(d)})});
                if(res.ok) { const r = await res.json(); v.watched_duration = r.watched_duration; v.last_watched = r.last_watched; }
            } catch(e) { console.error(e); }
        },
        async toggleFavorite(v) {
            v.is_favorite = !v.is_favorite;
            fetch(`/api/article/${v.id}/favorite`, {method:'POST'});
        },
        async toggleBookmark(v) {
            v.is_read_later = !v.is_read_later;
            fetch(`/api/article/${v.id}/bookmark`, {method:'POST'});
        },
        
        // Scan / Tasks
        async checkAllTaskStatuses() {
            try {
                let r = await fetch('/api/scan/status'); let d = await r.json();
                if(d.status !== 'idle') { this.scanStatus=d; this.scanType=d.message.includes('Full')?'full':'new'; this.startScanPolling(); }
                
                r = await fetch('/api/thumbnails/status'); d = await r.json();
                if(d.status !== 'idle') { this.thumbnailStatus=d; this.isGeneratingThumbnails=true; this.startThumbnailPolling(); }
                
                r = await fetch('/api/library/cleanup/status'); d = await r.json();
                if(d.status !== 'idle') { this.cleanupStatus=d; this.isCleaningUp=true; this.startCleanupPolling(); }
            } catch(e) {}
        },
        
        async scanNewVideos() { this.triggerScan(false); },
        async scanFullLibrary() { if(confirm('Scan all?')) this.triggerScan(true); },
        async triggerScan(full) {
            if(this.isAnyTaskRunning()) return;
            this.scanType = full ? 'full' : 'new';
            try {
                const res = await fetch('/api/scan_videos', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({full_scan:full}) });
                if(res.status===202) this.startScanPolling();
            } catch(e) { this.scanType='idle'; }
        },
        startScanPolling() {
            if(this.scanPollInterval) return;
            this.scanPollInterval = setInterval(async ()=>{
                const d = await (await fetch('/api/scan/status')).json();
                this.scanStatus = d;
                if(d.status==='idle' || d.status==='error') { 
                    clearInterval(this.scanPollInterval); this.scanPollInterval=null; this.scanType='idle'; 
                    if(d.status==='idle') { this.fetchMetadata(); this.fetchVideos(true); }
                }
            }, 3000);
        },
        
        // Thumbnails & Cleanup
        async generateMissingThumbnails() {
            this.isGeneratingThumbnails=true;
            await fetch('/api/thumbnails/generate_missing', {method:'POST'});
            this.startThumbnailPolling();
        },
        startThumbnailPolling() {
            if(this.thumbnailPollInterval) return;
            this.thumbnailPollInterval = setInterval(async ()=>{
                const d = await (await fetch('/api/thumbnails/status')).json();
                this.thumbnailStatus = d;
                if(d.status==='idle' || d.status==='error') { 
                    clearInterval(this.thumbnailPollInterval); this.thumbnailPollInterval=null; this.isGeneratingThumbnails=false; 
                    if(d.status==='idle') this.fetchVideos(true);
                }
            }, 2000);
        },
        async cleanupLibrary() {
            if(confirm('Prune library?')) {
                this.isCleaningUp=true;
                await fetch('/api/library/cleanup', {method:'POST'});
                this.startCleanupPolling();
            }
        },
        startCleanupPolling() {
            if(this.cleanupPollInterval) return;
            this.cleanupPollInterval = setInterval(async ()=>{
                const d = await (await fetch('/api/library/cleanup/status')).json();
                this.cleanupStatus = d;
                if(d.status==='idle' || d.status==='error') { 
                    clearInterval(this.cleanupPollInterval); this.cleanupPollInterval=null; this.isCleaningUp=false;
                    if(d.status==='idle') { this.fetchMetadata(); this.fetchVideos(true); }
                }
            }, 3000);
        },

        // Transcoding
        isQueued(id) { return this.transcodeQueue.includes(id); },
        isCurrentlyTranscoding(id) { return this.transcodeStatus.video_id === id && this.transcodeStatus.status !== 'idle'; },
        isOptimizing(id) { return this.isQueued(id) || this.isCurrentlyTranscoding(id); },
        async startTranscode(v) {
            if(this.isOptimizing(v.id)) return;
            this.transcodeQueue.push(v.id); this.processTranscodeQueue(); this.startTranscodePolling();
        },
        async deleteTranscode(v) {
            try {
                const res = await fetch(`/api/video/${v.id}/transcode/delete`, {method:'POST'});
                if(res.ok) this.updateVideoData(await res.json());
            } catch(e) { console.error(e); }
        },
        async processTranscodeQueue() {
            if(this.transcodeStatus.status !== 'idle' || this.transcodeQueue.length === 0) return;
            const vid = this.transcodeQueue[0];
            const res = await fetch(`/api/video/${vid}/transcode/start`, {method:'POST'});
            if(res.status===202) { 
                this.transcodeQueue.shift(); 
                this.transcodeStatus = {status:'starting', message:'Starting...', video_id:vid};
            } else { this.transcodeQueue.shift(); }
        },
        startTranscodePolling() {
            if(this.transcodePollInterval) return;
            this.transcodePollInterval = setInterval(async () => {
                const d = await (await fetch('/api/transcode/status')).json();
                const oldVid = this.transcodeStatus.video_id;
                this.transcodeStatus = d;
                if(d.status==='idle') {
                    if(oldVid) this.refreshModalVideoData(oldVid);
                    if(this.transcodeQueue.length > 0) this.processTranscodeQueue();
                    else { clearInterval(this.transcodePollInterval); this.transcodePollInterval=null; }
                }
            }, 3000);
        },
        async refreshModalVideoData(id) {
            const res = await fetch(`/api/videos?viewType=video&viewId=${id}`);
            const d = await res.json();
            if(d.articles && d.articles.length > 0) {
                const nv = d.articles[0];
                this.updateVideoData(nv);
                if(this.modalVideo && this.modalVideo.id === id) {
                    this.modalVideo = nv; 
                    this.currentVideoSrc = nv.has_transcode ? nv.transcode_url : nv.video_url;
                }
            }
        },
        updateVideoData(nv) {
            const idx = this.appData.videos.findIndex(v => v.id === nv.id);
            if(idx !== -1) this.appData.videos[idx] = nv;
        },
        toggleFolder(path) {
            const openPaths = Alpine.store('globalState').openFolderPaths;
            const index = openPaths.indexOf(path);
            if (index === -1) Alpine.store('globalState').openFolderPaths.push(path);
            else Alpine.store('globalState').openFolderPaths.splice(index, 1);
        },
        
        // Custom Thumbnails
        async createCustomThumb() {
            if (this.isCreatingThumb || !this.$refs.videoPlayer) return;
            this.isCreatingThumb = true;
            try {
                const res = await fetch(`/api/video/${this.modalVideo.id}/thumbnail/create_at_time`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({timestamp: this.$refs.videoPlayer.currentTime})});
                if(res.ok) this.updateVideoData(await res.json());
            } catch(e){} finally { this.isCreatingThumb = false; }
        },
        async deleteCustomThumb() {
            if (this.isCreatingThumb) return;
            this.isCreatingThumb = true;
            try {
                const res = await fetch(`/api/video/${this.modalVideo.id}/thumbnail/delete_custom`, {method:'POST'});
                if(res.ok) this.updateVideoData(await res.json());
            } catch(e){} finally { this.isCreatingThumb = false; }
        },

        // Filter Cycles
        cycleShortsFilter() { this.filterSettings.shorts = this.cycleFilterState(this.filterSettings.shorts); },
        cycleVRFilter() { this.filterSettings.vr = this.cycleFilterState(this.filterSettings.vr); },
        cycleOptimizedFilter() { this.filterSettings.optimized = this.cycleFilterState(this.filterSettings.optimized); },
        cycleFilterState(s) { return s==='normal'?'solo':(s==='solo'?'hide':'normal'); }
    };
}

// --- Global Helpers ---
function seekVideo(event, timestampString) {
    event.preventDefault();
    const player = document.getElementById('main-video-player');
    if (player) {
        const parts = timestampString.split(':').map(Number);
        let seconds = 0;
        if (parts.length === 3) seconds = (parts[0] * 3600) + (parts[1] * 60) + parts[2];
        else if (parts.length === 2) seconds = (parts[0] * 60) + parts[1];
        player.currentTime = seconds; player.play();
    }
}

// --- Components ---
function folderTree(tree, basePath = '') {
    return {
        tree: tree, basePath: basePath,
        isOpen(path) { return Alpine.store('globalState').openFolderPaths.includes(path); },
        isCurrentView(path) { const c = Alpine.store('globalState').currentView; return c.type === 'folder' && c.id === path; },
        toggle(path) { this.$root.parentElement.__x.$data.toggleFolder(path); },
        setView(type, path) { this.$root.parentElement.__x.$data.setView(type, path, null); },
        fullPath(name) { return this.basePath + name; },
        hasChildren(c) { return c && Object.keys(c).length > 0; },
        sortedEntries(obj) { return obj ? Object.entries(obj).sort((a, b) => a[0].localeCompare(b[0])) : []; }
    }
}

function smartPlaylistEditor(playlist, allAuthors) {
    return {
        allAuthors: allAuthors || [],
        checkedAuthors: [], otherFilters: [],
        newRuleType: 'title', newRuleOperator: 'gt', newRuleValue: '', newRuleDuration: 0, newRuleDurationUnit: 'minutes',
        init() {
            const authorRule = playlist.filters.find(f => f.type === 'author');
            if (authorRule && Array.isArray(authorRule.value)) this.checkedAuthors = [...authorRule.value];
            this.otherFilters = playlist.filters.filter(f => f.type !== 'author');
        },
        addRule() {
            let nf = { id: `filter_${Date.now()}`, type: this.newRuleType };
            if (this.newRuleType === 'title') {
                if (!this.newRuleValue.trim()) return;
                nf.value = this.newRuleValue.split(',').map(s => s.trim()).filter(s => s);
            } else if (this.newRuleType === 'duration') {
                let d = parseInt(this.newRuleDuration);
                if (isNaN(d) || d <= 0) return;
                if (this.newRuleDurationUnit === 'minutes') d *= 60; else if (this.newRuleDurationUnit === 'hours') d *= 3600;
                nf.operator = this.newRuleOperator; nf.value = d;
            }
            this.otherFilters.push(nf); this.newRuleValue = ''; this.newRuleDuration = 0;
        },
        removeRule(id) { this.otherFilters = this.otherFilters.filter(f => f.id !== id); },
        getRuleLabel(f) {
            if(f.type==='title') return `Title contains: [${f.value.join(', ')}]`;
            if(f.type==='duration') return `Duration ${f.operator==='gt'?'>':'<'} ${f.value}s`;
            return 'Unknown';
        },
        async saveSettings() {
            let final = [...this.otherFilters];
            if (this.checkedAuthors.length > 0) final.push({ id: 'author_filter_group', type: 'author', value: this.checkedAuthors });
            this.$dispatch('save-smart-playlist-filters', final);
        }
    };
}

// --- Alpine Init ---
document.addEventListener('alpine:init', () => {
    Alpine.data('videoApp', videoApp);
    Alpine.data('folderTree', folderTree);
    Alpine.data('smartPlaylistEditor', smartPlaylistEditor);
    Alpine.store('globalState', { openFolderPaths: [], currentView: { type: 'all', id: null, author: null } });
});