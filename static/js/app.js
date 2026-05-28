/**
 * GaziGPT - Ana JavaScript
 * Chat history is stored by the GCode backend.
 */

// ─── STATE ───────────────────────────────────
const state = {
    currentChatId: null,
    isLoading: false,
    deleteTargetId: null,
    attachedFile: null,   // { name, content }
    abortController: null, // streaming cancellation
    selectedModel: 'GaziGPT', // Default model
    autoAcceptEdits: false,
    autoPilot: false,
    securityLevel: 'safe',
    modelEffort: 'medium',
    currentEditPlanId: null,
    currentEditPreview: null,
    chats: {},
    chatStoreReady: false,
};

// ─── STORAGE HELPERS ─────────────────────────
const LEGACY_STORAGE_KEY = 'gazigpt_chats';
const SETTINGS_KEY = 'gazigpt_settings';
const LEGACY_CURRENT_CHAT_KEY = 'gazigpt_current_chat_id';
const APPROVAL_KEY = 'gcode_approval_mode';
const SECURITY_KEY = 'gcode_security_level';
const AUTO_PILOT_KEY = 'gcode_auto_pilot';
const MODEL_EFFORT_KEY = 'gcode_model_effort';
let chatSyncTimer = null;

function loadChatsFromStorage() {
    return state.chats || {};
}

function saveChatsToStorage(chats) {
    state.chats = chats || {};
    scheduleChatStoreSync();
}

async function initChatStore() {
    try {
        const res = await fetch('/api/chats/sync');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        state.chats = data.chats || {};
        state.currentChatId = data.current_chat_id || null;

        const legacyChats = migrateLegacyChatsIfNeeded();
        if (legacyChats) {
            state.chats = legacyChats.chats;
            state.currentChatId = legacyChats.currentChatId;
            await syncChatStoreNow();
            localStorage.removeItem(LEGACY_STORAGE_KEY);
            localStorage.removeItem(LEGACY_CURRENT_CHAT_KEY);
        }
    } catch (error) {
        console.warn('Internal chat store could not be loaded:', error);
        state.chats = {};
        showToast?.('Internal chat history could not be loaded. New chats will retry saving automatically.', 'error');
    } finally {
        state.chatStoreReady = true;
    }
}

function migrateLegacyChatsIfNeeded() {
    if (Object.keys(state.chats || {}).length) return null;
    try {
        const raw = localStorage.getItem(LEGACY_STORAGE_KEY);
        if (!raw) return null;
        const chats = JSON.parse(raw) || {};
        if (!Object.keys(chats).length) return null;
        const currentChatId = localStorage.getItem(LEGACY_CURRENT_CHAT_KEY);
        return { chats, currentChatId: chats[currentChatId] ? currentChatId : null };
    } catch (error) {
        console.warn('Legacy chat migration failed:', error);
        return null;
    }
}

function scheduleChatStoreSync() {
    if (!state.chatStoreReady) return;
    clearTimeout(chatSyncTimer);
    chatSyncTimer = setTimeout(() => {
        syncChatStoreNow().catch((error) => {
            console.warn('Internal chat history could not be saved:', error);
            showToast?.('Chat history could not be saved to the internal store.', 'error');
        });
    }, 180);
}

async function syncChatStoreNow() {
    const res = await fetch('/api/chats/sync', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            chats: state.chats || {},
            current_chat_id: state.currentChatId || null,
        }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.chats = data.chats || {};
    state.currentChatId = data.current_chat_id || null;
    return data;
}

window.addEventListener('beforeunload', () => {
    if (!state.chatStoreReady) return;
    clearTimeout(chatSyncTimer);
    const payload = JSON.stringify({
        chats: state.chats || {},
        current_chat_id: state.currentChatId || null,
    });
    if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/chats/sync', new Blob([payload], { type: 'application/json' }));
    }
});

function loadSettings() {
    try {
        return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {};
    } catch { return {}; }
}
function saveSettingsToStorage(s) {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
}

function setCurrentChatId(chatId) {
    state.currentChatId = chatId || null;
    scheduleChatStoreSync();
}

// ─── DOM HELPERS ─────────────────────────────
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const DOM = {
    sidebar: $('#sidebar'),
    chatList: $('#chatList'),
    chatMessages: $('#chatMessages'),
    emptyState: $('#emptyState'),
    messageInput: $('#messageInput'),
    sendBtn: $('#sendBtn'),
    stopBtn: $('#stopBtn'),
    charCount: $('#charCount'),
    newChatBtn: $('#newChatBtn'),
    toggleSidebar: $('#toggleSidebar'),
    searchChats: $('#searchChats'),
    attachBtn: $('#attachBtn'),
    fileInput: $('#fileInput'),
    filePreview: $('#filePreview'),
    filePreviewName: $('#filePreviewName'),
    filePreviewRemove: $('#filePreviewRemove'),
    editPreviewPanel: $('#editPreviewPanel'),
    autoPilotToggle: $('#autoPilotToggle'),
    autoAcceptToggle: $('#autoAcceptToggle'),
    securityLevel: $('#securityLevel'),
    modelEffort: $('#modelEffort'),
    // Modals
    deleteModal: $('#deleteModal'),
    confirmDelete: $('#confirmDelete'),
    cancelDelete: $('#cancelDelete'),
    settingsModal: $('#settingsModal'),
    settingsBtn: $('#settingsBtn'),
    closeSettings: $('#closeSettings'),
    saveSettingsBtn: $('#saveSettingsBtn'),
    settingsFontSize: $('#settingsFontSize'),
    fontSizeValue: $('#fontSizeValue'),
    settingsEnterSend: $('#settingsEnterSend'),
    settingsVoice: $('#settingsVoice'),
    clearAllChatsBtn: $('#clearAllChatsBtn'),
    closeSidebar: $('#closeSidebar'),
    sidebarOverlay: $('#sidebarOverlay'),
};

// ─── INIT ────────────────────────────────────
let logoSrc = ''; // Loaded from the LOGO value in agent.py.

document.addEventListener('DOMContentLoaded', async () => {
    initPlanControls();
    await initChatStore();
    renderChatList();
    restoreLastChat();
    applySettings();
    setupEventListeners();
    loadLogo();
    loadVoices();
});

function initPlanControls() {
    const storedApproval = localStorage.getItem(APPROVAL_KEY);
    if (storedApproval === null) {
        const enabled = confirm('Enable auto accept edits? If disabled, changes will wait in the Accept/Reject panel first.');
        localStorage.setItem(APPROVAL_KEY, enabled ? 'auto' : 'manual');
    }
    state.autoAcceptEdits = localStorage.getItem(APPROVAL_KEY) === 'auto';
    state.autoPilot = localStorage.getItem(AUTO_PILOT_KEY) === 'on';
    if (state.autoPilot) {
        state.autoAcceptEdits = true;
        localStorage.setItem(APPROVAL_KEY, 'auto');
    }
    state.securityLevel = localStorage.getItem(SECURITY_KEY) || 'safe';
    state.modelEffort = localStorage.getItem(MODEL_EFFORT_KEY) || 'medium';
    if (!['no', 'low', 'medium', 'high', 'xhigh'].includes(state.modelEffort)) {
        state.modelEffort = 'medium';
        localStorage.setItem(MODEL_EFFORT_KEY, state.modelEffort);
    }
    if (state.autoPilot && state.securityLevel === 'ask_each_step') {
        state.securityLevel = 'safe';
        localStorage.setItem(SECURITY_KEY, state.securityLevel);
    }
    if (DOM.securityLevel) DOM.securityLevel.value = state.securityLevel;
    if (DOM.modelEffort) DOM.modelEffort.value = state.modelEffort;
    updatePlanControls();
}

function updatePlanControls() {
    if (DOM.autoPilotToggle) {
        DOM.autoPilotToggle.textContent = `Auto Pilot: ${state.autoPilot ? 'On' : 'Off'}`;
        DOM.autoPilotToggle.classList.toggle('active', state.autoPilot);
    }
    if (DOM.autoAcceptToggle) {
        DOM.autoAcceptToggle.textContent = `Auto accept: ${state.autoAcceptEdits ? 'On' : 'Off'}`;
        DOM.autoAcceptToggle.classList.toggle('active', state.autoAcceptEdits);
    }
    if (DOM.securityLevel) DOM.securityLevel.value = state.securityLevel;
    if (DOM.modelEffort) DOM.modelEffort.value = state.modelEffort;
}

async function loadLogo() {
    try {
        const res = await fetch('/api/config');
        const data = await res.json();
        if (data.logo) {
            logoSrc = data.logo;
            applyLogoToEmptyState();
        }
    } catch (e) { /* Keep the default SVG when no logo is available. */ }
}

function applyLogoToEmptyState() {
    if (!logoSrc) return;
    const logoIcon = document.querySelector('.logo-icon');
    if (logoIcon) {
        logoIcon.innerHTML = `<img src="${logoSrc}" alt="GaziGPT" style="width:64px;height:64px;border-radius:14px;object-fit:cover;">`;
        logoIcon.style.background = 'transparent';
        logoIcon.style.border = 'none';
    }
}
function getChatsSorted(filter = '') {
    const all = loadChatsFromStorage();
    let list = Object.values(all);
    if (filter) list = list.filter(c => c.title.toLowerCase().includes(filter.toLowerCase()));
    list.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
    return list;
}

function renderChatList(filter = '') {
    const chats = getChatsSorted(filter);

    if (!chats.length) {
        DOM.chatList.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text-muted);font-size:0.82rem;">${filter ? 'No results found' : 'No chats yet'}</div>`;
        return;
    }

    DOM.chatList.innerHTML = chats.map(c => `
        <div class="chat-item ${c.id === state.currentChatId ? 'active' : ''}" data-id="${c.id}" onclick="selectChat('${c.id}')">
            <div class="chat-item-icon">💬</div>
            <div class="chat-item-info">
                <div class="chat-item-title">${escapeHtml(c.title)}</div>
                <div class="chat-item-date">${formatDate(c.updated_at)}</div>
            </div>
            <button class="chat-item-delete" onclick="event.stopPropagation();showDeleteModal('${c.id}')" title="Delete">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
        </div>
    `).join('');
}

// ─── CHAT CRUD (internal backend store) ──────
function restoreLastChat() {
    const all = loadChatsFromStorage();
    const savedId = state.currentChatId;
    if (savedId && all[savedId]) {
        selectChat(savedId);
        return;
    }

    const latest = Object.values(all).sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))[0];
    if (latest) {
        selectChat(latest.id);
    } else {
        showEmptyState();
    }
}

function createChat() {
    setCurrentChatId(null);
    showEmptyState();
    DOM.messageInput.focus();
}

function selectChat(chatId) {
    const all = loadChatsFromStorage();
    const chat = all[chatId];
    if (chat) {
        setCurrentChatId(chatId);
        renderChatList();
        showChatView(chat.messages);
    }
    DOM.sidebar.classList.remove('open');
    DOM.sidebarOverlay.classList.remove('open');
}

function deleteChat(chatId) {
    const all = loadChatsFromStorage();
    delete all[chatId];
    saveChatsToStorage(all);
    if (state.currentChatId === chatId) {
        setCurrentChatId(null);
        showEmptyState();
    }
    renderChatList();
    showToast('Chat deleted', 'success');
}

function clearAllChats() {
    saveChatsToStorage({});
    setCurrentChatId(null);
    showEmptyState();
    renderChatList();
    showToast('All chats cleared', 'success');
}

// ─── VIEWS ───────────────────────────────────
function showChatView(messages) {
    DOM.emptyState.style.display = 'none';
    DOM.chatMessages.style.display = 'flex';
    DOM.chatMessages.style.flexDirection = 'column';
    DOM.chatMessages.innerHTML = '';
    messages.forEach(m => {
        if (!m.is_system) appendMessage(m.role, m.content, m.timestamp, false);
    });
    scrollToBottom();
    DOM.messageInput.focus();
}

function showEmptyState() {
    DOM.emptyState.style.display = 'flex';
    DOM.chatMessages.style.display = 'none';
    setCurrentChatId(null);
    renderChatList();
    applyLogoToEmptyState();
}

// ─── SEND MESSAGE (STREAMING) ────────────────
async function sendMessage() {
    const message = DOM.messageInput.value.trim();
    if (!message || state.isLoading) return;

    state.isLoading = true;
    state.abortController = new AbortController();
    DOM.sendBtn.style.display = 'none';
    DOM.stopBtn.style.display = 'flex';
    DOM.messageInput.value = '';
    DOM.messageInput.style.height = 'auto';
    updateCharCount();

    // Create a chat for the first message.
    if (!state.currentChatId) {
        const id = 'c_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
        const chat = {
            id,
            title: message.slice(0, 50) + (message.length > 50 ? '...' : ''),
            messages: [],
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
        };
        const all = loadChatsFromStorage();
        all[id] = chat;
        saveChatsToStorage(all);
        setCurrentChatId(id);
    }

    // Leave the empty state.
    if (DOM.emptyState.style.display !== 'none') {
        DOM.emptyState.style.display = 'none';
        DOM.chatMessages.style.display = 'flex';
        DOM.chatMessages.style.flexDirection = 'column';
        DOM.chatMessages.innerHTML = '';
    }

    // Show and save the user message.
    const ts = new Date().toISOString();
    appendMessage('user', message, ts);

    const all = loadChatsFromStorage();
    const chat = all[state.currentChatId];
    if (!chat) { state.isLoading = false; DOM.sendBtn.disabled = false; return; }

    chat.messages.push({ role: 'user', content: message, timestamp: ts });
    if (chat.messages.length === 1) {
        chat.title = message.slice(0, 50) + (message.length > 50 ? '...' : '');
    }
    chat.updated_at = new Date().toISOString();
    all[state.currentChatId] = chat;
    saveChatsToStorage(all);

    // File attachment
    const attachedFile = state.attachedFile;
    let fileContent = '';
    let imageAnalyzed = false;
    clearFileAttachment();

    // Analyze attached images before sending.
    if (attachedFile && attachedFile.isImage) {
        appendMessage('assistant', 'Analyzing image...', undefined, true);
        try {
            const analyzeRes = await fetch('/api/analyze-image', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image_data: attachedFile.content, filename: attachedFile.name }),
            });
            const analyzeData = await analyzeRes.json();
            if (analyzeData.success) {
                fileContent = `\n\n--- Attached Image Analysis ---\n${analyzeData.description}\n--- End Image Analysis ---`;
                imageAnalyzed = true;
            } else {
                fileContent = `\n\n--- Image analysis failed: ${analyzeData.error || 'Unknown error'} ---`;
            }
        } catch (err) {
            fileContent = `\n\n--- Image analysis connection error: ${err.message} ---`;
        }
        // Remove the analysis message.
        const lastMsg = DOM.chatMessages.querySelector('.message-assistant:last-child');
        if (lastMsg && lastMsg.textContent.includes('Analyzing image')) {
            lastMsg.remove();
        }
    } else if (attachedFile) {
        fileContent = attachedFile.content;
    }

    // Show typing state.
    showTypingIndicator();

    let fullText = '';
    const ats = new Date().toISOString();
    
    // Read long-term memory from localStorage.
    const longTermMemory = JSON.parse(localStorage.getItem('gazigpt_long_term_memory') || '[]');

    try {
        const res = await fetch('/api/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                messages: chat.messages.filter(m => !m.is_system).map(m => ({ role: m.role, content: m.content })),
                message: message,
                file_content: fileContent,
                image_ratio: document.getElementById('imageRatio')?.value || '1:1',
                model: state.selectedModel,
                long_term_memory: longTermMemory,
                approval_mode: state.autoAcceptEdits ? 'auto' : 'manual',
                security_level: state.securityLevel,
                model_effort: state.modelEffort,
                auto_accept_edits: state.autoAcceptEdits,
                auto_authorize: state.autoPilot,
                auto_fix_enabled: state.autoPilot,
            }),
            signal: state.abortController.signal,
        });

        if (!res.ok) {
            hideTypingIndicator();
            const errorMessageForStorage = `Server error (${res.status}). Please try again.`;
            chat.messages.push({ role: 'assistant', content: errorMessageForStorage, timestamp: new Date().toISOString() });
            chat.updated_at = new Date().toISOString();
            all[state.currentChatId] = chat;
            saveChatsToStorage(all);
            renderChatList();
            appendMessage('assistant', `Server error (${res.status}). Please try again.`);
            state.isLoading = false;
            state.abortController = null;
            DOM.stopBtn.style.display = 'none';
            DOM.sendBtn.style.display = 'flex';
            return;
        }

        hideTypingIndicator();

        // Create the streaming message shell.
        let msgDiv = createStreamingMessage(ats);
        let bodyEl = msgDiv.querySelector('.message-body');
        let finalText = '';  // Son kaydedilecek metin
        let suffixHTML = ''; // Badge'ler burada tutulacak
        let prefixHTML = ''; // Generated images are kept here.


        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) { console.log('[DEBUG] Stream done, fullText length:', fullText.length); break; }

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const ev = JSON.parse(line.slice(6));
                    console.log('[DEBUG] SSE event:', ev.type, ev.type === 'chunk' ? ev.content?.substring(0,50) : '');

                    if (ev.type === 'chunk') {
                        fullText += ev.content;
                        
                        let formattedText = formatThinkTags(fullText);

                        bodyEl.innerHTML = prefixHTML + renderMarkdown(formattedText) + suffixHTML;
                        bodyEl.querySelectorAll('pre code').forEach(b => {
                            if (!b.dataset.highlighted) {
                                hljs.highlightElement(b);
                                b.dataset.highlighted = 'true';
                            }
                        });
                        scrollToBottom();

                    } else if (ev.type === 'extended_phase') {
                        // GaziGPT Extended stage indicator.
                        const phaseId = ev.phase || '';
                        const phaseLabel = ev.label || 'Processing...';
                        const phaseColors = {
                            meta_prompt: '#e879f9',
                            semantic_memory: '#a78bfa',
                            memory: '#8b5cf6',
                            thinking: '#f59e0b',
                            code_architect: '#0ea5e9',
                            implementation: '#14b8a6',
                            code_review: '#ef4444',
                            ensemble: '#06b6d4',
                            synthesis: '#10b981',
                            verification: '#22c55e',
                        };
                        const color = phaseColors[phaseId] || '#6366f1';
                        
                        // Mark the previous stage as complete.
                        const prevPhase = bodyEl.querySelector('.extended-phase-active');
                        if (prevPhase) {
                            prevPhase.classList.remove('extended-phase-active');
                            prevPhase.querySelector('.phase-spinner')?.remove();
                            const checkMark = document.createElement('span');
                            checkMark.style.cssText = 'color:#22c55e;margin-right:6px;';
                            checkMark.textContent = '✓';
                            prevPhase.prepend(checkMark);
                            prevPhase.style.opacity = '0.6';
                        }
                        
                        // Clear old phase indicators during synthesis.
                        if (phaseId === 'synthesis') {
                            bodyEl.querySelectorAll('.extended-phase-indicator').forEach(el => el.remove());
                        }
                        
                        // Add a new phase indicator before synthesis/verification.
                        if (phaseId !== 'synthesis' && phaseId !== 'verification') {
                            const phaseEl = document.createElement('div');
                            phaseEl.className = 'extended-phase-indicator extended-phase-active';
                            phaseEl.style.cssText = `
                                display:flex; align-items:center; gap:10px; padding:10px 16px;
                                background:${color}15; border-left:3px solid ${color};
                                border-radius:0 10px 10px 0; margin:4px 0; font-size:0.88rem;
                                color:${color}; animation:fadeIn 0.3s ease;
                            `;
                            phaseEl.innerHTML = `
                                <div class="phase-spinner" style="width:16px;height:16px;border:2px solid ${color}40;border-top-color:${color};border-radius:50%;animation:tool-spin 0.7s linear infinite;"></div>
                                <span>${phaseLabel}</span>
                            `;
                            bodyEl.appendChild(phaseEl);
                        }
                        scrollToBottom();

                    } else if (ev.type === 'plan_phase') {
                        renderPlanStatus(ev.label || ev.phase || 'Plan phase is running...');

                    } else if (ev.type === 'plan_update') {
                        renderPlanUpdate(ev);

                    } else if (ev.type === 'request_wait') {
                        renderPlanStatus(ev.message || `Waiting ${ev.seconds || 20}s for the next request...`);

                    } else if (ev.type === 'stage_validation' || ev.type === 'final_validation') {
                        renderPlanStatus(ev.message || 'Running status update...');

                    } else if (ev.type === 'validation_error' || ev.type === 'repair_start') {
                        renderPlanStatus(ev.message || 'Running status update...');

                    } else if (ev.type === 'edit_preview') {
                        renderEditPreview(ev.edit, ev.auto_applied);
                        const normalizedEdit = state.currentEditPreview || ev.edit || {};
                        const files = normalizedEdit.files || [];
                        const blockId = 'edit_group_' + Math.random().toString(36).substring(2, 9);
                        const editBlocksHTML = renderChatFileEditBlocks(files, ev.auto_applied, blockId);
                        suffixHTML = `<hr style="border-color:rgba(255,255,255,0.05);margin:16px 0 12px 0;">
<div id="${blockId}" style="margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px;">${editBlocksHTML}</div>`;
                        // Trigger JS animation after innerHTML is set
                        setTimeout(() => animateChatFileEditBlocks(blockId, files, ev.auto_applied), 100);

                    } else if (ev.type === 'permission_required') {
                        renderPlanStatus(ev.message || 'Running status update...');

                    } else if (ev.type === 'ask_question') {
                        renderAskQuestionModal(ev.question, ev.options);

                    } else if (ev.type === 'tool_start') {
                        const toolCount = ev.count || 1;
                        const tools = ev.tools || [];
                        
                        if (tools.includes('generate_image')) {
                            bodyEl.innerHTML = `
                                <div class="image-generating-box">
                                    <div class="image-gen-shimmer"></div>
                                    <div class="image-gen-content">
                                        <div class="image-gen-spinner"></div>
                                        <div class="image-gen-text">🎨 Generating image...</div>
                                        <div class="image-gen-hint">Please wait, usually 10-20 seconds</div>
                                    </div>
                                </div>
                            `;
                        } else {
                            bodyEl.innerHTML = `
                                <div class="tool-status" style="display:flex;align-items:center;gap:10px;padding:12px 16px;background:rgba(99,102,241,0.08);border-radius:12px;margin:4px 0;">
                                    <div class="tool-spinner" style="width:20px;height:20px;border:2.5px solid rgba(99,102,241,0.2);border-top-color:rgb(99,102,241);border-radius:50%;animation:tool-spin 0.8s linear infinite;"></div>
                                    <span style="color:var(--text-secondary);font-size:0.9rem;">💻 Writing code and preparing file operations...</span>
                                </div>
                            `;
                        }
                        scrollToBottom();

                    } else if (ev.type === 'tool_done') {
                        // Tool completed; the second stream may follow.
                        const toolNames = ev.tools || [];
                        const toolResults = ev.results || [];
                        const hasPlanEdit = toolResults.some(tr => tr.plan_id);
                        const toolBadges = toolNames.map(t => `<span style="background:rgba(99,102,241,0.1);color:rgb(129,140,248);padding:3px 10px;border-radius:6px;font-size:0.75rem;font-weight:500;border:1px solid rgba(99,102,241,0.2);">${t}</span>`).join(' ');
                        
                        // Keep generated images as a prefix so they remain visible.
                        prefixHTML = '';
                        for (const tr of toolResults) {
                            if (tr.image_url) {
                                const uid = Math.random().toString(36).substr(2, 9);
                                const proxyUrl = '/api/image-proxy?url=' + encodeURIComponent(tr.image_url);
                                prefixHTML += `
<div class="generated-image-container">
    <div id="loader_${uid}" class="image-generating-box" style="margin:0; max-width:none; border:none; border-radius:0; border-bottom:1px solid rgba(255,255,255,0.06);">
        <div class="image-gen-shimmer"></div>
        <div class="image-gen-content">
            <div class="image-gen-spinner"></div>
            <div class="image-gen-text">🎨 Generating image...</div>
            <div class="image-gen-hint">Please wait, usually 10-20 seconds</div>
        </div>
    </div>
    <img id="img_${uid}" src="${proxyUrl}" alt="Generated Image" loading="eager" style="display:none;" onload="document.getElementById('loader_${uid}').style.display='none'; this.style.display='block';">
    <div class="generated-image-actions">
        <button onclick="openImageLightbox(document.getElementById('img_${uid}').src)" class="btn-fullscreen">🔍 Fullscreen</button>
        <button onclick="downloadImage(document.getElementById('img_${uid}').src, 'image.png')" class="btn-download">Download</button>
    </div>
</div>\n\n`;
                            }
                        }
                        
                        if (!hasPlanEdit) {
                            fullText = '';
                            suffixHTML = `<hr style="border-color:rgba(255,255,255,0.05);margin:16px 0 12px 0;">
<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
<span style="font-size:0.8rem;color:rgba(255,255,255,0.5);display:flex;align-items:center;gap:4px;"><span style="color:#10b981;">✅</span> Tools completed:</span>
${toolBadges}
</div>\n\n`;
                            bodyEl.innerHTML = prefixHTML + suffixHTML + '<span class="stream-cursor">▋</span>';
                        } else {
                            fullText = '';
                            suffixHTML += `<hr style="border-color:rgba(255,255,255,0.05);margin:16px 0 12px 0;">
<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
<span style="font-size:0.8rem;color:rgba(255,255,255,0.5);display:flex;align-items:center;gap:4px;"><span style="color:#10b981;">✅</span> Edit plan ready:</span>
${toolBadges}
</div>\n\n`;
                            bodyEl.innerHTML = prefixHTML + suffixHTML + '<span class="stream-cursor">▋</span>';
                        }
                        scrollToBottom();

                    } else if (ev.type === 'error') {
                        bodyEl.innerHTML = `<div style="color:#ef4444;">❌ Error: ${ev.message || 'Unknown error'}</div>`;
                        scrollToBottom();

                    } else if (ev.type === 'done') {
                        // Stream cursor'u temizle
                        const cursor = bodyEl.querySelector('.stream-cursor');
                        if (cursor) cursor.remove();
                        
                        // Stop any remaining active phase indicators.
                        const activePhase = bodyEl.querySelector('.extended-phase-active');
                        if (activePhase) {
                            activePhase.classList.remove('extended-phase-active');
                            activePhase.querySelector('.phase-spinner')?.remove();
                            const checkMark = document.createElement('span');
                            checkMark.style.cssText = 'color:#22c55e;margin-right:6px;';
                            checkMark.textContent = '✓';
                            activePhase.prepend(checkMark);
                            activePhase.style.opacity = '0.6';
                        }

                        // Add copy buttons to code blocks.
                        bodyEl.querySelectorAll('pre').forEach(pre => {
                            if (!pre.querySelector('.code-header')) {
                                const code = pre.querySelector('code');
                                const lang = code?.className?.match(/language-(\w+)/)?.[1] || 'code';
                                const header = document.createElement('div');
                                header.className = 'code-header';
                                header.innerHTML = `<span>${lang}</span><button class="copy-btn" onclick="copyCode(this)">📋 Copy</button>`;
                                pre.insertBefore(header, pre.firstChild);
                            }
                        });
                        
                        // Collapse thinking boxes.
                        bodyEl.querySelectorAll('details.thinking-box').forEach(d => d.removeAttribute('open'));

                        // Save final text including generated images and badges.
                        finalText = (prefixHTML ? prefixHTML + "\n\n" : "") + stripToolBlocks(fullText) + (suffixHTML ? "\n\n" + suffixHTML : "");
                    }
                } catch (e) { /* skip malformed SSE line */ }
            }
        }

        if (finalText || fullText) {
            chat.messages.push({ role: 'assistant', content: finalText || fullText, timestamp: ats });
            
            // Save to long-term memory.
            if (state.selectedModel === 'GaziGPT Extended') {
                longTermMemory.push({ user: message, ai: finalText || fullText });
                if (longTermMemory.length > 50) {
                    longTermMemory.shift();
                }
                localStorage.setItem('gazigpt_long_term_memory', JSON.stringify(longTermMemory));
            }
            
            // Show and populate the action bar.
            const actionsDiv = msgDiv.querySelector('.message-actions');
            if (actionsDiv) {
                actionsDiv.style.display = 'flex';
                actionsDiv.innerHTML = `
                    <button class="action-btn" onclick="copyMessageText(this)" title="Copy">
                        <svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
                    </button>
                    <button class="action-btn" onclick="likeMessage(this)" title="Like">
                        <svg viewBox="0 0 24 24"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"></path></svg>
                    </button>
                    <button class="action-btn" onclick="dislikeMessage(this)" title="Dislike">
                        <svg viewBox="0 0 24 24"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"></path></svg>
                    </button>
                    <button class="action-btn" onclick="shareMessage(this)" title="Share">
                        <svg viewBox="0 0 24 24"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line></svg>
                    </button>
                    <button class="action-btn" onclick="regenerateMessage(this)" title="Regenerate">
                        <svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
                    </button>
                    <button class="action-btn" onclick="playTTSMessage(this)" title="Read aloud (TTS)">
                        <svg viewBox="0 0 24 24"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>
                    </button>
                `;
            }
        }

        // Final cleanup: remove leftover stream cursors.
        const leftoverCursor = bodyEl.querySelector('.stream-cursor');
        if (leftoverCursor) leftoverCursor.remove();

    } catch (err) {
        hideTypingIndicator();
        if (err.name === 'AbortError') {
            // The user stopped generation; save partial content.
            if (fullText || prefixHTML || suffixHTML) {
                const partialText = (prefixHTML ? prefixHTML + "\n\n" : "") + stripToolBlocks(fullText) + (suffixHTML ? "\n\n" + suffixHTML : "");
                chat.messages.push({ role: 'assistant', content: partialText, timestamp: ats });
            }
            // Clear the cursor.
            const cursorEl = document.querySelector('.message-assistant:last-child .stream-cursor');
            if (cursorEl) cursorEl.remove();
            showToast('Response stopped', 'success');
        } else {
            appendMessage('assistant', '❌ Connection error: ' + err.message);
        }
    }

    chat.updated_at = new Date().toISOString();
    all[state.currentChatId] = chat;
    saveChatsToStorage(all);
    renderChatList();

    state.isLoading = false;
    state.abortController = null;
    DOM.stopBtn.style.display = 'none';
    DOM.sendBtn.style.display = 'flex';
    DOM.messageInput.focus();
}

/** Stop the streaming response. */
function stopGeneration() {
    if (state.abortController) {
        state.abortController.abort();
    }
}

/** Empty assistant message shell for streaming. */
function createStreamingMessage(timestamp) {
    const time = formatTime(timestamp);
    const div = document.createElement('div');
    div.className = 'message message-assistant';
    div.innerHTML = `
        <div class="message-header">
            <div class="message-avatar">${getAssistantAvatar()}</div>
            <span class="message-sender">GaziGPT</span>
            <span class="message-time">${time}</span>
        </div>
        <div class="message-body"><span class="stream-cursor">▊</span></div>
        <div class="message-actions" style="display:none;"></div>
    `;
    DOM.chatMessages.appendChild(div);
    fixAvatarBg(div);
    scrollToBottom();
    return div;
}

/** Logo varsa img (arkas\u0131 transparent), yoksa emoji d\u00f6ner */
function getAssistantAvatar() {
    if (logoSrc) return `<img src="${logoSrc}" alt="GaziGPT" style="width:36px;height:36px;border-radius:8px;object-fit:cover;">`;
    return '✨';
}

/** Remove the avatar background when a custom logo is used. */
function fixAvatarBg(el) {
    if (!logoSrc) return;
    const avatar = el.querySelector('.message-avatar');
    if (avatar) { avatar.style.background = 'transparent'; }
}

function renderPlanStatus(label) {
    if (!DOM.editPreviewPanel || state.currentEditPlanId) return;
    DOM.editPreviewPanel.style.display = 'block';
    DOM.editPreviewPanel.innerHTML = `
        <div class="edit-preview-header">
            <div class="edit-preview-title">${escapeHtml(label)}</div>
            <div class="edit-preview-actions"><button type="button" onclick="clearEditPreview()">Hide</button></div>
        </div>
    `;
}

function renderPlanUpdate(update) {
    if (!DOM.editPreviewPanel || state.currentEditPlanId) return;
    const plan = update.plan || {};
    const files = Array.isArray(plan.files) ? plan.files : [];
    const folders = Array.isArray(plan.folders) ? plan.folders : [];
    const workers = Array.isArray(update.workers) ? update.workers : [];
    const fileRows = files.map(file => `
        <div class="edit-file-row">
            <div class="edit-file-main" onclick="this.parentElement.classList.toggle('open')">
                <span class="edit-file-path">${escapeHtml(file.path || '')}</span>
                <span>${escapeHtml(file.purpose || '')}⌄</span>
            </div>
            <pre class="edit-file-preview">${escapeHtml(JSON.stringify(file, null, 2))}</pre>
        </div>
    `).join('');
    const workerRows = workers.map(worker => `
        <div class="edit-file-row">
            <div class="edit-file-main">
                <span class="edit-file-path">${escapeHtml(worker.path || '')}</span>
                <span>${escapeHtml(worker.status || 'waiting')}</span>
            </div>
        </div>
    `).join('');
    DOM.editPreviewPanel.style.display = 'block';
    DOM.editPreviewPanel.innerHTML = `
        <div class="edit-preview-header">
            <div class="edit-preview-title">${escapeHtml(update.message || 'Plan phase is running...')}</div>
            <div class="edit-preview-actions"><button type="button" onclick="clearEditPreview()">Hide</button></div>
        </div>
        <div class="edit-plan-section">
            <div><strong>${escapeHtml(plan.project_name || 'Plan')}</strong></div>
            <div class="edit-plan-muted">${escapeHtml(plan.summary || '')}</div>
            <div class="edit-plan-muted">${folders.length ? 'Folders: ' + escapeHtml(folders.join(', ')) : 'No folders or single-file project'}</div>
        </div>
        ${fileRows ? `<div class="edit-plan-subtitle">Files</div>${fileRows}` : ''}
        ${workerRows ? `<div class="edit-plan-subtitle">File agents</div>${workerRows}` : ''}
    `;
}

function cleanDiffNumber(value) {
    const number = Number(value || 0);
    if (!Number.isFinite(number) || Object.is(number, -0)) return 0;
    return Math.max(0, Math.trunc(number));
}

function renderDiffStats(added, removed, { compact = false } = {}) {
    const add = cleanDiffNumber(added);
    const rem = cleanDiffNumber(removed);
    const parts = [];
    if (add > 0) parts.push(`<span class="diff-add">+${add}</span>`);
    if (rem > 0) parts.push(`<span class="diff-remove">-${rem}</span>`);
    if (!parts.length) return compact ? '' : '<span class="diff-neutral">0 changes</span>';
    return parts.join(' ');
}

function normalizeEditPreviewPayload(edit) {
    if (!edit || !Array.isArray(edit.files)) return edit;
    const previous = state.currentEditPreview;
    const files = edit.files || [];
    const totals = edit.totals || { files: files.length, added: 0, removed: 0 };
    const incomingZero = files.length > 0 && Number(totals.added || 0) === 0 && Number(totals.removed || 0) === 0;
    if (!previous || !Array.isArray(previous.files)) {
        return edit;
    }

    const currentPaths = files.map(file => file.path || '').sort().join('\n');
    const previousPaths = previous.files.map(file => file.path || '').sort().join('\n');
    const previousTotals = previous.totals || {};
    const previousHasStats = Number(previousTotals.added || 0) > 0 || Number(previousTotals.removed || 0) > 0;
    if (!previousHasStats || currentPaths !== previousPaths) {
        return edit;
    }

    const previousByPath = new Map(previous.files.map(file => [file.path, file]));
    let reusedStats = false;
    const mergedFiles = files.map(file => {
        const oldFile = previousByPath.get(file.path);
        if (!oldFile) return file;
        const hasOwnStats = Number(file.added || 0) > 0 || Number(file.removed || 0) > 0;
        if (hasOwnStats) return file;
        const oldHasStats = Number(oldFile.added || 0) > 0 || Number(oldFile.removed || 0) > 0;
        if (!incomingZero && !oldHasStats) return file;
        reusedStats = true;
        return {
            ...file,
            added: oldFile.added || 0,
            removed: oldFile.removed || 0,
            preview: file.preview || oldFile.preview || '',
        };
    });
    if (!incomingZero && !reusedStats) {
        return edit;
    }
    return {
        ...edit,
        files: mergedFiles,
        totals: {
            files: mergedFiles.length,
            added: mergedFiles.reduce((sum, file) => sum + Number(file.added || 0), 0),
            removed: mergedFiles.reduce((sum, file) => sum + Number(file.removed || 0), 0),
        },
    };
}

function renderEditPreview(edit, autoApplied = false) {
    if (!DOM.editPreviewPanel || !edit) return;
    edit = normalizeEditPreviewPayload(edit);
    state.currentEditPreview = edit;
    state.currentEditPlanId = edit.plan_id;
    const files = edit.files || [];
    const totals = edit.totals || { files: files.length, added: 0, removed: 0 };
    const folderAction = renderProjectFolderAction(edit.project_folder);
    const rows = files.map(file => `
        <div class="edit-file-row">
            <div class="edit-file-main" onclick="this.parentElement.classList.toggle('open')">
                <span class="edit-file-path">${escapeHtml(file.path)}</span>
                <span style="color: #888; font-size: 0.85em; margin-left: 10px; flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${escapeHtml(file.purpose || 'File will be updated')}</span>
                ${!autoApplied ? `<button type="button" onclick="event.stopPropagation(); acceptCurrentEdit()" style="background-color: #22c55e; color: white; border: none; padding: 2px 8px; border-radius: 4px; margin-right: 5px; font-size: 0.8em; cursor: pointer;">Accept</button><button type="button" onclick="event.stopPropagation(); rejectCurrentEdit()" style="background-color: #ef4444; color: white; border: none; padding: 2px 8px; border-radius: 4px; margin-right: 10px; font-size: 0.8em; cursor: pointer;">Reject</button>` : ''}
                <span>${renderDiffStats(file.added, file.removed, { compact: true })}⌄</span>
            </div>
            <pre class="edit-file-preview">${escapeHtml(file.preview || '(no preview)')}</pre>
        </div>
    `).join('');
    DOM.editPreviewPanel.style.display = 'block';
    DOM.editPreviewPanel.innerHTML = `
        <div class="edit-preview-header">
            <div class="edit-preview-title">${totals.files || files.length} files changed&nbsp; ${renderDiffStats(totals.added, totals.removed)}${autoApplied ? ' · applied' : ''}</div>
            <div class="edit-preview-actions">
                <button type="button" onclick="undoEdit()">Undo ↶</button>
                <button type="button" onclick="redoEdit()">Redo ↷</button>
                <button type="button" onclick="inspectCurrentEdit()">Inspect ↗</button>
                <button type="button" onclick="clearEditPreview()">×</button>
            </div>
        </div>
        ${rows}
        ${folderAction}
    `;
}

function renderProjectFolderAction(folder) {
    if (!folder) return '';
    const relPath = folder.path || '.';
    const displayPath = folder.absolute_path || relPath;
    return `
        <div class="project-folder-row">
            <button type="button" class="project-folder-link" data-path="${escapeHtml(relPath)}">
                Open project folder: ${escapeHtml(displayPath)}
            </button>
        </div>
    `;
}

async function openProjectFolder(path, button) {
    const originalText = button?.textContent;
    if (button) {
        button.disabled = true;
        button.textContent = 'Opening folder...';
    }
    try {
        const res = await fetch('/api/open-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: path || '.' }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Could not open folder');
        showToast('Folder opened', 'success');
    } catch (err) {
        showToast(err.message || 'Could not open folder', 'error');
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = originalText;
        }
    }
}

function clearEditPreview() {
    if (DOM.editPreviewPanel) {
        DOM.editPreviewPanel.style.display = 'none';
        DOM.editPreviewPanel.innerHTML = '';
    }
    state.currentEditPlanId = null;
    state.currentEditPreview = null;
}

async function postEditAction(url, body = {}) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Action failed');
    return data;
}

async function acceptCurrentEdit() {
    if (!state.currentEditPlanId) return;
    try {
        const data = await postEditAction('/api/edits/accept', { plan_id: state.currentEditPlanId });
        renderEditPreview(data.edit, true);
        showToast('Edits applied', 'success');
    } catch (err) {
        showToast(err.message, 'error');
    }
}

async function rejectCurrentEdit() {
    if (!state.currentEditPlanId) return;
    try {
        await postEditAction('/api/edits/reject', { plan_id: state.currentEditPlanId });
        clearEditPreview();
        showToast('Edits rejected', 'success');
    } catch (err) {
        showToast(err.message, 'error');
    }
}

async function undoEdit() {
    try {
        const data = await postEditAction('/api/edits/undo');
        renderEditPreview(data.edit, true);
        showToast('Last edit batch undone', 'success');
    } catch (err) {
        showToast(err.message, 'error');
    }
}

async function redoEdit() {
    try {
        const data = await postEditAction('/api/edits/redo');
        renderEditPreview(data.edit, true);
        showToast('Edit batch redone', 'success');
    } catch (err) {
        showToast(err.message, 'error');
    }
}

function inspectCurrentEdit() {
    if (!state.currentEditPlanId) return;
    window.open(`/api/edits/${encodeURIComponent(state.currentEditPlanId)}`, '_blank');
}

// ─── MESSAGE RENDERING ──────────────────────
function formatThinkTags(text) {
    // Hide gazi_tool blocks completely, including unfinished streamed blocks.
    let isCoding = /```gazi_tool/i.test(text) && !/```gazi_tool[\s\S]*?```/i.test(text);
    let answerText = text.replace(/```gazi_tool[\s\S]*?```/gi, '');
    answerText = answerText.replace(/```gazi_tool[\s\S]*$/i, '');
    answerText = answerText.replace(/(^|\n)\s*gazi_tool\s*\n[\s\S]*$/i, '$1');

    if (!/<\/?think>/i.test(answerText) && !isCoding) return answerText;

    let thoughts = [];
    
    // Add a compact status badge while a tool block is still streaming.
    if (isCoding) {
        answerText += `\n\n<div class="tool-status" style="display:inline-flex;align-items:center;gap:8px;padding:8px 14px;background:rgba(139, 92, 246, 0.1);border-radius:10px;color:var(--primary);font-size:0.85rem;font-weight:500;margin-top:10px;border:1px solid rgba(139, 92, 246, 0.2);"><div class="phase-spinner" style="width:14px;height:14px;border:2px solid rgba(139,92,246,0.2);border-top-color:rgb(139,92,246);border-radius:50%;animation:tool-spin 0.7s linear infinite;"></div>Writing files/code...</div>`;
    }

    // Remove completed <think>...</think> blocks.
    let closedPattern = /<think>([\s\S]*?)<\/think>/gi;
    let m;
    let replacements = [];
    while ((m = closedPattern.exec(text)) !== null) {
        thoughts.push(m[1].trim());
        replacements.push(m[0]);
    }
    for (const r of replacements) {
        answerText = answerText.replace(r, '');
    }
    
    // Capture a final unclosed <think> block while streaming.
    let unclosedMatch = answerText.match(/<think>([\s\S]*)$/i);
    let isStillThinking = false;
    if (unclosedMatch) {
        thoughts.push(unclosedMatch[1].trim());
        answerText = answerText.replace(unclosedMatch[0], '');
        isStillThinking = true;
    }
    
    // Remove stray think tags.
    answerText = answerText.replace(/<\/?think>/gi, '');
    thoughts = thoughts.filter(t => t.length > 0);
    if (thoughts.length === 0) return answerText;
    
    let thoughtsText = thoughts.join('\n\n');
    let openAttr = isStillThinking ? 'open' : '';
    
    return `\n\n<details class="thinking-box" ${openAttr}><summary class="thinking-header"><span class="think-icon">Brain</span> Thinking process</summary><div class="thinking-content">\n\n${thoughtsText}\n\n</div></details>\n\n${answerText}`;
}

function stripToolBlocks(text) {
    return (text || '')
        .replace(/```gazi_tool[\s\S]*?```/gi, '')
        .replace(/```gazi_tool[\s\S]*$/i, '')
        .replace(/(^|\n)\s*gazi_tool\s*\n[\s\S]*$/i, '$1')
        .trim();
}

function appendMessage(role, content, timestamp, scroll = true) {
    const isUser = role === 'user';
    const time = timestamp ? formatTime(timestamp) : formatTime(new Date().toISOString());
    let renderedContent = content;
    
    if (!isUser) {
        renderedContent = formatThinkTags(renderedContent);
    }
    
    const rendered = isUser ? escapeHtml(renderedContent) : renderMarkdown(renderedContent);

    const div = document.createElement('div');
    div.className = `message message-${role}`;
    div.innerHTML = `
        <div class="message-header">
            <div class="message-avatar">${isUser ? '👤' : getAssistantAvatar()}</div>
            <span class="message-sender">${isUser ? 'You' : 'GaziGPT'}</span>
            <span class="message-time">${time}</span>
        </div>
        <div class="message-body">${rendered}</div>
    `;
    if (!isUser) {
        div.innerHTML += `
        <div class="message-actions">
            <button class="action-btn" onclick="copyMessageText(this)" title="Copy">
                <svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>
            </button>
            <button class="action-btn" onclick="likeMessage(this)" title="Like">
                <svg viewBox="0 0 24 24"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"></path></svg>
            </button>
            <button class="action-btn" onclick="dislikeMessage(this)" title="Dislike">
                <svg viewBox="0 0 24 24"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"></path></svg>
            </button>
            <button class="action-btn" onclick="shareMessage(this)" title="Share">
                <svg viewBox="0 0 24 24"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line></svg>
            </button>
            <button class="action-btn" onclick="regenerateMessage(this)" title="Regenerate">
                <svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>
            </button>
            <button class="action-btn" onclick="playTTSMessage(this)" title="Read aloud (TTS)">
                <svg viewBox="0 0 24 24"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>
            </button>
        </div>`;
    }
    DOM.chatMessages.appendChild(div);
    if (!isUser) fixAvatarBg(div);

    // Highlight code
    div.querySelectorAll('pre code').forEach(block => hljs.highlightElement(block));

    // Copy buttons
    div.querySelectorAll('pre').forEach(pre => {
        if (!pre.querySelector('.code-header')) {
            const code = pre.querySelector('code');
            const lang = code?.className?.match(/language-(\w+)/)?.[1] || 'code';
            const header = document.createElement('div');
            header.className = 'code-header';
            header.innerHTML = `<span>${lang}</span><button class="copy-btn" onclick="copyCode(this)">📋 Copy</button>`;
            pre.insertBefore(header, pre.firstChild);
        }
    });

    if (scroll) scrollToBottom();
}

// ─── TYPING INDICATOR (message style) ───────
function showTypingIndicator() {
    const el = document.createElement('div');
    el.className = 'typing-message';
    el.id = 'typingIndicator';
    el.innerHTML = `
        <div class="message-header">
            <div class="message-avatar">${getAssistantAvatar()}</div>
            <span class="message-sender">GaziGPT</span>
            <span class="message-time">thinking...</span>
        </div>
        <div class="typing-dots">
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
        </div>
    `;
    DOM.chatMessages.appendChild(el);
    scrollToBottom();
}
function hideTypingIndicator() {
    const el = document.getElementById('typingIndicator');
    if (el) el.remove();
}

// ─── FILE ATTACHMENT ─────────────────────────
const IMAGE_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.gif', '.webp'];

function isImageFile(filename) {
    const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'));
    return IMAGE_EXTENSIONS.includes(ext);
}

function handleFileSelect(e) {
    const file = e.target.files[0];
    if (!file) return;

    const isImage = isImageFile(file.name);

    if (isImage) {
        // Image file, max 10MB.
        if (file.size > 10_000_000) {
            showToast('Image file is too large (max 10MB)', 'error');
            return;
        }
        const reader = new FileReader();
        reader.onload = (ev) => {
            const base64Data = ev.target.result; // data:image/...;base64,...
            state.attachedFile = { name: file.name, content: base64Data, isImage: true };
            DOM.filePreviewName.textContent = `🖼️ ${file.name}`;
            DOM.filePreview.style.display = 'flex';
            // Show preview.
            const previewImg = document.getElementById('filePreviewImage');
            const previewThumb = document.getElementById('filePreviewThumb');
            if (previewImg && previewThumb) {
                previewThumb.src = base64Data;
                previewImg.style.display = 'block';
            }
        };
        reader.readAsDataURL(file);
    } else {
        // Text file, max 500KB.
        if (file.size > 500_000) {
            showToast('File is too large (max 500KB)', 'error');
            return;
        }
        const reader = new FileReader();
        reader.onload = (ev) => {
            state.attachedFile = { name: file.name, content: ev.target.result, isImage: false };
            DOM.filePreviewName.textContent = `📎 ${file.name}`;
            DOM.filePreview.style.display = 'flex';
            // Hide preview.
            const previewImg = document.getElementById('filePreviewImage');
            if (previewImg) previewImg.style.display = 'none';
        };
        reader.readAsText(file);
    }
    DOM.fileInput.value = ''; // reset
}

function clearFileAttachment() {
    state.attachedFile = null;
    DOM.filePreview.style.display = 'none';
    DOM.filePreviewName.textContent = '';
    const previewImg = document.getElementById('filePreviewImage');
    if (previewImg) previewImg.style.display = 'none';
}

// ─── DELETE MODAL ────────────────────────────
function showDeleteModal(chatId) {
    state.deleteTargetId = chatId;
    DOM.deleteModal.classList.add('show');
}
function hideDeleteModal() {
    DOM.deleteModal.classList.remove('show');
    state.deleteTargetId = null;
}
function confirmDeleteChat() {
    if (state.deleteTargetId) deleteChat(state.deleteTargetId);
    hideDeleteModal();
}

// ─── SETTINGS MODAL ─────────────────────────
function showSettingsModal() {
    const s = loadSettings();
    DOM.settingsFontSize.value = s.font_size || 14;
    DOM.fontSizeValue.textContent = s.font_size || 14;
    DOM.settingsEnterSend.checked = s.send_with_enter !== false;
    DOM.settingsVoice.value = s.voice || "en-US-AvaMultilingualNeural";
    DOM.settingsModal.classList.add('show');
}
function hideSettingsModal() { DOM.settingsModal.classList.remove('show'); }

function saveSettings() {
    const s = {
        font_size: parseInt(DOM.settingsFontSize.value),
        send_with_enter: DOM.settingsEnterSend.checked,
        voice: DOM.settingsVoice.value,
    };
    saveSettingsToStorage(s);
    applySettings();
    showToast('Settings saved', 'success');
    hideSettingsModal();
}

function applySettings() {
    const s = loadSettings();
    document.documentElement.style.setProperty('--font-size-base', `${s.font_size || 14}px`);
}

// ─── HELPERS ─────────────────────────────────
function openSidebar() {
    DOM.sidebar.classList.add('open');
    DOM.sidebarOverlay.classList.add('open');
}
function closeSidebar() {
    DOM.sidebar.classList.remove('open');
    DOM.sidebarOverlay.classList.remove('open');
}

function scrollToBottom() { DOM.chatMessages.scrollTop = DOM.chatMessages.scrollHeight; }

function renderMarkdown(text) {
    if (!text) return '';
    marked.setOptions({
        breaks: true, gfm: true,
        highlight: (code, lang) => {
            if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
            return hljs.highlightAuto(code).value;
        },
    });
    return marked.parse(text);
}

function formatDate(d) {
    if (!d) return '';
    const diff = Date.now() - new Date(d).getTime();
    if (diff < 60000) return 'Just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)} min ago`;
    return new Date(d).toLocaleDateString('en-US', { day: 'numeric', month: 'short' });
}
function formatTime(d) {
    return new Date(d).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}
function escapeHtml(t) {
    const d = document.createElement('div');
    d.textContent = t;
    return d.innerHTML.replace(/\n/g, '<br>');
}
function copyCode(btn) {
    const code = btn.closest('pre').querySelector('code');
    navigator.clipboard.writeText(code.textContent).then(() => {
        btn.textContent = 'Copied!';
        setTimeout(() => btn.textContent = 'Copy', 2000);
    });
}
function setQuickPrompt(text) {
    DOM.messageInput.value = text;
    DOM.messageInput.focus();
    updateCharCount();
    autoResize();
}
function updateCharCount() { DOM.charCount.textContent = DOM.messageInput.value.length; }
function autoResize() {
    DOM.messageInput.style.height = 'auto';
    DOM.messageInput.style.height = Math.min(DOM.messageInput.scrollHeight, 200) + 'px';
}

function showToast(msg, type = 'success') {
    let c = document.querySelector('.toast-container');
    if (!c) { c = document.createElement('div'); c.className = 'toast-container'; document.body.appendChild(c); }
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    t.innerHTML = `<span>${type === 'success' ? '✅' : '❌'}</span><span>${msg}</span>`;
    c.appendChild(t);
    setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3000);
}

// ─── ACTION BUTTON HANDLERS ──────────────────────
function copyMessageText(btn) {
    const msgDiv = btn.closest('.message').querySelector('.message-body');
    let clone = msgDiv.cloneNode(true);
    clone.querySelectorAll('.thinking-box').forEach(b => b.remove());
    const text = clone.innerText.replace(/📋 Copy/g, '').trim();
    navigator.clipboard.writeText(text).then(() => {
        showToast('Message copied', 'success');
        btn.classList.add('active');
        setTimeout(() => btn.classList.remove('active'), 2000);
    });
}
function likeMessage(btn) {
    btn.classList.toggle('active');
    const dislikeBtn = btn.parentElement.querySelector('button[title="Dislike"]');
    if (dislikeBtn) dislikeBtn.classList.remove('active');
    if (btn.classList.contains('active')) showToast('Message liked', 'success');
}
function dislikeMessage(btn) {
    btn.classList.toggle('active');
    const likeBtn = btn.parentElement.querySelector('button[title="Like"]');
    if (likeBtn) likeBtn.classList.remove('active');
    if (btn.classList.contains('active')) showToast('Message disliked', 'success');
}
function shareMessage(btn) {
    const msgDiv = btn.closest('.message').querySelector('.message-body');
    let clone = msgDiv.cloneNode(true);
    clone.querySelectorAll('.thinking-box').forEach(b => b.remove());
    const text = clone.innerText.replace(/📋 Copy/g, '').trim();
    
    if (navigator.share) {
        navigator.share({
            title: 'GaziGPT Response',
            text: text
        }).catch(err => console.log('Share error:', err));
    } else {
        copyMessageText(btn);
        showToast('Sharing is not supported here. Message copied instead.', 'success');
    }
}
function regenerateMessage(btn) {
    if (!state.currentChatId || state.isLoading) return;
    const all = loadChatsFromStorage();
    const chat = all[state.currentChatId];
    if (!chat || chat.messages.length < 2) return;
    
    // Remove the latest assistant or partial error message.
    while (chat.messages.length > 0 && chat.messages[chat.messages.length - 1].role !== 'user') {
        chat.messages.pop();
    }
    saveChatsToStorage(all);
    showChatView(chat.messages);
    
    // Find the last user message.
    const lastUserMsg = chat.messages[chat.messages.length - 1];
    if (lastUserMsg) {
        // Reuse the current sendMessage flow for regeneration.
        DOM.messageInput.value = lastUserMsg.content;
        chat.messages.pop(); // Remove and re-add the user message through sendMessage.
        saveChatsToStorage(all);
        showChatView(chat.messages);
        sendMessage();
    }
}

let isPlayingTTS = false;
let currentAudio = null;
let currentTTSBtn = null;

const ttsPlayIcon = `<svg viewBox="0 0 24 24"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>`;
const ttsStopIcon = `<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12"></rect></svg>`;

function playTTSMessage(btn) {
    if (isPlayingTTS && currentAudio) {
        currentAudio.pause();
        currentAudio = null;
        isPlayingTTS = false;
        if (currentTTSBtn) {
            currentTTSBtn.classList.remove('active');
            currentTTSBtn.innerHTML = ttsPlayIcon;
            currentTTSBtn = null;
        }
        return;
    }
    
    // Stop any other message currently being read aloud.
    if (currentAudio) {
        currentAudio.pause();
        if (currentTTSBtn) {
            currentTTSBtn.classList.remove('active');
            currentTTSBtn.innerHTML = ttsPlayIcon;
        }
    }
    
    const msgDiv = btn.closest('.message').querySelector('.message-body');
    let clone = msgDiv.cloneNode(true);
    clone.querySelectorAll('.thinking-box').forEach(b => b.remove());
    const text = clone.innerText.replace(/📋 Copy/g, '').trim();
    if (!text) return;

    currentTTSBtn = btn;
    btn.classList.add('active');
    btn.innerHTML = ttsStopIcon;
    
    const s = loadSettings();
    const voice = s.voice || "en-US-AvaMultilingualNeural";
    const audioUrl = `/api/tts?text=${encodeURIComponent(text.substring(0, 5000))}&voice=${encodeURIComponent(voice)}`;
    
    currentAudio = document.getElementById('ttsAudio');
    currentAudio.src = audioUrl;
    currentAudio.play().then(() => {
        isPlayingTTS = true;
        currentAudio.onended = () => {
            isPlayingTTS = false;
            if (currentTTSBtn === btn) {
                btn.classList.remove('active');
                btn.innerHTML = ttsPlayIcon;
                currentTTSBtn = null;
            }
        };
    }).catch(err => {
        showToast('Could not play audio.', 'error');
        btn.classList.remove('active');
        btn.innerHTML = ttsPlayIcon;
        isPlayingTTS = false;
        currentTTSBtn = null;
    });
}

async function loadVoices() {
    try {
        const res = await fetch('/api/voices');
        const voices = await res.json();
        DOM.settingsVoice.innerHTML = '';
        voices.forEach(v => {
            const opt = document.createElement('option');
            opt.value = v.ShortName;
            opt.textContent = v.FriendlyName;
            DOM.settingsVoice.appendChild(opt);
        });
        const s = loadSettings();
        if (s.voice) DOM.settingsVoice.value = s.voice;
    } catch (e) {
        DOM.settingsVoice.innerHTML = '<option value="">Voices could not be loaded</option>';
    }
}

// ─── EVENT LISTENERS ─────────────────────────
function setupEventListeners() {
    DOM.newChatBtn.addEventListener('click', createChat);
    DOM.sendBtn.addEventListener('click', sendMessage);
    DOM.stopBtn.addEventListener('click', stopGeneration);
    document.addEventListener('click', (e) => {
        const button = e.target.closest?.('.project-folder-link');
        if (!button) return;
        e.preventDefault();
        openProjectFolder(button.dataset.path || '.', button);
    });
    DOM.autoAcceptToggle?.addEventListener('click', () => {
        state.autoAcceptEdits = !state.autoAcceptEdits;
        if (!state.autoAcceptEdits && state.autoPilot) {
            state.autoPilot = false;
            localStorage.setItem(AUTO_PILOT_KEY, 'off');
        }
        localStorage.setItem(APPROVAL_KEY, state.autoAcceptEdits ? 'auto' : 'manual');
        updatePlanControls();
    });
    DOM.autoPilotToggle?.addEventListener('click', () => {
        state.autoPilot = !state.autoPilot;
        localStorage.setItem(AUTO_PILOT_KEY, state.autoPilot ? 'on' : 'off');
        if (state.autoPilot) {
            state.autoAcceptEdits = true;
            localStorage.setItem(APPROVAL_KEY, 'auto');
            if (state.securityLevel === 'ask_each_step') {
                state.securityLevel = 'safe';
                localStorage.setItem(SECURITY_KEY, state.securityLevel);
            }
            showToast('Auto Pilot enabled: edits can be applied, safe commands can run, and auto-fix can recover validation errors.', 'success');
        } else {
            showToast('Auto Pilot disabled', 'success');
        }
        updatePlanControls();
    });
    DOM.securityLevel?.addEventListener('change', (e) => {
        state.securityLevel = e.target.value;
        localStorage.setItem(SECURITY_KEY, state.securityLevel);
        updatePlanControls();
        fetch('/api/permissions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                approval_mode: state.autoAcceptEdits ? 'auto' : 'manual',
                security_level: state.securityLevel,
                auto_authorize: state.autoPilot,
            }),
        }).catch(() => {});
    });
    DOM.modelEffort?.addEventListener('change', (e) => {
        state.modelEffort = e.target.value;
        localStorage.setItem(MODEL_EFFORT_KEY, state.modelEffort);
        updatePlanControls();
        showToast(`Effort: ${state.modelEffort}`, 'success');
    });

    DOM.messageInput.addEventListener('input', () => { updateCharCount(); autoResize(); });
    DOM.messageInput.addEventListener('keydown', (e) => {
        const s = loadSettings();
        if (e.key === 'Enter' && !e.shiftKey && s.send_with_enter !== false) {
            e.preventDefault();
            sendMessage();
        }
    });

    DOM.searchChats.addEventListener('input', (e) => renderChatList(e.target.value));
    DOM.toggleSidebar.addEventListener('click', () => openSidebar());
    DOM.closeSidebar.addEventListener('click', () => closeSidebar());
    DOM.sidebarOverlay.addEventListener('click', () => closeSidebar());

    // File
    DOM.attachBtn.addEventListener('click', () => DOM.fileInput.click());
    DOM.fileInput.addEventListener('change', handleFileSelect);
    DOM.filePreviewRemove.addEventListener('click', clearFileAttachment);

    // Clipboard paste
    DOM.messageInput.addEventListener('paste', (e) => {
        const items = e.clipboardData?.items;
        if (!items) return;
        for (const item of items) {
            if (item.type.startsWith('image/')) {
                e.preventDefault();
                const file = item.getAsFile();
                if (!file) return;
                const reader = new FileReader();
                reader.onload = (ev) => {
                    const base64Data = ev.target.result;
                    state.attachedFile = { name: 'pasted-image.png', content: base64Data, isImage: true };
                    DOM.filePreviewName.textContent = 'Pasted image';
                    DOM.filePreview.style.display = 'flex';
                    showToast('Image pasted', 'success');
                };
                reader.readAsDataURL(file);
                break;
            }
        }
    });

    // Delete modal
    DOM.confirmDelete.addEventListener('click', confirmDeleteChat);
    DOM.cancelDelete.addEventListener('click', hideDeleteModal);
    DOM.deleteModal.addEventListener('click', (e) => { if (e.target === DOM.deleteModal) hideDeleteModal(); });

    // Settings
    DOM.settingsBtn.addEventListener('click', showSettingsModal);
    DOM.closeSettings.addEventListener('click', hideSettingsModal);
    DOM.settingsModal.addEventListener('click', (e) => { if (e.target === DOM.settingsModal) hideSettingsModal(); });
    DOM.saveSettingsBtn.addEventListener('click', saveSettings);
    DOM.settingsFontSize.addEventListener('input', (e) => { DOM.fontSizeValue.textContent = e.target.value; });
    DOM.clearAllChatsBtn.addEventListener('click', () => {
        if (confirm('All chats will be deleted. Are you sure?')) clearAllChats();
    });

    // Shortcuts
    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.key === 'n') { e.preventDefault(); createChat(); }
        if (e.key === 'Escape') { hideDeleteModal(); hideSettingsModal(); closeSidebar(); }
    });
}

// --- MODEL SELECTOR (GCode v2) ---
function setupModelSelector() {
    const btn = document.getElementById('modelSelectorBtn');
    const dropdown = document.getElementById('modelDropdown');
    const options = document.querySelectorAll('.model-option');
    const currentName = document.getElementById('currentModelName');
    if(!btn || !dropdown) return;
    btn.addEventListener('click', (e) => { e.stopPropagation(); dropdown.classList.toggle('show'); });
    document.addEventListener('click', (e) => { if (!dropdown.contains(e.target) && !btn.contains(e.target)) dropdown.classList.remove('show'); });
    options.forEach(opt => {
        opt.addEventListener('click', () => {
            options.forEach(o => { o.classList.remove('active'); o.querySelector('.model-check').innerHTML = ''; });
            opt.classList.add('active');
            opt.querySelector('.model-check').innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2" fill="none"><polyline points="20 6 9 17 4 12"></polyline></svg>';
            state.selectedModel = opt.dataset.model;
            currentName.textContent = opt.dataset.model;
            dropdown.classList.remove('show');
            updateTierUI(opt.dataset.model);
            showToast(opt.dataset.model + ' selected', 'success');
        });
    });
    var ts = document.getElementById('thinkingLevel');
    var tv = document.getElementById('thinkingLevelValue');
    if (ts && tv) ts.addEventListener('input', (e) => { tv.textContent = e.target.value; });
    updateTierUI(state.selectedModel);
    fetchTierStatus();
}
document.addEventListener('DOMContentLoaded', setupModelSelector);



// ─── IMAGE LIGHTBOX (Fullscreen Modal) ────────
function openImageLightbox(src) {
    let overlay = document.getElementById('imageLightboxOverlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'imageLightboxOverlay';
        overlay.style.cssText = `
            position:fixed; top:0; left:0; width:100vw; height:100vh;
            background:rgba(0,0,0,0.92); z-index:99999;
            display:flex; align-items:center; justify-content:center;
            cursor:zoom-out; opacity:0; transition:opacity 0.25s ease;
            backdrop-filter:blur(8px);
        `;
        overlay.innerHTML = `
            <button id="lightboxClose" style="
                position:absolute; top:20px; right:24px;
                background:rgba(255,255,255,0.12); border:none;
                color:#fff; font-size:28px; width:44px; height:44px;
                border-radius:50%; cursor:pointer; display:flex;
                align-items:center; justify-content:center;
                backdrop-filter:blur(4px); transition:background 0.2s;
            " onmouseover="this.style.background='rgba(255,255,255,0.25)'"
               onmouseout="this.style.background='rgba(255,255,255,0.12)'"
            >✕</button>
            <img id="lightboxImg" style="
                max-width:92vw; max-height:90vh;
                border-radius:12px; object-fit:contain;
                box-shadow:0 20px 60px rgba(0,0,0,0.6);
                transition:transform 0.3s ease;
            " alt="Image">
            <div style="
                position:absolute; bottom:24px; display:flex; gap:12px;
            ">
                <button onclick="downloadImage(document.getElementById('lightboxImg').src, 'image.png')" style="
                    background:rgba(255,255,255,0.12); border:1px solid rgba(255,255,255,0.15);
                    color:#fff; padding:10px 20px; border-radius:10px;
                    cursor:pointer; font-size:14px; backdrop-filter:blur(4px);
                    transition:background 0.2s;
                " onmouseover="this.style.background='rgba(255,255,255,0.25)'"
                   onmouseout="this.style.background='rgba(255,255,255,0.12)'"
                >Download</button>
            </div>
        `;
        document.body.appendChild(overlay);
        
        // Close interactions
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target.id === 'lightboxClose') {
                closeLightbox();
            }
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && overlay.style.display === 'flex') {
                closeLightbox();
            }
        });
    }
    
    const img = document.getElementById('lightboxImg');
    img.src = src;
    overlay.style.display = 'flex';
    requestAnimationFrame(() => { overlay.style.opacity = '1'; });
}

function closeLightbox() {
    const overlay = document.getElementById('imageLightboxOverlay');
    if (overlay) {
        overlay.style.opacity = '0';
        setTimeout(() => {
            overlay.style.display = 'none';
        }, 250);
    }
}

async function downloadImage(src, filename = 'image.png') {
    try {
        const response = await fetch(src);
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        showToast('Download started', 'success');
    } catch (err) {
        window.open(src, '_blank');
    }
}

// ─── GCODE v2: TIER SYSTEM ───────────────────
function updateTierUI(m) {
    var map = {'GaziGPT':'core','GaziGPT Thinking':'core','GaziGPT Extended':'extended','GaziGPT Hyper':'hyper'};
    var t = map[m] || 'core';
    document.body.setAttribute('data-tier', t);
    var tl = document.getElementById('tierLabel');
    if (tl) tl.textContent = {'core':'Core','extended':'Extended','hyper':'Hyper'}[t] || 'v2';
    var tc = document.getElementById('thinkingLevelControl');
    if (tc) tc.style.display = (m==='GaziGPT'||m==='GaziGPT Thinking') ? 'flex' : 'none';
}
async function fetchTierStatus() {
    try { var r = await fetch('/api/tier/status'); var d = await r.json(); if(d.active){var dot=document.getElementById('tierDot');if(dot)dot.style.background='var(--tier-accent)';} } catch(e){}
}

// ─── RECOVERY: Re-attach missing event listeners ───
document.addEventListener('DOMContentLoaded', () => {
    var mi = document.getElementById('messageInput');
    if (mi && !mi._v2patched) {
        mi._v2patched = true;
        mi.addEventListener('input', () => { updateCharCount(); autoResize(); });
        mi.addEventListener('keydown', (e) => {
            const s = loadSettings();
            if (e.key === 'Enter' && !e.shiftKey && s.send_with_enter !== false) { e.preventDefault(); sendMessage(); }
        });
    }
    ['searchChats','toggleSidebar','closeSidebar','sidebarOverlay','attachBtn','fileInput',
     'filePreviewRemove','confirmDelete','cancelDelete','settingsBtn','closeSettings',
     'saveSettingsBtn','settingsFontSize','clearAllChatsBtn'].forEach(id => {
        var el = document.getElementById(id);
        if (el && !el._v2patched) {
            el._v2patched = true;
            if (id === 'searchChats') el.addEventListener('input', (e) => renderChatList(e.target.value));
            if (id === 'toggleSidebar') el.addEventListener('click', () => openSidebar());
            if (id === 'closeSidebar') el.addEventListener('click', () => closeSidebar());
            if (id === 'sidebarOverlay') el.addEventListener('click', () => closeSidebar());
            if (id === 'attachBtn') el.addEventListener('click', () => document.getElementById('fileInput')?.click());
            if (id === 'fileInput') el.addEventListener('change', handleFileSelect);
            if (id === 'filePreviewRemove') el.addEventListener('click', clearFileAttachment);
            if (id === 'confirmDelete') el.addEventListener('click', confirmDeleteChat);
            if (id === 'cancelDelete') el.addEventListener('click', hideDeleteModal);
            if (id === 'settingsBtn') el.addEventListener('click', showSettingsModal);
            if (id === 'closeSettings') el.addEventListener('click', hideSettingsModal);
            if (id === 'saveSettingsBtn') el.addEventListener('click', saveSettings);
            if (id === 'settingsFontSize') el.addEventListener('input', (e) => { var v = document.getElementById('fontSizeValue'); if(v) v.textContent = e.target.value; });
            if (id === 'clearAllChatsBtn') el.addEventListener('click', () => { if (confirm('Clear all chats?')) clearAllChats(); });
        }
    });
    // Thinking slider
    var tsl = document.getElementById('thinkingLevel');
    var tval = document.getElementById('thinkingLevelValue');
    if (tsl && tval) tsl.addEventListener('input', (e) => { tval.textContent = e.target.value; });
    // Init tier
    updateTierUI(state.selectedModel);
    fetchTierStatus();
});

function getFileExtensionBadge(filename) {
    if (!filename) return '';
    const ext = filename.split('.').pop().toLowerCase();
    const colors = {
        'js': '#eab308',
        'ts': '#3178c6',
        'py': '#3b82f6',
        'html': '#e34f26',
        'css': '#2563eb',
        'json': '#16a34a',
        'md': '#6b7280'
    };
    const color = colors[ext] || '#8b5cf6';
    return `<strong style="color: ${color}; margin: 0 8px; font-weight: 700; font-family: 'Inter', sans-serif;">${ext.toUpperCase()}</strong>`;
}

function renderChatFileEditBlocks(files, autoApplied, groupId = "") {
    if (!files || !files.length) return '';
    return files.map((f, i) => {
        const badge = getFileExtensionBadge(f.path);
        const name = f.path.split('/').pop();
        const statusIcon = `<div class="status-icon-container" style="margin-left:12px; display:inline-flex; align-items:center; justify-content:center; width:14px; height:14px;"><div class="tool-spinner" style="width:14px; height:14px; border:2px solid #ccc; border-top-color:#888;"></div></div>`;
        
        return `
        <div id="${groupId}_row_${i}" class="inline-file-edit-block" style="display:inline-flex; align-items:center; background:#efece5; border-radius:6px; padding:6px 12px; font-family: 'Inter', sans-serif; font-size:13px; color:#555; border: 1px solid rgba(0,0,0,0.05); box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
            <span class="edit-status-text" style="color:#888;">Editing</span>
            ${badge}
            <strong style="color:#333; margin-right: 12px;">${escapeHtml(name)}</strong>
            <span class="inline-diff-stats" style="font-family: 'JetBrains Mono', monospace;">
                <span class="diff-add-wrap" style="display:none;color:#16a34a;">+<span class="diff-add-num">0</span></span>
                <span class="diff-rem-wrap" style="display:none;color:#dc2626;">-<span class="diff-rem-num">0</span></span>
            </span>
            ${statusIcon}
            <button style="background:none; border:none; cursor:pointer; font-size:16px; color:#999; margin-left:16px; padding:0; line-height:1; font-family: 'Inter', sans-serif;" onclick="this.parentElement.style.display='none'">×</button>
        </div>
        `;
    }).join('');
}

function animateChatFileEditBlocks(groupId, files, autoApplied) {
    if (!files || !files.length) return;
    files.forEach((f, i) => {
        const row = document.getElementById(groupId + '_row_' + i);
        if (!row) return;
        const addEl = row.querySelector('.diff-add-num');
        const remEl = row.querySelector('.diff-rem-num');
        const addWrap = row.querySelector('.diff-add-wrap');
        const remWrap = row.querySelector('.diff-rem-wrap');
        const statusText = row.querySelector('.edit-status-text');
        const iconContainer = row.querySelector('.status-icon-container');
        
        const targetAdd = cleanDiffNumber(f.added);
        const targetRem = cleanDiffNumber(f.removed);
        if (addWrap) addWrap.style.display = targetAdd > 0 ? 'inline' : 'none';
        if (remWrap) remWrap.style.display = targetRem > 0 ? 'inline' : 'none';
        
        const duration = 1200; // 1.2s animation
        const steps = 30;
        const stepTime = duration / steps;
        
        let currentStep = 0;
        const interval = setInterval(() => {
            currentStep++;
            const progress = currentStep / steps;
            
            if(addEl) addEl.textContent = Math.floor(targetAdd * progress);
            if(remEl) remEl.textContent = Math.floor(targetRem * progress);
            
            if (currentStep >= steps) {
                clearInterval(interval);
                if(addEl) addEl.textContent = targetAdd;
                if(remEl) remEl.textContent = targetRem;
                
                if (autoApplied) {
                    if(statusText) statusText.textContent = "Edited";
                    if(iconContainer) iconContainer.innerHTML = '<span style="color:#10b981; font-weight: bold; font-family: \'Inter\', sans-serif;">✓</span>';
                }
            }
        }, stepTime);
    });
}
