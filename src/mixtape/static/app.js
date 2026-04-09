/* ===================================================================
   Mixtape Creator — Frontend Application
   =================================================================== */

(function () {
    "use strict";

    // ----------------------------------------------------------------
    // State
    // ----------------------------------------------------------------
    let tracks = [];
    let currentJobId = null;
    let pollTimer = null;

    // ----------------------------------------------------------------
    // DOM references
    // ----------------------------------------------------------------
    const $trackList = document.getElementById("track-list");
    const $loading = document.getElementById("loading");
    const $totalDuration = document.getElementById("total-duration");
    const $trackCount = document.getElementById("track-count");
    const $transitionMode = document.getElementById("transition-mode");
    const $crossfadeGroup = document.getElementById("crossfade-group");
    const $crossfadeDuration = document.getElementById("crossfade-duration");
    const $crossfadeValue = document.getElementById("crossfade-value");
    const $fxProbGroup = document.getElementById("fx-prob-group");
    const $fxSeedGroup = document.getElementById("fx-seed-group");
    const $fxProb = document.getElementById("fx-prob");
    const $fxProbValue = document.getElementById("fx-prob-value");
    const $fxSeed = document.getElementById("fx-seed");
    const $parseStyle = document.getElementById("parse-style");
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
            const params = new URLSearchParams({
                crossfade: $crossfadeDuration.value,
                parse_style: $parseStyle.value,
            });
            const resp = await fetch(`/api/tracks?${params}`);
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
            .map(
                (t, i) => `
            <div class="track-card" data-file="${escapeAttr(t.file)}">
                <div class="track-pos">${i + 1}</div>
                <div class="track-info">
                    <div class="track-display">${escapeHtml(t.display)}</div>
                    <div class="track-filename">${escapeHtml(t.file)}</div>
                </div>
                <div class="track-duration">${t.duration_display || "??"}</div>
            </div>`
            )
            .join("");

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
            ghostClass: "sortable-ghost",
            chosenClass: "sortable-chosen",
            dragClass: "sortable-drag",
            onEnd: function (evt) {
                // Reorder our local tracks array
                const moved = tracks.splice(evt.oldIndex, 1)[0];
                tracks.splice(evt.newIndex, 0, moved);
                // Update position numbers
                updatePositionNumbers();
                // Send new order to backend
                reorderTracks();
            },
        });
    }

    function updatePositionNumbers() {
        const cards = $trackList.querySelectorAll(".track-card");
        cards.forEach((card, i) => {
            card.querySelector(".track-pos").textContent = i + 1;
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
                body: JSON.stringify({
                    order,
                    crossfade: parseFloat($crossfadeDuration.value),
                    parse_style: $parseStyle.value,
                }),
            });
            tracks = data.tracks;
            updateSummary(data.total_duration_display, tracks.length);
            // Update start times in the UI without re-rendering
            updateStartTimes();
        } catch (err) {
            console.error("Reorder failed:", err);
        }
    }

    function updateStartTimes() {
        const cards = $trackList.querySelectorAll(".track-card");
        tracks.forEach((t, i) => {
            if (cards[i]) {
                // We could add start time display here in the future
            }
        });
    }

    // ----------------------------------------------------------------
    // Update summary display
    // ----------------------------------------------------------------
    function updateSummary(durationDisplay, count) {
        $totalDuration.textContent = durationDisplay;
        $trackCount.textContent = `${count} track${count !== 1 ? "s" : ""}`;
    }

    // ----------------------------------------------------------------
    // Transition mode UI logic
    // ----------------------------------------------------------------
    function updateTransitionUI() {
        const mode = $transitionMode.value;

        // Show/hide crossfade slider
        if (mode === "none") {
            $crossfadeGroup.classList.add("hidden");
        } else {
            $crossfadeGroup.classList.remove("hidden");
        }

        // Show/hide FX controls
        if (mode === "dj-random") {
            $fxProbGroup.classList.remove("hidden");
            $fxSeedGroup.classList.remove("hidden");
        } else {
            $fxProbGroup.classList.add("hidden");
            $fxSeedGroup.classList.add("hidden");
        }
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
        const payload = {
            order,
            crossfade: parseFloat($crossfadeDuration.value),
            fx_mode: $transitionMode.value,
            fx_prob: parseFloat($fxProb.value),
            fx_seed: $fxSeed.value || null,
            parse_style: $parseStyle.value,
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
                    $buildStatus.textContent = `✅ Done! Output: ${data.output_path}`;
                    $buildStatus.className = "build-status success";
                    $btnBuild.disabled = false;
                    $btnBuild.textContent = "⚡ Build Mixtape";
                } else if (data.status === "error") {
                    clearInterval(pollTimer);
                    $buildStatus.textContent = `❌ Error: ${data.error}`;
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

    $transitionMode.addEventListener("change", () => {
        updateTransitionUI();
        // If crossfade changed, recalculate
        if (tracks.length > 0) reorderTracks();
    });

    $crossfadeDuration.addEventListener("input", () => {
        $crossfadeValue.textContent = `${parseFloat($crossfadeDuration.value).toFixed(1)}s`;
    });

    $crossfadeDuration.addEventListener("change", () => {
        if (tracks.length > 0) reorderTracks();
    });

    $fxProb.addEventListener("input", () => {
        $fxProbValue.textContent = parseFloat($fxProb.value).toFixed(2);
    });

    $parseStyle.addEventListener("change", () => {
        loadTracks();
    });

    // ----------------------------------------------------------------
    // Initialize
    // ----------------------------------------------------------------
    updateTransitionUI();
    loadTracks();
})();