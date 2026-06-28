/* ===================================================================
   Mixtape Creator — Frontend Application
   =================================================================== */

(function () {
    "use strict";

    // ----------------------------------------------------------------
    // State
    // ----------------------------------------------------------------
    let tracks = [];
    let transitionModes = {}; // keyed by track filename -> mode for transition after that track
    let removedFiles = new Set(); // filenames the user removed (kept out of the playlist)
    let currentJobId = null;
    let pollTimer = null;
    let mixcloudConnected = false;
    // Which track filename is loaded in the audio element (playing OR paused).
    // null = no track loaded.
    let currentlyPlaying = null;
    let isScrubbing = false; // true while the user is dragging the seek bar
    const audioPlayer = new Audio();
    audioPlayer.preload = "none";
    audioPlayer.addEventListener("ended", () => {
        currentlyPlaying = null;
        unmountSeekBar();
        updatePlayButtons();
    });
    audioPlayer.addEventListener("play", () => updatePlayButtons());
    audioPlayer.addEventListener("pause", () => updatePlayButtons());
    // Keep the seek bar in sync with playback.
    audioPlayer.addEventListener("loadedmetadata", () => syncSeekBar(true));
    audioPlayer.addEventListener("timeupdate", () => {
        if (!isScrubbing) syncSeekBar(false);
    });

    // Monotonic counter so stale /api/tracks/reorder responses can't
    // clobber newer local mutations (e.g. rapid successive deletes).
    let reorderSeq = 0;

    // Fully release the audio element — pause, clear src, load().
    function clearAudioPlayer() {
        audioPlayer.pause();
        audioPlayer.removeAttribute("src");
        try { audioPlayer.load(); } catch (e) { /* ignore */ }
        currentlyPlaying = null;
        unmountSeekBar();
    }

    // ----------------------------------------------------------------
    // DOM references
    // ----------------------------------------------------------------
    const $trackList = document.getElementById("track-list");
    const $loading = document.getElementById("loading");
    const $totalDuration = document.getElementById("total-duration");
    const $trackCount = document.getElementById("track-count");
    const $btnBuild = document.getElementById("btn-build");
    const $btnRefresh = document.getElementById("btn-refresh");
    const $buildStatus = document.getElementById("build-status");
    const $warningBanner = document.getElementById("warning-banner");
    const $warningText = document.getElementById("warning-text");
    const $btnReset = document.getElementById("btn-reset");
    const $savedHint = document.getElementById("saved-hint");

    // ----------------------------------------------------------------
    // API helpers
    // ----------------------------------------------------------------
    async function api(url, options = {}) {
        const resp = await fetch(url, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ error: resp.statusText }));
            throw new Error(err.error || `HTTP ${resp.status}`);
        }
        return resp.json();
    }

    // ----------------------------------------------------------------
    // Load tracks
    // ----------------------------------------------------------------
    async function loadTracks() {
        $trackList.innerHTML = '<div class="loading">Loading tracks…</div>';
        $btnBuild.disabled = true;
        $warningBanner.classList.add("hidden");
        hideUploadSection();

        try {
            const resp = await fetch("/api/tracks");
            // Check for warning header (e.g. ffprobe missing)
            const warning = resp.headers.get("X-Warning");
            if (warning) {
                $warningText.textContent = warning;
                $warningBanner.classList.remove("hidden");
            }
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ error: resp.statusText }));
                throw new Error(err.error || `HTTP ${resp.status}`);
            }
            const data = await resp.json();
            tracks = data.tracks;
            // Restore the saved session: transitions (the server has already
            // randomized any first-seen track) and the removed-files set.
            transitionModes = data.transitions || {};
            removedFiles = new Set(data.removed || []);
            // Guard against any gaps (shouldn't happen — server fills these).
            tracks.forEach((t) => {
                if (!(t.file in transitionModes)) {
                    transitionModes[t.file] = "default";
                }
            });
            renderTracks();
            updateSummary(data.total_duration_display, tracks.length);
            $btnBuild.disabled = tracks.length === 0;
        } catch (err) {
            $trackList.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📂</div>
                    <p>No tracks found.</p>
                    <p style="margin-top:4px; font-size:12px; color:var(--text-muted);">
                        Add audio files to the "Music for mixtape" folder and click Refresh.
                    </p>
                </div>`;
            updateSummary("--:--", 0);
        }
    }

    // ----------------------------------------------------------------
    // Render track cards
    // ----------------------------------------------------------------
    function renderTracks() {
        if (tracks.length === 0) {
            $trackList.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📂</div>
                    <p>No tracks found.</p>
                </div>`;
            return;
        }

        $trackList.innerHTML = tracks
            .map((t, i) => {
                const isLast = i === tracks.length - 1;
                const mode = transitionModes[t.file] || "default";
                const transitionHtml = isLast
                    ? ""
                    : `<div class="track-transition">
                        <select class="transition-select" data-file="${escapeAttr(t.file)}">
                            <option value="default"${mode === "default" ? " selected" : ""}>Default</option>
                            <option value="dj-smooth"${mode === "dj-smooth" ? " selected" : ""}>DJ Smooth</option>
                            <option value="dj-random"${mode === "dj-random" ? " selected" : ""}>DJ Random</option>
                            <option value="dj-dynamic"${mode === "dj-dynamic" ? " selected" : ""}>DJ Dynamic</option>
                        </select>
                    </div>`;

                const isPlaying = currentlyPlaying === t.file && !audioPlayer.paused;
                const q = qualityBadge(t);
                return `
            <div class="track-card" data-file="${escapeAttr(t.file)}">
                <button class="track-play-btn${isPlaying ? " playing" : ""}" data-file="${escapeAttr(t.file)}" title="${isPlaying ? "Pause" : "Play"}">
                    ${isPlaying ? "⏸" : "▶"}
                </button>
                <div class="track-pos">${i + 1}</div>
                <div class="track-info">
                    <div class="track-display">${escapeHtml(t.display)}</div>
                    <div class="track-filename">${escapeHtml(t.file)}</div>
                </div>
                ${q ? `<span class="track-quality ${q.cls}" title="${escapeAttr(q.title)}">${escapeHtml(q.label)}</span>` : `<span class="track-quality track-quality--none">—</span>`}
                <div class="track-duration">${t.duration_display || "??"}</div>
                <button class="track-delete-btn" data-file="${escapeAttr(t.file)}" title="Remove from playlist">
                    ✕
                </button>
            </div>${transitionHtml}`;
            })
            .join("");

        // Attach transition dropdown listeners
        $trackList.querySelectorAll(".transition-select").forEach((sel) => {
            sel.addEventListener("change", (e) => {
                transitionModes[e.target.dataset.file] = e.target.value;
                saveSession();
            });
        });

        // Attach play button listeners
        $trackList.querySelectorAll(".track-play-btn").forEach((btn) => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                togglePlay(btn.dataset.file);
            });
            // Prevent drag-initiating pointer events on the button from starting a sort
            btn.addEventListener("pointerdown", (e) => e.stopPropagation());
            btn.addEventListener("mousedown", (e) => e.stopPropagation());
        });

        // Attach delete button listeners
        $trackList.querySelectorAll(".track-delete-btn").forEach((btn) => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                deleteTrack(btn.dataset.file);
            });
            btn.addEventListener("pointerdown", (e) => e.stopPropagation());
            btn.addEventListener("mousedown", (e) => e.stopPropagation());
        });

        initSortable();

        // Re-attach the seek bar under the active track (renderTracks rebuilds
        // the whole list, so the previously-mounted bar was discarded).
        if (currentlyPlaying && tracks.some((t) => t.file === currentlyPlaying)) {
            mountSeekBar(currentlyPlaying);
            syncSeekBar(true);
        }
    }

    // ----------------------------------------------------------------
    // Seek bar (scrub playback position of the active track)
    // ----------------------------------------------------------------
    let seekBarEl = null;

    function formatTime(secs) {
        if (!isFinite(secs) || secs < 0) secs = 0;
        const m = Math.floor(secs / 60);
        const s = Math.floor(secs % 60);
        return `${m}:${s < 10 ? "0" : ""}${s}`;
    }

    function ensureSeekBar() {
        if (seekBarEl) return seekBarEl;
        const el = document.createElement("div");
        el.className = "track-seek";
        el.innerHTML = `
            <span class="seek-time seek-cur">0:00</span>
            <input type="range" class="seek-range" min="0" max="0" value="0" step="0.1" aria-label="Seek position">
            <span class="seek-time seek-dur">0:00</span>`;
        const range = el.querySelector(".seek-range");
        // Don't let dragging the slider start a SortableJS drag.
        ["pointerdown", "mousedown"].forEach((evt) =>
            range.addEventListener(evt, (e) => e.stopPropagation())
        );
        range.addEventListener("input", () => {
            isScrubbing = true;
            el.querySelector(".seek-cur").textContent = formatTime(Number(range.value));
        });
        const commit = () => {
            if (isFinite(audioPlayer.duration)) {
                audioPlayer.currentTime = Number(range.value);
            }
            isScrubbing = false;
        };
        range.addEventListener("change", commit);
        seekBarEl = el;
        return el;
    }

    function mountSeekBar(file) {
        const el = ensureSeekBar();
        const card = $trackList.querySelector(`.track-card[data-file="${cssEscape(file)}"]`);
        if (!card) return;
        // Insert directly beneath the active track card.
        if (el.previousElementSibling !== card) {
            card.insertAdjacentElement("afterend", el);
        }
    }

    function unmountSeekBar() {
        if (seekBarEl && seekBarEl.parentNode) {
            seekBarEl.parentNode.removeChild(seekBarEl);
        }
        isScrubbing = false;
    }

    function syncSeekBar(updateMax) {
        if (!seekBarEl) return;
        const range = seekBarEl.querySelector(".seek-range");
        const dur = audioPlayer.duration;
        if (updateMax && isFinite(dur) && dur > 0) {
            range.max = dur;
            seekBarEl.querySelector(".seek-dur").textContent = formatTime(dur);
        }
        if (!isScrubbing) range.value = audioPlayer.currentTime || 0;
        seekBarEl.querySelector(".seek-cur").textContent = formatTime(audioPlayer.currentTime || 0);
    }

    // CSS.escape fallback for older engines (used in attribute selectors).
    function cssEscape(str) {
        if (window.CSS && CSS.escape) return CSS.escape(str);
        return (str || "").replace(/["\\\]]/g, "\\$&");
    }

    // ----------------------------------------------------------------
    // Play / pause
    // ----------------------------------------------------------------
    function togglePlay(file) {
        // Same track already loaded — toggle pause/resume in place
        if (currentlyPlaying === file) {
            if (audioPlayer.paused) {
                audioPlayer.play().catch((err) => {
                    console.error("Playback failed:", err);
                });
            } else {
                audioPlayer.pause();
            }
            return;
        }
        // Different track — switch source and play from the start
        currentlyPlaying = file;
        audioPlayer.src = `/api/audio/${encodeURIComponent(file)}`;
        // Reset and show the seek bar under this track immediately; it fills
        // in its duration once "loadedmetadata" fires.
        mountSeekBar(file);
        syncSeekBar(true);
        audioPlayer.play().catch((err) => {
            console.error("Playback failed:", err);
            currentlyPlaying = null;
            unmountSeekBar();
            updatePlayButtons();
        });
    }

    function updatePlayButtons() {
        $trackList.querySelectorAll(".track-play-btn").forEach((btn) => {
            const isPlaying = currentlyPlaying === btn.dataset.file && !audioPlayer.paused;
            btn.classList.toggle("playing", isPlaying);
            btn.textContent = isPlaying ? "⏸" : "▶";
            btn.title = isPlaying ? "Pause" : "Play";
        });
    }

    // ----------------------------------------------------------------
    // Delete track from playlist (does not delete file on disk)
    // ----------------------------------------------------------------
    function deleteTrack(file) {
        // Fully release the audio element if the deleted track is loaded
        // (whether playing or paused) — drops any in-flight fetch.
        if (currentlyPlaying === file) {
            clearAudioPlayer();
        }
        tracks = tracks.filter((t) => t.file !== file);
        delete transitionModes[file];
        removedFiles.add(file);
        saveSession();

        if (tracks.length === 0) {
            renderTracks();
            updateSummary("--:--", 0);
            $btnBuild.disabled = true;
            return;
        }

        // Render immediately for fast feedback, then sync with server
        // (reorderTracks will re-render with server-recomputed data).
        renderTracks();
        reorderTracks();
    }

    // ----------------------------------------------------------------
    // SortableJS drag-and-drop
    // ----------------------------------------------------------------
    let sortable = null;

    function initSortable() {
        if (sortable) sortable.destroy();

        sortable = new Sortable($trackList, {
            animation: 200,
            handle: ".track-card",
            draggable: ".track-card",
            filter: ".track-play-btn, .track-delete-btn",
            preventOnFilter: false,
            ghostClass: "sortable-ghost",
            chosenClass: "sortable-chosen",
            dragClass: "sortable-drag",
            onEnd: function (evt) {
                // Rebuild tracks array from current DOM order
                const cards = $trackList.querySelectorAll(".track-card");
                const newOrder = [];
                cards.forEach((card) => {
                    const file = card.dataset.file;
                    const t = tracks.find((tr) => tr.file === file);
                    if (t) newOrder.push(t);
                });
                tracks = newOrder;
                // Re-render to fix position numbers and transition dropdowns
                renderTracks();
                // Send new order to backend
                reorderTracks();
                saveSession();
            },
        });
    }

    // ----------------------------------------------------------------
    // Reorder tracks (send to backend for recalculation)
    // ----------------------------------------------------------------
    async function reorderTracks() {
        const order = tracks.map((t) => t.file);
        const seq = ++reorderSeq;
        try {
            const data = await api("/api/tracks/reorder", {
                method: "POST",
                body: JSON.stringify({ order }),
            });
            // Drop stale responses: if another reorder/delete happened
            // after this request was sent, its mutation is authoritative.
            if (seq !== reorderSeq) return;
            tracks = data.tracks;
            updateSummary(data.total_duration_display, tracks.length);
            // Re-render so position numbers and server-recomputed data
            // (start times, etc.) stay in sync with the cards.
            renderTracks();
        } catch (err) {
            console.error("Reorder failed:", err);
        }
    }

    // ----------------------------------------------------------------
    // Persist the working playlist (debounced auto-save)
    // ----------------------------------------------------------------
    let saveTimer = null;

    function saveSession() {
        if (saveTimer) clearTimeout(saveTimer);
        saveTimer = setTimeout(async () => {
            try {
                await api("/api/session", {
                    method: "PUT",
                    body: JSON.stringify({
                        order: tracks.map((t) => t.file),
                        removed: Array.from(removedFiles),
                        transitions: transitionModes,
                    }),
                });
                showSavedHint();
            } catch (err) {
                console.error("Session save failed:", err);
            }
        }, 400);
    }

    let savedHintTimer = null;
    function showSavedHint(text = "Saved ✓") {
        if (!$savedHint) return;
        $savedHint.textContent = text;
        $savedHint.classList.add("visible");
        if (savedHintTimer) clearTimeout(savedHintTimer);
        savedHintTimer = setTimeout(() => $savedHint.classList.remove("visible"), 1500);
    }

    // ----------------------------------------------------------------
    // Reset playlist — restore all songs (clear removed tracks)
    // ----------------------------------------------------------------
    async function resetSession() {
        const ok = await confirmModal({
            title: "Reset playlist",
            message: "Restore all songs and clear your removed tracks? Your current order and removed list will be cleared.",
            confirmText: "Restore all",
            cancelText: "Cancel",
        });
        if (!ok) return;
        try {
            await api("/api/session/reset", { method: "POST" });
            removedFiles = new Set();
            await loadTracks();
            showSavedHint("Playlist reset");
        } catch (err) {
            console.error("Reset failed:", err);
        }
    }

    // ----------------------------------------------------------------
    // Update summary display
    // ----------------------------------------------------------------
    function updateSummary(durationDisplay, count) {
        $totalDuration.textContent = durationDisplay;
        $trackCount.textContent = `${count} track${count !== 1 ? "s" : ""}`;
    }

    // ----------------------------------------------------------------
    // Build mixtape
    // ----------------------------------------------------------------
    async function buildMixtape() {
        if (tracks.length === 0) return;

        $btnBuild.disabled = true;
        $btnBuild.textContent = "⏳ Building…";
        $buildStatus.textContent = "Starting build…";
        $buildStatus.className = "build-status building";
        hideUploadSection();

        const order = tracks.map((t) => t.file);
        // Build per-transition modes array (one per adjacent pair)
        const transitions = [];
        for (let i = 0; i < tracks.length - 1; i++) {
            transitions.push(transitionModes[tracks[i].file] || "default");
        }

        const payload = {
            order,
            transitions,
        };

        try {
            const data = await api("/api/build", {
                method: "POST",
                body: JSON.stringify(payload),
            });
            currentJobId = data.job_id;
            pollBuildStatus();
        } catch (err) {
            $buildStatus.textContent = `Error: ${err.message}`;
            $buildStatus.className = "build-status error";
            $btnBuild.disabled = false;
            $btnBuild.textContent = "⚡ Build Mixtape";
        }
    }

    function pollBuildStatus() {
        if (pollTimer) clearInterval(pollTimer);

        pollTimer = setInterval(async () => {
            try {
                const data = await api(`/api/build/status/${currentJobId}`);

                if (data.status === "building") {
                    $buildStatus.textContent = data.progress || "Building…";
                } else if (data.status === "done") {
                    clearInterval(pollTimer);
                    $buildStatus.textContent = `Done! Output: ${data.output_path}`;
                    $buildStatus.className = "build-status success";
                    $btnBuild.disabled = false;
                    $btnBuild.textContent = "⚡ Build Mixtape";
                    // Clear any AI-filled fields from a previous build so
                    // the next modal open triggers fresh suggestions for
                    // the new tracklist. Only clear on success — on error
                    // we preserve whatever the user may have typed.
                    if ($uploadName) $uploadName.value = "";
                    if ($uploadDescription) $uploadDescription.value = "";
                    if ($uploadTags) $uploadTags.value = "";
                    showUploadSection();
                } else if (data.status === "error") {
                    clearInterval(pollTimer);
                    $buildStatus.textContent = `Error: ${data.error}`;
                    $buildStatus.className = "build-status error";
                    $btnBuild.disabled = false;
                    $btnBuild.textContent = "⚡ Build Mixtape";
                }
            } catch (err) {
                clearInterval(pollTimer);
                $buildStatus.textContent = `Polling error: ${err.message}`;
                $buildStatus.className = "build-status error";
                $btnBuild.disabled = false;
                $btnBuild.textContent = "⚡ Build Mixtape";
            }
        }, 1500);
    }

    // ----------------------------------------------------------------
    // Mixcloud Upload
    // ----------------------------------------------------------------
    const $btnShowUpload = document.getElementById("btn-show-upload");
    const $uploadModal = document.getElementById("upload-modal");
    const $uploadName = document.getElementById("upload-name");
    const $uploadDescription = document.getElementById("upload-description");
    const $uploadTags = document.getElementById("upload-tags");
    const $btnUpload = document.getElementById("btn-upload");
    const $btnModalClose = document.getElementById("btn-modal-close");
    const $btnModalCancel = document.getElementById("btn-modal-cancel");
    const $uploadStatus = document.getElementById("upload-status");
    const $coverPreset = document.getElementById("cover-preset");
    const $coverTextSize = document.getElementById("cover-text-size");
    const $coverPreview = document.getElementById("cover-preview");
    const $coverPreviewPlaceholder = document.getElementById("cover-preview-placeholder");
    const $coverBaseNote = document.getElementById("cover-base-note");

    let aiEnabled = false;
    let coverBaseAvailable = false;
    let coverPreviewTimer = null;

    async function checkMixcloudConnection() {
        try {
            const data = await api("/api/mixcloud/status");
            mixcloudConnected = data.connected;
            aiEnabled = !!data.ai_enabled;
        } catch {
            mixcloudConnected = false;
            aiEnabled = false;
        }
    }

    function showUploadSection() {
        if (!mixcloudConnected || !$btnShowUpload) return;
        $btnShowUpload.classList.remove("hidden");
    }

    function hideUploadSection() {
        if ($btnShowUpload) $btnShowUpload.classList.add("hidden");
    }

    async function openUploadModal() {
        $uploadModal.classList.remove("hidden");
        $uploadStatus.textContent = "";
        $uploadStatus.className = "upload-status";
        // Refresh connection/AI state in case the user added the API key
        // or connected Mixcloud in Settings after this page loaded.
        await checkMixcloudConnection();
        await checkCoverStatus();
        // Auto-fill from AI on first open per build, only if fields are empty
        if (aiEnabled && !$uploadName.value && !$uploadDescription.value && !$uploadTags.value) {
            await autofillFromAI();
        }
        // Render an initial preview if we have a title and a base image.
        schedulePreviewRefresh(0);
    }

    async function checkCoverStatus() {
        try {
            const data = await api("/api/cover/status");
            coverBaseAvailable = !!data.has_base;
            if (coverBaseAvailable) {
                $coverBaseNote.textContent = `using ${data.base_filename}`;
                $coverBaseNote.className = "cover-base-note ok";
            } else {
                $coverBaseNote.textContent = "No base image — drop one into cover/ to enable cover art";
                $coverBaseNote.className = "cover-base-note missing";
            }
        } catch {
            coverBaseAvailable = false;
            $coverBaseNote.textContent = "";
            $coverBaseNote.className = "cover-base-note";
        }
    }

    function schedulePreviewRefresh(delay = 450) {
        if (coverPreviewTimer) clearTimeout(coverPreviewTimer);
        coverPreviewTimer = setTimeout(refreshCoverPreview, delay);
    }

    function refreshCoverPreview() {
        if (!coverBaseAvailable) {
            $coverPreview.classList.add("hidden");
            $coverPreviewPlaceholder.classList.remove("hidden");
            $coverPreviewPlaceholder.textContent =
                "Drop a cover_base.jpg/.png into the cover/ folder to enable preview";
            return;
        }
        const title = ($uploadName.value || "").trim();
        if (!title) {
            $coverPreview.classList.add("hidden");
            $coverPreviewPlaceholder.classList.remove("hidden");
            $coverPreviewPlaceholder.textContent =
                "Enter a mixtape name to see the cover preview";
            return;
        }
        const preset = $coverPreset.value || "neon";
        // Cache-bust with a counter so the browser re-fetches on each change.
        const textSize = ($coverTextSize && $coverTextSize.value) || "medium";
        const bust = Date.now();
        const url = `/api/cover/preview?title=${encodeURIComponent(title)}&preset=${encodeURIComponent(preset)}&text_size=${encodeURIComponent(textSize)}&_=${bust}`;
        $coverPreview.onload = () => {
            $coverPreview.classList.remove("hidden");
            $coverPreviewPlaceholder.classList.add("hidden");
        };
        $coverPreview.onerror = () => {
            $coverPreview.classList.add("hidden");
            $coverPreviewPlaceholder.classList.remove("hidden");
            $coverPreviewPlaceholder.textContent = "Cover preview failed to render";
        };
        $coverPreview.src = url;
    }

    async function autofillFromAI() {
        const prevPlaceholderName = $uploadName.placeholder;
        const prevPlaceholderDesc = $uploadDescription.placeholder;
        const prevPlaceholderTags = $uploadTags.placeholder;
        $uploadName.placeholder = "✨ Generating…";
        $uploadDescription.placeholder = "✨ Generating description…";
        $uploadTags.placeholder = "✨ Generating tags…";
        $uploadName.disabled = true;
        $uploadDescription.disabled = true;
        $uploadTags.disabled = true;
        $uploadStatus.textContent = "✨ Generating suggestions…";
        $uploadStatus.className = "upload-status building";
        try {
            const data = await api("/api/mixcloud/suggest", { method: "POST" });
            if (data.name && !$uploadName.value) $uploadName.value = data.name;
            if (data.description && !$uploadDescription.value) {
                $uploadDescription.value = data.description;
            }
            if (Array.isArray(data.tags) && data.tags.length && !$uploadTags.value) {
                $uploadTags.value = data.tags.join(", ");
            }
            $uploadStatus.textContent = "✨ Suggestions filled — edit as you like, then Upload.";
            $uploadStatus.className = "upload-status success";
        } catch (err) {
            $uploadStatus.textContent = `AI fill failed: ${err.message}`;
            $uploadStatus.className = "upload-status error";
        } finally {
            $uploadName.placeholder = prevPlaceholderName;
            $uploadDescription.placeholder = prevPlaceholderDesc;
            $uploadTags.placeholder = prevPlaceholderTags;
            $uploadName.disabled = false;
            $uploadDescription.disabled = false;
            $uploadTags.disabled = false;
        }
    }

    function closeUploadModal() {
        $uploadModal.classList.add("hidden");
    }

    async function uploadToMixcloud() {
        const name = $uploadName.value.trim();
        if (!name) {
            $uploadStatus.textContent = "Please enter a mixtape name.";
            $uploadStatus.className = "upload-status error";
            return;
        }

        const tagsRaw = $uploadTags.value.trim();
        const tags = tagsRaw ? tagsRaw.split(",").map((t) => t.trim()).filter(Boolean) : [];

        $btnUpload.disabled = true;
        $btnUpload.textContent = "⏳ Uploading…";
        $uploadStatus.textContent = "Starting upload...";
        $uploadStatus.className = "upload-status building";

        try {
            const data = await api("/api/mixcloud/upload", {
                method: "POST",
                body: JSON.stringify({
                    name,
                    description: $uploadDescription.value.trim(),
                    tags,
                    cover_preset: $coverPreset.value || "neon",
                    text_size: ($coverTextSize && $coverTextSize.value) || "medium",
                }),
            });
            pollUploadStatus(data.job_id);
        } catch (err) {
            $uploadStatus.textContent = `Error: ${err.message}`;
            $uploadStatus.className = "upload-status error";
            $btnUpload.disabled = false;
            $btnUpload.textContent = "☁ Upload to Mixcloud";
        }
    }

    function pollUploadStatus(jobId) {
        const timer = setInterval(async () => {
            try {
                const data = await api(`/api/mixcloud/upload/status/${jobId}`);
                if (data.status === "uploading") {
                    $uploadStatus.textContent = data.progress || "Uploading…";
                } else if (data.status === "done") {
                    clearInterval(timer);
                    if (data.mixcloud_url) {
                        $uploadStatus.innerHTML = `Upload complete! <a href="${data.mixcloud_url}" target="_blank" class="upload-link">View on Mixcloud</a>`;
                    } else {
                        $uploadStatus.textContent = "Upload complete!";
                    }
                    $uploadStatus.className = "upload-status success upload-done";
                    $btnUpload.disabled = true;
                    $btnUpload.textContent = "✓ Uploaded";
                    if ($btnShowUpload) {
                        $btnShowUpload.disabled = true;
                        $btnShowUpload.title = "Already uploaded this session";
                    }
                } else if (data.status === "error") {
                    clearInterval(timer);
                    $uploadStatus.textContent = `Error: ${data.error}`;
                    $uploadStatus.className = "upload-status error";
                    $btnUpload.disabled = false;
                    $btnUpload.textContent = "☁ Upload to Mixcloud";
                }
            } catch (err) {
                clearInterval(timer);
                $uploadStatus.textContent = `Polling error: ${err.message}`;
                $uploadStatus.className = "upload-status error";
                $btnUpload.disabled = false;
                $btnUpload.textContent = "☁ Upload to Mixcloud";
            }
        }, 2000);
    }

    // ----------------------------------------------------------------
    // Utility
    // ----------------------------------------------------------------
    // ----------------------------------------------------------------
    // Styled confirm modal (replaces native window.confirm)
    // ----------------------------------------------------------------
    const $confirmModal = document.getElementById("confirm-modal");
    const $confirmTitle = document.getElementById("confirm-title");
    const $confirmMessage = document.getElementById("confirm-message");
    const $confirmOk = document.getElementById("confirm-ok");
    const $confirmCancel = document.getElementById("confirm-cancel");
    const $confirmClose = document.getElementById("confirm-close");

    function confirmModal(opts = {}) {
        const {
            title = "Confirm",
            message = "Are you sure?",
            confirmText = "Confirm",
            cancelText = "Cancel",
            danger = false,
        } = opts;

        return new Promise((resolve) => {
            if (!$confirmModal) {
                // Fallback if the modal markup is missing.
                resolve(window.confirm(message));
                return;
            }
            $confirmTitle.textContent = title;
            $confirmMessage.textContent = message;
            $confirmOk.textContent = confirmText;
            $confirmCancel.textContent = cancelText;
            $confirmOk.classList.toggle("btn-danger", !!danger);
            $confirmOk.classList.toggle("btn-primary", !danger);
            $confirmModal.classList.remove("hidden");
            $confirmOk.focus();

            function cleanup(result) {
                $confirmModal.classList.add("hidden");
                $confirmOk.removeEventListener("click", onOk);
                $confirmCancel.removeEventListener("click", onCancel);
                $confirmClose.removeEventListener("click", onCancel);
                $confirmModal.removeEventListener("click", onOverlay);
                document.removeEventListener("keydown", onKey);
                resolve(result);
            }
            function onOk() { cleanup(true); }
            function onCancel() { cleanup(false); }
            function onOverlay(e) { if (e.target === $confirmModal) cleanup(false); }
            function onKey(e) {
                if (e.key === "Escape") cleanup(false);
                else if (e.key === "Enter") cleanup(true);
            }

            $confirmOk.addEventListener("click", onOk);
            $confirmCancel.addEventListener("click", onCancel);
            $confirmClose.addEventListener("click", onCancel);
            $confirmModal.addEventListener("click", onOverlay);
            document.addEventListener("keydown", onKey);
        });
    }

    // Build a quality badge (codec + bitrate) with a coarse quality tier.
    // Returns null when no bitrate info is available.
    function qualityBadge(t) {
        const kbps = t.bit_rate_kbps;
        if (!kbps) return null;
        const codec = (t.codec || "").toUpperCase();
        const label = codec ? `${codec} · ${kbps}k` : `${kbps} kbps`;

        let cls;
        if (t.lossless || kbps >= 320) cls = "track-quality--high";
        else if (kbps >= 192) cls = "track-quality--mid";
        else if (kbps > 128) cls = "track-quality--low";
        else cls = "track-quality--poor";

        const parts = [];
        if (codec) parts.push(codec);
        parts.push(`${kbps} kbps`);
        if (t.sample_rate_hz) parts.push(`${(t.sample_rate_hz / 1000).toFixed(1)} kHz`);
        if (t.lossless) parts.push("lossless");
        else if (kbps <= 128) parts.push("poor quality");
        else if (kbps < 192) parts.push("lower quality");
        return { label, cls, title: parts.join(" · ") };
    }

    function escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = str || "";
        return div.innerHTML;
    }

    function escapeAttr(str) {
        return (str || "")
            .replace(/&/g, "&amp;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    // ----------------------------------------------------------------
    // Event listeners
    // ----------------------------------------------------------------
    $btnRefresh.addEventListener("click", loadTracks);
    if ($btnReset) $btnReset.addEventListener("click", resetSession);
    $btnBuild.addEventListener("click", buildMixtape);
    if ($btnShowUpload) $btnShowUpload.addEventListener("click", openUploadModal);
    if ($btnUpload) $btnUpload.addEventListener("click", uploadToMixcloud);
    if ($btnModalClose) $btnModalClose.addEventListener("click", closeUploadModal);
    if ($btnModalCancel) $btnModalCancel.addEventListener("click", closeUploadModal);
    if ($uploadModal) $uploadModal.addEventListener("click", (e) => {
        if (e.target === $uploadModal) closeUploadModal();
    });
    // Cover preview: debounced refresh on title changes, immediate on preset / text-size change.
    if ($uploadName) $uploadName.addEventListener("input", () => schedulePreviewRefresh());
    if ($coverPreset) $coverPreset.addEventListener("change", () => schedulePreviewRefresh(0));
    if ($coverTextSize) $coverTextSize.addEventListener("change", () => schedulePreviewRefresh(0));

    // ----------------------------------------------------------------
    // Initialize
    // ----------------------------------------------------------------
    checkMixcloudConnection();
    loadTracks();
})();
