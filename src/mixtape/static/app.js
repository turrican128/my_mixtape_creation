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
    let currentJobId = null;
    let pollTimer = null;
    let mixcloudConnected = false;

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
            // Initialize default transition modes for new tracks
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

                return `
            <div class="track-card" data-file="${escapeAttr(t.file)}">
                <div class="track-pos">${i + 1}</div>
                <div class="track-info">
                    <div class="track-display">${escapeHtml(t.display)}</div>
                    <div class="track-filename">${escapeHtml(t.file)}</div>
                </div>
                <div class="track-duration">${t.duration_display || "??"}</div>
            </div>${transitionHtml}`;
            })
            .join("");

        // Attach transition dropdown listeners
        $trackList.querySelectorAll(".transition-select").forEach((sel) => {
            sel.addEventListener("change", (e) => {
                transitionModes[e.target.dataset.file] = e.target.value;
            });
        });

        initSortable();
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
            },
        });
    }

    // ----------------------------------------------------------------
    // Reorder tracks (send to backend for recalculation)
    // ----------------------------------------------------------------
    async function reorderTracks() {
        const order = tracks.map((t) => t.file);
        try {
            const data = await api("/api/tracks/reorder", {
                method: "POST",
                body: JSON.stringify({ order }),
            });
            tracks = data.tracks;
            updateSummary(data.total_duration_display, tracks.length);
        } catch (err) {
            console.error("Reorder failed:", err);
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
    const $uploadTrigger = document.getElementById("upload-trigger");
    const $btnShowUpload = document.getElementById("btn-show-upload");
    const $uploadModal = document.getElementById("upload-modal");
    const $uploadName = document.getElementById("upload-name");
    const $uploadDescription = document.getElementById("upload-description");
    const $uploadTags = document.getElementById("upload-tags");
    const $btnUpload = document.getElementById("btn-upload");
    const $btnModalClose = document.getElementById("btn-modal-close");
    const $btnModalCancel = document.getElementById("btn-modal-cancel");
    const $uploadStatus = document.getElementById("upload-status");

    async function checkMixcloudConnection() {
        try {
            const data = await api("/api/mixcloud/status");
            mixcloudConnected = data.connected;
        } catch {
            mixcloudConnected = false;
        }
    }

    function showUploadSection() {
        if (!mixcloudConnected || !$uploadTrigger) return;
        $uploadTrigger.classList.remove("hidden");
    }

    function openUploadModal() {
        $uploadModal.classList.remove("hidden");
        $uploadStatus.textContent = "";
        $uploadStatus.className = "upload-status";
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
                    $uploadStatus.className = "upload-status success";
                    $btnUpload.disabled = false;
                    $btnUpload.textContent = "☁ Upload to Mixcloud";
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
    $btnBuild.addEventListener("click", buildMixtape);
    if ($btnShowUpload) $btnShowUpload.addEventListener("click", openUploadModal);
    if ($btnUpload) $btnUpload.addEventListener("click", uploadToMixcloud);
    if ($btnModalClose) $btnModalClose.addEventListener("click", closeUploadModal);
    if ($btnModalCancel) $btnModalCancel.addEventListener("click", closeUploadModal);
    if ($uploadModal) $uploadModal.addEventListener("click", (e) => {
        if (e.target === $uploadModal) closeUploadModal();
    });

    // ----------------------------------------------------------------
    // Initialize
    // ----------------------------------------------------------------
    checkMixcloudConnection();
    loadTracks();
})();
