// Newsroom Frontend Controller

let currentSearchNewsId = null;

// --- News Fetching ---

async function startNewsFetch() {
    const btn = document.getElementById("btnFetchNews");
    const icon = document.getElementById("fetchIcon");
    const progressCard = document.getElementById("fetchProgressCard");
    const progressMsg = document.getElementById("fetchStatusMessage");
    const progressPct = document.getElementById("fetchStatusPercent");
    const progressBar = document.getElementById("fetchProgressBar");

    if (btn) btn.disabled = true;
    if (icon) icon.classList.add("animate-spin");
    if (progressCard) progressCard.classList.remove("hidden");

    try {
        const resp = await fetch("/api/news/fetch?max_items=35", { method: "POST" });
        const data = await resp.json();

        // Polling loop
        const interval = setInterval(async () => {
            const stResp = await fetch("/api/news/fetch-status");
            const st = await stResp.json();

            if (progressMsg) progressMsg.textContent = st.message || "Обработка...";
            if (progressPct) progressPct.textContent = `${st.progress || 0}%`;
            if (progressBar) progressBar.style.width = `${st.progress || 0}%`;

            if (st.status === "completed" || st.status === "error") {
                clearInterval(interval);
                setTimeout(() => {
                    window.location.reload();
                }, 1000);
            }
        }, 1500);

    } catch (e) {
        alert("Ошибка запуска сбора новостей: " + e);
        if (btn) btn.disabled = false;
        if (icon) icon.classList.remove("animate-spin");
    }
}

// --- Text Character Counter & Save ---

function updateCharCount(newsId) {
    const textarea = document.getElementById(`anchorText_${newsId}`);
    const badge = document.getElementById(`charCount_${newsId}`);
    if (!textarea || !badge) return;

    const len = textarea.value.length;
    badge.textContent = `${len} / 1000 знаков`;
    if (len <= 1000) {
        badge.className = "text-xs font-bold px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800/60";
    } else {
        badge.className = "text-xs font-bold px-2 py-0.5 rounded bg-red-950 text-red-400 border border-red-800/60";
    }
}

async function saveNewsText(newsId) {
    const textarea = document.getElementById(`anchorText_${newsId}`);
    if (!textarea) return;

    try {
        const resp = await fetch(`/api/news/${newsId}/text`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: textarea.value })
        });
        if (resp.ok) {
            showToast("Текст диктора сохранен!");
        }
    } catch (e) {
        alert("Ошибка сохранения: " + e);
    }
}

// --- Voice Regeneration ---

async function regenerateVoice(newsId) {
    const btn = document.getElementById(`btnRegenVoice_${newsId}`);
    const textarea = document.getElementById(`anchorText_${newsId}`);
    const originalText = btn ? btn.innerHTML : "";

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i class="fa-solid fa-spinner animate-spin"></i> <span>Синтез...</span>`;
    }

    try {
        const textVal = textarea ? textarea.value : "";
        const resp = await fetch(`/api/news/${newsId}/regenerate-voice`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: textVal })
        });
        const data = await resp.json();

        if (data.success && data.item) {
            const item = data.item;
            const container = document.getElementById(`voicePlayerContainer_${newsId}`);
            if (container) {
                container.innerHTML = `
                    <audio controls class="h-9 w-60 rounded bg-slate-900" id="audioPlayer_${newsId}">
                        <source src="/cache/audio/${item.voice_filename}?t=${Date.now()}" type="audio/wav">
                        Ваш браузер не поддерживает аудио.
                    </audio>
                    <span class="text-xs text-slate-400 font-medium whitespace-nowrap" id="audioDuration_${newsId}">
                        ⏱️ ${item.voice_duration} сек
                    </span>
                    <span class="text-[11px] px-2 py-0.5 rounded bg-slate-800 text-sky-400 border border-slate-700 font-mono whitespace-nowrap" id="audioEngine_${newsId}">
                        ${item.voice_engine}
                    </span>
                `;
            }

            const indicator = document.getElementById(`voiceIndicator_${newsId}`);
            if (indicator) {
                indicator.className = "w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse";
            }

            if (btn) {
                btn.className = "px-3 py-1.5 text-xs font-bold bg-slate-800 hover:bg-slate-700 text-sky-300 rounded-lg border border-slate-700 transition-all flex items-center space-x-1.5";
                btn.innerHTML = `<i class="fa-solid fa-rotate-right" id="btnRegenIcon_${newsId}"></i><span id="btnRegenText_${newsId}">Перегенерировать голос</span>`;
            }
            showToast("Озвучка диктора успешно создана!");
        } else {
            alert("Ошибка генерации: " + (data.detail || "Неизвестная ошибка"));
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        }
    } catch (e) {
        alert("Ошибка запроса озвучки: " + e);
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    } finally {
        if (btn) {
            btn.disabled = false;
        }
    }
}

// --- Music Settings ---

async function updateMusicOption(newsId, optionValue) {
    try {
        await fetch(`/api/news/${newsId}/music-settings`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ music_option: optionValue })
        });
        showToast("Настройки музыки обновлены!");
    } catch (e) {
        alert("Ошибка настройки музыки: " + e);
    }
}

// --- Timeline Image Management ---

function getTimelineImages(newsId) {
    const timeline = document.getElementById(`timeline_${newsId}`);
    if (!timeline) return [];
    return Array.from(timeline.children).map(c => c.getAttribute("data-img"));
}

async function moveSlide(newsId, imgName, delta) {
    let images = getTimelineImages(newsId);
    const idx = images.indexOf(imgName);
    if (idx === -1) return;

    const newIdx = idx + delta;
    if (newIdx < 0 || newIdx >= images.length) return;

    // Swap
    const temp = images[idx];
    images[idx] = images[newIdx];
    images[newIdx] = temp;

    await sendReorder(newsId, images);
}

async function removeSlide(newsId, imgName) {
    if (!confirm("Удалить этот слайд из ролика?")) return;
    try {
        const resp = await fetch(`/api/news/${newsId}/images/${imgName}`, { method: "DELETE" });
        if (resp.ok) {
            window.location.reload();
        }
    } catch (e) {
        alert("Ошибка удаления слайда: " + e);
    }
}

async function sendReorder(newsId, images) {
    try {
        const resp = await fetch(`/api/news/${newsId}/images/reorder`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ images: images })
        });
        if (resp.ok) {
            window.location.reload();
        }
    } catch (e) {
        alert("Ошибка сортировки: " + e);
    }
}

function openSearchModal(newsId) {
    currentSearchNewsId = newsId;
    const modal = document.getElementById("imageSearchModal");
    if (modal) modal.classList.remove("hidden");
}

function closeSearchModal() {
    const modal = document.getElementById("imageSearchModal");
    if (modal) modal.classList.add("hidden");
    currentSearchNewsId = null;
}

async function executeCustomImageSearch() {
    if (!currentSearchNewsId) return;
    const qInput = document.getElementById("customSearchQuery");
    const query = qInput ? qInput.value.trim() : "";
    if (!query) return;

    const btn = document.getElementById("btnExecuteSearch");
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i class="fa-solid fa-spinner animate-spin"></i> <span>Поиск и UNCROP...</span>`;
    }

    try {
        const resp = await fetch(`/api/news/${currentSearchNewsId}/images/search`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: query })
        });
        if (resp.ok) {
            closeSearchModal();
            window.location.reload();
        } else {
            alert("Не удалось загрузить изображения");
        }
    } catch (e) {
        alert("Ошибка поиска: " + e);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<i class="fa-solid fa-magnifying-glass"></i> <span>Найти</span>`;
        }
    }
}

async function uploadImage(newsId, inputElem) {
    if (!inputElem.files || !inputElem.files[0]) return;
    const file = inputElem.files[0];

    const formData = new FormData();
    formData.append("file", file);

    try {
        const resp = await fetch(`/api/news/${newsId}/images/upload`, {
            method: "POST",
            body: formData
        });
        if (resp.ok) {
            window.location.reload();
        } else {
            alert("Ошибка загрузки изображения");
        }
    } catch (e) {
        alert("Ошибка: " + e);
    }
}

function previewSlide(imgUrl) {
    const modal = document.getElementById("slidePreviewModal");
    const img = document.getElementById("previewModalImage");
    if (modal && img) {
        img.src = imgUrl;
        modal.classList.remove("hidden");
    }
}

function closePreviewModal() {
    const modal = document.getElementById("slidePreviewModal");
    if (modal) modal.classList.add("hidden");
}

// --- Video Preview Modal ---

function previewVideo(videoUrl) {
    const modal = document.getElementById("videoPreviewModal");
    const player = document.getElementById("videoPreviewPlayer");
    const title = document.getElementById("videoPreviewTitle");
    if (modal && player) {
        player.src = videoUrl;
        if (title) title.textContent = "Просмотр видеоролика: " + (videoUrl.split('/').pop() || "");
        modal.classList.remove("hidden");
        player.play().catch(() => {});
    }
}

function closeVideoPreviewModal() {
    const modal = document.getElementById("videoPreviewModal");
    const player = document.getElementById("videoPreviewPlayer");
    if (player) {
        player.pause();
        player.src = "";
    }
    if (modal) modal.classList.add("hidden");
}

// --- City B-Roll Archive Library Modal & Explorer ---

let currentBrollNewsId = null;
let currentBrollPath = "";
let currentBrollParentPath = null;
let currentBrollDirIdx = 0;
let brollSearchDebounceTimer = null;

function switchBrollFolder(idx) {
    currentBrollDirIdx = parseInt(idx) || 0;
    currentBrollPath = "";
    browseBroll("", "");
}

function openBrollModal(newsId) {
    currentBrollNewsId = newsId;
    const modal = document.getElementById("brollModal");
    if (modal) modal.classList.remove("hidden");
    const searchInput = document.getElementById("brollSearchInput");
    if (searchInput) searchInput.value = "";
    const clearBtn = document.getElementById("brollClearSearchBtn");
    if (clearBtn) clearBtn.classList.add("hidden");
    browseBroll(currentBrollPath || "");
}

function closeBrollModal() {
    const modal = document.getElementById("brollModal");
    if (modal) modal.classList.add("hidden");
    currentBrollNewsId = null;
}

function onBrollSearchInput(val) {
    const clearBtn = document.getElementById("brollClearSearchBtn");
    if (clearBtn) {
        if (val.trim()) clearBtn.classList.remove("hidden");
        else clearBtn.classList.add("hidden");
    }
    clearTimeout(brollSearchDebounceTimer);
    if (val.trim()) {
        brollSearchDebounceTimer = setTimeout(() => {
            browseBroll("", val.trim());
        }, 300);
    } else {
        browseBroll(currentBrollPath || "");
    }
}

function clearBrollSearch() {
    const searchInput = document.getElementById("brollSearchInput");
    if (searchInput) searchInput.value = "";
    const clearBtn = document.getElementById("brollClearSearchBtn");
    if (clearBtn) clearBtn.classList.add("hidden");
    browseBroll(currentBrollPath || "");
}

function brollGoUp() {
    if (currentBrollParentPath !== null && currentBrollParentPath !== undefined) {
        browseBroll(currentBrollParentPath);
    }
}

async function browseBroll(subpath = "", search = "") {
    const loading = document.getElementById("brollLoadingIndicator");
    const foldersSec = document.getElementById("brollFoldersSection");
    const videosSec = document.getElementById("brollVideosSection");
    const emptyState = document.getElementById("brollEmptyState");
    const foldersGrid = document.getElementById("brollFoldersGrid");
    const videosGrid = document.getElementById("brollGrid");
    const breadcrumbs = document.getElementById("brollBreadcrumbs");
    const upBtn = document.getElementById("brollUpBtn");
    const folderDisplay = document.getElementById("brollFolderDisplay");
    const dirSelect = document.getElementById("brollDirSelect");

    if (loading) loading.classList.remove("hidden");
    if (foldersSec) foldersSec.classList.add("hidden");
    if (videosSec) videosSec.classList.add("hidden");
    if (emptyState) emptyState.classList.add("hidden");

    try {
        const url = `/api/broll/browse?subpath=${encodeURIComponent(subpath || "")}&search=${encodeURIComponent(search || "")}&dir_idx=${currentBrollDirIdx}`;
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        if (loading) loading.classList.add("hidden");

        if (dirSelect && data.broll_dirs && data.broll_dirs.length > 0) {
            dirSelect.innerHTML = data.broll_dirs.map((d, i) => `
                <option value="${i}" ${i === currentBrollDirIdx ? 'selected' : ''}>${escapeHtml(d)}</option>
            `).join("");
        } else if (folderDisplay && data.broll_dir) {
            folderDisplay.textContent = data.broll_dir;
        }

        if (!data.is_search) {
            currentBrollPath = data.current_path || "";
            currentBrollParentPath = data.parent_path;
        }

        // Render Up button
        if (upBtn) {
            if (!data.is_search && data.parent_path !== null && data.parent_path !== undefined) {
                upBtn.disabled = false;
            } else {
                upBtn.disabled = true;
            }
        }

        // Render Breadcrumbs
        if (breadcrumbs) {
            if (data.breadcrumbs && data.breadcrumbs.length > 0) {
                breadcrumbs.innerHTML = data.breadcrumbs.map((bc, idx) => {
                    const isLast = idx === data.breadcrumbs.length - 1;
                    if (isLast) {
                        return `<span class="text-white font-bold">${escapeHtml(bc.name)}</span>`;
                    } else {
                        return `
                            <button onclick="browseBroll('${escapeJsStr(bc.path)}')" class="text-sky-400 hover:text-sky-300 hover:underline transition-colors">
                                ${escapeHtml(bc.name)}
                            </button>
                            <span class="text-slate-600">/</span>
                        `;
                    }
                }).join("");
            } else {
                breadcrumbs.innerHTML = `<span class="text-white font-bold">КОНСЕРВЫ</span>`;
            }
        }

        // Render Folders
        const folders = data.folders || [];
        if (foldersSec && foldersGrid) {
            if (folders.length > 0) {
                const countEl = document.getElementById("brollFoldersCount");
                if (countEl) countEl.textContent = `${folders.length}`;
                foldersGrid.innerHTML = folders.map(f => `
                    <div onclick="browseBroll('${escapeJsStr(f.rel_path)}')" 
                         class="cursor-pointer bg-slate-950/80 hover:bg-sky-950/40 border border-slate-800 hover:border-sky-500/50 p-2.5 rounded-xl flex items-center space-x-2.5 transition-all group shadow-sm">
                        <div class="w-8 h-8 rounded-lg bg-amber-500/10 border border-amber-500/20 flex items-center justify-center text-amber-400 group-hover:scale-110 transition-transform shrink-0">
                            <i class="fa-solid fa-folder text-sm"></i>
                        </div>
                        <div class="min-w-0 flex-1">
                            <p class="text-xs font-bold text-white truncate group-hover:text-sky-300" title="${escapeHtml(f.name)}">
                                ${escapeHtml(f.name)}
                            </p>
                            <p class="text-[10px] text-slate-400">
                                ${f.video_count} видео
                            </p>
                        </div>
                        <i class="fa-solid fa-chevron-right text-slate-600 text-xs group-hover:text-sky-400 transition-colors"></i>
                    </div>
                `).join("");
                foldersSec.classList.remove("hidden");
            } else {
                foldersSec.classList.add("hidden");
            }
        }

        // Render Videos
        const videos = data.videos || [];
        if (videosSec && videosGrid) {
            if (videos.length > 0) {
                const countEl = document.getElementById("brollVideosCount");
                if (countEl) countEl.textContent = `${videos.length}`;
                videosGrid.innerHTML = videos.map(v => `
                    <div class="bg-slate-950 border border-slate-800 rounded-xl overflow-hidden shadow-md flex flex-col group hover:border-sky-500/50 transition-all">
                        <div class="aspect-video bg-black relative overflow-hidden flex items-center justify-center">
                            <img src="${v.thumb_url}" loading="lazy" class="w-full h-full object-cover" 
                                 onerror="this.style.display='none'; if(this.nextElementSibling) this.nextElementSibling.style.display='flex';">
                            <div class="hidden w-full h-full items-center justify-center text-slate-600 text-2xl bg-slate-900">
                                <i class="fa-solid fa-video"></i>
                            </div>
                            <div class="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center gap-2">
                                <button onclick="previewVideo('${v.stream_url}')" class="px-3 py-1.5 bg-sky-600 hover:bg-sky-500 text-white rounded text-xs font-bold shadow flex items-center gap-1.5">
                                    <i class="fa-solid fa-play text-xs"></i> Просмотр
                                </button>
                            </div>
                            <span class="absolute bottom-1 right-1 bg-black/80 text-[10px] text-slate-300 px-1.5 py-0.5 rounded font-mono">
                                ${v.size_mb} МБ
                            </span>
                            ${v.folder && data.is_search ? `
                                <span class="absolute top-1 left-1 bg-sky-950/90 border border-sky-800/80 text-[10px] text-sky-300 px-1.5 py-0.5 rounded font-medium max-w-[80%] truncate" title="${escapeHtml(v.folder)}">
                                    <i class="fa-solid fa-folder text-[9px] mr-1"></i>${escapeHtml(v.folder)}
                                </span>
                            ` : ''}
                        </div>
                        <div class="p-3 flex-1 flex flex-col justify-between space-y-2">
                            <p class="text-xs font-bold text-white truncate" title="${escapeHtml(v.name)}">${escapeHtml(v.name)}</p>
                            <button onclick="insertBrollToNews('${escapeJsStr(v.rel_path)}', ${v.dir_idx !== undefined ? v.dir_idx : 'currentBrollDirIdx'})" class="w-full py-1.5 bg-emerald-600/90 hover:bg-emerald-500 text-white rounded-lg text-xs font-bold transition-all flex items-center justify-center gap-1 shadow">
                                <i class="fa-solid fa-plus"></i> Вставить в сюжет
                            </button>
                        </div>
                    </div>
                `).join("");
                videosSec.classList.remove("hidden");
            } else {
                videosSec.classList.add("hidden");
            }
        }

        // Handle Empty state
        if (folders.length === 0 && videos.length === 0) {
            if (emptyState) {
                const title = document.getElementById("brollEmptyTitle");
                const desc = document.getElementById("brollEmptyDesc");
                if (data.is_search) {
                    if (title) title.textContent = `По запросу «${data.search_query}» ничего не найдено`;
                    if (desc) desc.textContent = "Попробуйте изменить поисковые слова или очистить строку поиска.";
                } else {
                    if (title) title.textContent = "В этой папке нет видеороликов";
                    if (desc) desc.textContent = "Перейдите в другие подпапки архива или загрузите новое видео кнопкой вверху.";
                }
                emptyState.classList.remove("hidden");
            }
        }

    } catch (e) {
        if (loading) loading.classList.add("hidden");
        if (emptyState) {
            const title = document.getElementById("brollEmptyTitle");
            const desc = document.getElementById("brollEmptyDesc");
            if (title) title.textContent = "Ошибка загрузки архива";
            if (desc) desc.textContent = String(e);
            emptyState.classList.remove("hidden");
        }
    }
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function escapeJsStr(str) {
    if (!str) return "";
    return String(str).replace(/\\/g, "\\\\").replace(/'/g, "\\'").replace(/"/g, '\\"');
}

async function insertBrollToNews(rel_path, dir_idx) {
    if (!currentBrollNewsId) return;
    try {
        const resp = await fetch(`/api/news/${currentBrollNewsId}/media/add-broll`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rel_path, dir_idx: (dir_idx !== undefined ? dir_idx : currentBrollDirIdx) })
        });
        if (resp.ok) {
            closeBrollModal();
            window.location.reload();
        } else {
            const err = await resp.json().catch(() => ({}));
            alert("Не удалось добавить видео в сюжет: " + (err.detail || resp.statusText));
        }
    } catch (e) {
        alert("Ошибка: " + e);
    }
}

async function uploadToBrollLibrary(input) {
    if (!input.files || !input.files[0]) return;
    const file = input.files[0];
    const formData = new FormData();
    formData.append("file", file);
    formData.append("subpath", currentBrollPath || "");
    formData.append("dir_idx", currentBrollDirIdx);

    const label = input.closest("label");
    const origHtml = label ? label.innerHTML : "";
    if (label) label.innerHTML = `<i class="fa-solid fa-spinner animate-spin"></i> Загрузка...`;

    try {
        const resp = await fetch("/api/broll/upload", {
            method: "POST",
            body: formData
        });
        if (resp.ok) {
            showToast("Видео успешно добавлено в архив!");
            browseBroll(currentBrollPath || "");
        } else {
            alert("Ошибка загрузки видео в архив");
        }
    } catch (e) {
        alert("Ошибка: " + e);
    } finally {
        if (label) label.innerHTML = origHtml;
        input.value = "";
    }
}

async function deleteNewsItem(newsId) {
    if (!confirm("Удалить эту новость из эфирной ленты?")) return;
    try {
        const resp = await fetch(`/api/news/${newsId}`, { method: "DELETE" });
        if (resp.ok) {
            const card = document.getElementById(`newsCard_${newsId}`);
            if (card) card.remove();
        }
    } catch (e) {
        alert("Ошибка удаления: " + e);
    }
}

// --- Video Rendering ---

async function renderVideo(newsId) {
    const btn = document.getElementById(`btnRenderVideo_${newsId}`);
    const section = document.getElementById(`videoRenderSection_${newsId}`);
    const progressBox = document.getElementById(`renderProgressBox_${newsId}`);
    const playerBox = document.getElementById(`videoPlayerBox_${newsId}`);
    const renderMsg = document.getElementById(`renderMessage_${newsId}`);
    const renderPct = document.getElementById(`renderPercent_${newsId}`);
    const renderBar = document.getElementById(`renderBar_${newsId}`);

    if (btn) {
        btn.disabled = true;
        btn.classList.add("opacity-50");
    }
    if (section) section.classList.remove("hidden");
    if (progressBox) progressBox.classList.remove("hidden");
    if (playerBox) playerBox.classList.add("hidden");

    try {
        const resp = await fetch(`/api/news/${newsId}/render-video`, { method: "POST" });
        if (!resp.ok) {
            const err = await resp.json();
            alert("Ошибка: " + (err.detail || "Не удалось запустить рендеринг"));
            if (btn) btn.disabled = false;
            return;
        }

        // Poll render status
        const interval = setInterval(async () => {
            const stResp = await fetch(`/api/news/${newsId}/render-status`);
            const st = await stResp.json();

            if (renderMsg) renderMsg.textContent = st.message || "Рендеринг...";
            if (renderPct) renderPct.textContent = `${st.progress || 0}%`;
            if (renderBar) renderBar.style.width = `${st.progress || 0}%`;

            if (st.status === "completed") {
                clearInterval(interval);
                if (progressBox) progressBox.classList.add("hidden");
                if (playerBox) playerBox.classList.remove("hidden");

                const videoEl = document.getElementById(`videoElement_${newsId}`);
                if (videoEl) {
                    videoEl.src = `${st.video_url}?t=${Date.now()}`;
                    videoEl.load();
                }
                const dlBtn = document.getElementById(`btnDownloadVideo_${newsId}`);
                if (dlBtn) {
                    dlBtn.href = st.download_url || `/api/news/${newsId}/download`;
                    if (st.video_filename) {
                        dlBtn.setAttribute("download", st.video_filename);
                    }
                }

                if (btn) {
                    btn.disabled = false;
                    btn.classList.remove("opacity-50");
                }
                showToast("Видеоролик успешно смонтирован!");
            } else if (st.status === "error") {
                clearInterval(interval);
                alert("Ошибка во время рендеринга видео: " + (st.message || "Неизвестная ошибка"));
                if (btn) {
                    btn.disabled = false;
                    btn.classList.remove("opacity-50");
                }
            }
        }, 1500);

    } catch (e) {
        alert("Ошибка рендеринга: " + e);
        if (btn) {
            btn.disabled = false;
            btn.classList.remove("opacity-50");
        }
    }
}

// --- Admin Settings & Multi-Provider AI ---

function selectAiProvider(provider) {
    const hidden = document.getElementById("cfgAiProvider");
    if (hidden) hidden.value = provider;

    const radios = document.querySelectorAll("input[name='ai_provider_radio']");
    radios.forEach(r => {
        const parentLabel = r.closest("label");
        if (!parentLabel) return;
        if (r.value === provider) {
            r.checked = true;
            if (provider === "gemini") {
                parentLabel.className = "cursor-pointer border rounded-xl p-4 flex items-center space-x-3.5 transition-all border-sky-500 bg-sky-950/40 text-sky-200 shadow-md shadow-sky-950/40";
            } else {
                parentLabel.className = "cursor-pointer border rounded-xl p-4 flex items-center space-x-3.5 transition-all border-amber-500 bg-amber-950/40 text-amber-200 shadow-md shadow-amber-950/40";
            }
        } else {
            r.checked = false;
            parentLabel.className = "cursor-pointer border rounded-xl p-4 flex items-center space-x-3.5 transition-all border-slate-800 bg-slate-900/60 text-slate-400 hover:border-slate-700";
        }
    });
}

function toggleVisibility(inputId, btnEl) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const icon = btnEl ? btnEl.querySelector("i") : null;
    if (input.type === "password") {
        input.type = "text";
        if (icon) icon.className = "fa-solid fa-eye-slash";
    } else {
        input.type = "password";
        if (icon) icon.className = "fa-solid fa-eye";
    }
}

function toggleKeyVisibility() {
    toggleVisibility("cfgGeminiKey", document.querySelector("button[onclick='toggleKeyVisibility()']"));
}

async function testProvider(provider = "gemini") {
    const key = (document.getElementById("cfgGeminiKey")?.value || "").trim();
    const model = (document.getElementById("cfgTextModel")?.value || "").trim();
    const proxy = (document.getElementById("cfgProxyUrl")?.value || "").trim();
    const feedback = document.getElementById("feedback_gemini");
    const btn = document.getElementById("btnTest_gemini");

    if (!key) {
        if (feedback) {
            feedback.classList.remove("hidden");
            feedback.className = "text-xs font-medium p-2.5 rounded bg-red-950 text-red-300 block border border-red-800";
            feedback.innerHTML = `<i class="fa-solid fa-circle-exclamation text-red-400 mr-1.5"></i> Укажите API ключ Google AI Studio`;
        }
        return;
    }

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<i class="fa-solid fa-spinner animate-spin"></i> <span>Проверка...</span>`;
    }
    if (feedback) {
        feedback.classList.remove("hidden");
        feedback.className = "text-xs font-medium p-2.5 rounded bg-sky-950 text-sky-300 border border-sky-800 block";
        feedback.innerHTML = `<i class="fa-solid fa-circle-notch animate-spin mr-1.5"></i> Отправка проверочного запроса к Gemini API...`;
    }

    try {
        const resp = await fetch("/api/config/test-ai", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                provider: "gemini",
                api_key: key,
                model: model,
                proxy_url: proxy
            })
        });
        const res = await resp.json();
        if (feedback) {
            feedback.classList.remove("hidden");
            if (res.success) {
                feedback.className = "text-xs font-medium p-2.5 rounded bg-emerald-950 text-emerald-300 block border border-emerald-800 shadow";
                feedback.innerHTML = `<i class="fa-solid fa-circle-check text-emerald-400 mr-1.5"></i> ${res.message}`;
            } else {
                feedback.className = "text-xs font-medium p-2.5 rounded bg-red-950 text-red-300 block border border-red-800 shadow";
                feedback.innerHTML = `<i class="fa-solid fa-circle-exclamation text-red-400 mr-1.5"></i> ${res.message}`;
            }
        }
    } catch (e) {
        if (feedback) {
            feedback.classList.remove("hidden");
            feedback.className = "text-xs font-medium p-2.5 rounded bg-red-950 text-red-300 block border border-red-800 shadow";
            feedback.innerHTML = `<i class="fa-solid fa-triangle-exclamation mr-1.5"></i> Ошибка сетевого запроса: ${e}`;
        }
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<i class="fa-solid fa-bolt"></i> <span>Проверить</span>`;
        }
    }
}

async function testGeminiConnection() {
    return testProvider("gemini");
}

function updatePromptCharCount() {
    const el = document.getElementById("cfgSearchPrompt");
    const countEl = document.getElementById("promptCharCount");
    if (el && countEl) {
        countEl.textContent = `Символов: ${el.value.length}`;
    }
}

async function resetSearchPromptToDefault() {
    if (!confirm("Сбросить поисковый промпт к исходному тексту инструкции?")) return;
    try {
        const resp = await fetch("/api/config/default-prompt");
        if (resp.ok) {
            const data = await resp.json();
            const el = document.getElementById("cfgSearchPrompt");
            if (el) {
                el.value = data.prompt;
                updatePromptCharCount();
                showToast("Промпт сброшен к исходному! Нажмите «Сохранить настройки».");
            }
        }
    } catch (e) {
        alert("Ошибка сброса: " + e);
    }
}

function getElVal(id, defaultVal = "") {
    const el = document.getElementById(id);
    return el ? el.value.trim() : defaultVal;
}

async function saveAllSettings() {
    const btnTop = document.querySelector("button[onclick='saveAllSettings()']");
    const btnBottom = document.getElementById("btnBottomSave");
    
    const setButtonsState = (html, disabled) => {
        if (btnTop) { btnTop.innerHTML = html; btnTop.disabled = disabled; }
        if (btnBottom) { btnBottom.innerHTML = html; btnBottom.disabled = disabled; }
    };

    setButtonsState(`<i class="fa-solid fa-spinner animate-spin"></i> <span>Сохранение...</span>`, true);

    try {
        const currentCfg = await (await fetch("/api/config")).json();

        // Active AI Provider: strictly Gemini
        currentCfg.ai_provider = "gemini";

        // Google Gemini (Free Tier + Grounding)
        currentCfg.gemini_api_key = getElVal("cfgGeminiKey", currentCfg.gemini_api_key);
        currentCfg.gemini_text_model = getElVal("cfgTextModel", currentCfg.gemini_text_model);
        const groundingChk = document.getElementById("cfgGeminiGrounding");
        if (groundingChk) {
            currentCfg.gemini_use_search_grounding = groundingChk.checked;
        }

        // Search & Editorial Prompt
        const promptEl = document.getElementById("cfgSearchPrompt");
        if (promptEl) {
            currentCfg.search_prompt = promptEl.value;
        }

        // TTS Settings (Gemini TTS: speaker and model)
        currentCfg.gemini_tts_model = getElVal("cfgTtsModel", currentCfg.gemini_tts_model || "gemini-3.8-flash-tts");
        currentCfg.tts_speaker = getElVal("cfgTtsSpeaker", currentCfg.tts_speaker || "Callirrhoe");

        // Network & Media
        currentCfg.proxy_url = getElVal("cfgProxyUrl", currentCfg.proxy_url);
        currentCfg.vk_service_token = getElVal("cfgVkToken", currentCfg.vk_service_token);
        currentCfg.bumper_path = getElVal("cfgBumperPath", currentCfg.bumper_path);
        
        const dbVal = parseFloat(getElVal("cfgMusicDb", "-5.4"));
        currentCfg.music_volume_db = isNaN(dbVal) ? -5.4 : dbVal;
        
        currentCfg.logo_path = getElVal("cfgLogoPath", currentCfg.logo_path);
        currentCfg.logo_position = getElVal("cfgLogoPos", currentCfg.logo_position);

        // B-Roll folders (Multiple paths)
        const brollInputs = document.querySelectorAll(".cfg-broll-dir-input");
        const brollDirs = [];
        brollInputs.forEach(inp => {
            const val = inp.value.trim();
            if (val && !brollDirs.includes(val)) {
                brollDirs.push(val);
            }
        });
        if (brollDirs.length === 0) {
            const singleBroll = getElVal("cfgBrollDir", "F:\\КОНСЕРВЫ");
            brollDirs.push(singleBroll || "F:\\КОНСЕРВЫ");
        }
        currentCfg.broll_dirs = brollDirs;
        currentCfg.city_videos_dir = brollDirs[0];

        // Background Music tracks (Multiple paths)
        const trackRows = document.querySelectorAll("#musicTracksContainer .music-track-row");
        const musicTracks = [];
        let defaultMusicPath = "";
        trackRows.forEach((row, idx) => {
            const trackId = row.getAttribute("data-track-id") || `track_${idx}`;
            const nameInp = row.querySelector(".cfg-music-name-input");
            const pathInp = row.querySelector(".cfg-music-path-input");
            const defRadio = row.querySelector(".cfg-music-default-radio");
            const name = nameInp ? nameInp.value.trim() : "";
            const path = pathInp ? pathInp.value.trim() : "";
            if (path) {
                const isDef = defRadio ? defRadio.checked : (idx === 0);
                if (isDef && !defaultMusicPath) {
                    defaultMusicPath = path;
                }
                musicTracks.push({
                    id: trackId,
                    name: name || `Трек ${idx + 1}`,
                    path: path,
                    enabled: true
                });
            }
        });
        if (musicTracks.length > 0) {
            currentCfg.music_tracks = musicTracks;
            currentCfg.default_music_path = defaultMusicPath || musicTracks[0].path;
        } else {
            currentCfg.default_music_path = getElVal("cfgMusicPath", currentCfg.default_music_path || "F:\\новостник\\музыка.wav");
        }

        // Sources toggles
        const rows = document.querySelectorAll("#sourcesTableBody tr");
        rows.forEach(tr => {
            const id = tr.getAttribute("data-source-id");
            const chk = tr.querySelector(".source-toggle");
            if (id && chk && currentCfg.sources) {
                const srcObj = currentCfg.sources.find(s => s.id === id);
                if (srcObj) srcObj.enabled = chk.checked;
            }
        });

        const resp = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(currentCfg)
        });

        if (resp.ok) {
            setButtonsState(`<i class="fa-solid fa-check text-emerald-400"></i> <span>Сохранено!</span>`, false);
            showToast("Все параметры нейросетей, ключи и конфигурация успешно сохранены!");
            setTimeout(() => {
                setButtonsState(`<i class="fa-solid fa-floppy-disk"></i> <span>Сохранить настройки</span>`, false);
            }, 2500);
        } else {
            const err = await resp.json();
            alert("Ошибка сохранения: " + (err.detail || "Сервер отклонил запрос"));
            setButtonsState(`<i class="fa-solid fa-floppy-disk"></i> <span>Сохранить настройки</span>`, false);
        }
    } catch (e) {
        alert("Ошибка при сохранении: " + e);
        setButtonsState(`<i class="fa-solid fa-floppy-disk"></i> <span>Сохранить настройки</span>`, false);
    }
}

async function testSourceUrl(url) {
    try {
        const resp = await fetch("/api/config/test-source", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: url })
        });
        const res = await resp.json();
        alert(res.message);
    } catch (e) {
        alert("Ошибка проверки: " + e);
    }
}

async function addNewSource() {
    const name = document.getElementById("newSourceName").value.trim();
    const url = document.getElementById("newSourceUrl").value.trim();
    if (!name || !url) {
        alert("Укажите название и URL ленты");
        return;
    }

    const currentCfg = await (await fetch("/api/config")).json();
    const newId = "src_" + Date.now();
    currentCfg.sources.push({
        id: newId,
        name: name,
        url: url,
        enabled: true,
        category: "Пользовательские"
    });

    await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentCfg)
    });
    window.location.reload();
}

async function removeSource(sourceId) {
    if (!confirm("Удалить этот источник?")) return;
    const currentCfg = await (await fetch("/api/config")).json();
    currentCfg.sources = currentCfg.sources.filter(s => s.id !== sourceId);
    await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(currentCfg)
    });
    window.location.reload();
}

async function uploadLogo(inputElem) {
    if (!inputElem.files || !inputElem.files[0]) return;
    const formData = new FormData();
    formData.append("file", inputElem.files[0]);

    try {
        const resp = await fetch("/api/upload/logo", { method: "POST", body: formData });
        if (resp.ok) {
            showToast("Логотип телеканала обновлен!");
            setTimeout(() => window.location.reload(), 1000);
        }
    } catch (e) {
        alert("Ошибка загрузки логотипа: " + e);
    }
}

// --- Archive ---

async function deleteArchiveVideo(filename, elementId) {
    if (!confirm(`Удалить видео ${filename}?`)) return;
    try {
        const resp = await fetch(`/api/archive/${encodeURIComponent(filename)}`, { method: "DELETE" });
        if (resp.ok) {
            const card = document.getElementById(elementId) || document.getElementById(`archiveCard_${filename}`);
            if (card) card.remove();
            showToast("Видео удалено из архива");
        } else {
            alert("Не удалось удалить видео");
        }
    } catch (e) {
        alert("Ошибка удаления: " + e);
    }
}

// --- Toast notification ---

function showToast(msg) {
    const toast = document.createElement("div");
    toast.className = "fixed bottom-5 right-5 bg-sky-600 text-white font-bold text-xs px-4 py-2.5 rounded-lg shadow-2xl z-50 transition-opacity duration-300 flex items-center space-x-2";
    toast.innerHTML = `<i class="fa-solid fa-check"></i> <span>${msg}</span>`;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = "0";
        setTimeout(() => toast.remove(), 300);
    }, 2500);
}

// --- Initial Load Check ---

async function checkFetchStatusOnLoad() {
    const progressCard = document.getElementById("fetchProgressCard");
    const progressMsg = document.getElementById("fetchStatusMessage");
    const progressPct = document.getElementById("fetchStatusPercent");
    const progressBar = document.getElementById("fetchProgressBar");
    const btn = document.getElementById("btnFetchNews");
    const icon = document.getElementById("fetchIcon");

    try {
        const resp = await fetch("/api/news/fetch-status");
        const st = await resp.json();
        if (st.status === "running") {
            if (btn) btn.disabled = true;
            if (icon) icon.classList.add("animate-spin");
            if (progressCard) progressCard.classList.remove("hidden");
            if (progressMsg) progressMsg.textContent = st.message || "Обработка...";
            if (progressPct) progressPct.textContent = `${st.progress || 0}%`;
            if (progressBar) progressBar.style.width = `${st.progress || 0}%`;

            const interval = setInterval(async () => {
                const stResp = await fetch("/api/news/fetch-status");
                const cur = await stResp.json();
                if (progressMsg) progressMsg.textContent = cur.message || "Обработка...";
                if (progressPct) progressPct.textContent = `${cur.progress || 0}%`;
                if (progressBar) progressBar.style.width = `${cur.progress || 0}%`;

                if (cur.status === "completed" || cur.status === "error") {
                    clearInterval(interval);
                    setTimeout(() => window.location.reload(), 1000);
                }
            }, 1500);
        }
    } catch (e) {
        // ignore
    }
}

document.addEventListener("DOMContentLoaded", () => {
    checkFetchStatusOnLoad();
    initAdminMediaValidation();
});

// --- Admin Media Managers: Music & B-Roll ---

function addMusicTrackRow(name = "", path = "", isDefault = false) {
    const container = document.getElementById("musicTracksContainer");
    if (!container) return;
    const trackId = `track_${Date.now()}`;
    const row = document.createElement("div");
    row.className = "music-track-row bg-slate-900/90 border border-slate-800 rounded-lg p-3 flex flex-col md:flex-row md:items-center justify-between gap-3";
    row.setAttribute("data-track-id", trackId);
    row.innerHTML = `
        <div class="flex items-center space-x-3 flex-1 min-w-0">
            <label class="flex items-center space-x-1.5 text-xs text-slate-400 cursor-pointer shrink-0" title="Использовать как трек по умолчанию">
                <input type="radio" name="default_music_radio" class="cfg-music-default-radio text-purple-500 focus:ring-0" ${isDefault ? 'checked' : ''}>
                <span class="text-[11px] font-semibold text-slate-300">Основной</span>
            </label>
            <div class="w-full sm:w-44 shrink-0">
                <input type="text" class="cfg-music-name-input w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-1.5 text-xs text-white font-medium focus:border-purple-500 focus:outline-none" placeholder="Название трека" value="${escapeHtml(name)}">
            </div>
            <div class="flex-1 min-w-0">
                <input type="text" class="cfg-music-path-input w-full bg-slate-950 border border-slate-800 rounded px-2.5 py-1.5 text-xs text-slate-300 font-mono focus:border-purple-500 focus:outline-none" placeholder="F:\\путь\\к_треку.wav" value="${escapeHtml(path)}" onchange="updateMusicRowPreview(this)">
            </div>
        </div>
        <div class="flex items-center space-x-2 shrink-0 justify-end">
            <audio controls class="h-8 max-w-[180px] bg-slate-950 rounded">
                <source src="/media/preview/music?path=${encodeURIComponent(path)}" type="audio/wav">
            </audio>
            <button type="button" onclick="removeMusicTrackRow(this)" class="p-2 text-slate-500 hover:text-red-400 transition-colors" title="Удалить трек">
                <i class="fa-solid fa-trash-can text-xs"></i>
            </button>
        </div>
    `;
    container.appendChild(row);
}

function removeMusicTrackRow(btn) {
    const row = btn.closest(".music-track-row");
    if (!row) return;
    const container = document.getElementById("musicTracksContainer");
    if (container && container.querySelectorAll(".music-track-row").length <= 1) {
        alert("Должен оставаться хотя бы один музыкальный трек!");
        return;
    }
    row.remove();
}

function updateMusicRowPreview(input) {
    const row = input.closest(".music-track-row");
    if (!row) return;
    const audio = row.querySelector("audio");
    if (audio && input.value.trim()) {
        audio.src = `/media/preview/music?path=${encodeURIComponent(input.value.trim())}`;
        audio.load();
    }
}

async function uploadAdminMusicTrack(input) {
    if (!input.files || !input.files[0]) return;
    const file = input.files[0];
    const formData = new FormData();
    formData.append("file", file);

    const label = input.closest("label");
    const origHtml = label ? label.innerHTML : "";
    if (label) label.innerHTML = `<i class="fa-solid fa-spinner animate-spin"></i> Загрузка...`;

    try {
        const resp = await fetch("/api/upload/music", {
            method: "POST",
            body: formData
        });
        if (resp.ok) {
            const data = await resp.json();
            showToast("Аудиофайл загружен!");
            addMusicTrackRow(data.name || file.name, data.path, false);
        } else {
            alert("Ошибка загрузки музыки");
        }
    } catch (e) {
        alert("Ошибка: " + e);
    } finally {
        if (label) label.innerHTML = origHtml;
        input.value = "";
    }
}

function addBrollDirRow(path = "") {
    const container = document.getElementById("brollDirsContainer");
    if (!container) return;
    const row = document.createElement("div");
    row.className = "broll-dir-row bg-slate-900/90 border border-slate-800 rounded-lg p-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3";
    row.innerHTML = `
        <div class="flex items-center space-x-2.5 flex-1 min-w-0">
            <i class="fa-solid fa-folder-open text-sky-400 text-sm shrink-0"></i>
            <input type="text" class="cfg-broll-dir-input w-full bg-slate-950 border border-slate-800 rounded px-3 py-1.5 text-xs text-slate-200 font-mono focus:border-sky-500 focus:outline-none" value="${escapeHtml(path)}" placeholder="Например: F:\\КОНСЕРВЫ или D:\\Видео\\Архив" onblur="validateBrollDirRow(this)">
        </div>
        <div class="flex items-center space-x-2 shrink-0 justify-end">
            <span class="broll-dir-status text-[11px] px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">Проверка...</span>
            <button type="button" onclick="removeBrollDirRow(this)" class="p-1.5 text-slate-500 hover:text-red-400 transition-colors" title="Удалить путь">
                <i class="fa-solid fa-trash-can text-xs"></i>
            </button>
        </div>
    `;
    container.appendChild(row);
    const input = row.querySelector(".cfg-broll-dir-input");
    if (input && path) validateBrollDirRow(input);
}

function removeBrollDirRow(btn) {
    const row = btn.closest(".broll-dir-row");
    if (!row) return;
    const container = document.getElementById("brollDirsContainer");
    if (container && container.querySelectorAll(".broll-dir-row").length <= 1) {
        alert("Должна быть указана хотя бы одна папка B-Roll!");
        return;
    }
    row.remove();
}

async function validateBrollDirRow(input) {
    const row = input.closest(".broll-dir-row");
    if (!row) return;
    const statusEl = row.querySelector(".broll-dir-status");
    if (!statusEl) return;
    const path = input.value.trim();
    if (!path) {
        statusEl.className = "broll-dir-status text-[11px] px-2 py-0.5 rounded bg-slate-800 text-slate-500 font-mono";
        statusEl.textContent = "Не указан";
        return;
    }
    statusEl.className = "broll-dir-status text-[11px] px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-mono";
    statusEl.textContent = "Проверка...";
    try {
        const resp = await fetch(`/api/system/check-path?path=${encodeURIComponent(path)}`);
        if (resp.ok) {
            const d = await resp.json();
            if (d.exists && d.is_dir) {
                statusEl.className = "broll-dir-status text-[11px] px-2 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800 font-mono";
                statusEl.textContent = d.video_count > 0 ? `Доступно (${d.video_count} видео)` : "Папка пуста";
            } else {
                statusEl.className = "broll-dir-status text-[11px] px-2 py-0.5 rounded bg-red-950 text-red-400 border border-red-800 font-mono";
                statusEl.textContent = "Не найдена";
            }
        }
    } catch (e) {
        statusEl.textContent = "Ошибка сети";
    }
}

function initAdminMediaValidation() {
    const inputs = document.querySelectorAll(".cfg-broll-dir-input");
    if (inputs.length > 0) {
        inputs.forEach(inp => validateBrollDirRow(inp));
    }
}
