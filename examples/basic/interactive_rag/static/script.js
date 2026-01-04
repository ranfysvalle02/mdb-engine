// ---------------------------
// Global state and references
// ---------------------------
let currentSessionId = "default";
let allSessions = [];
let availableModels = [];
let chunkCache = new Map();

// Dom references - will be initialized after DOM loads
let chatBox, userInput, chatForm, sessionSelector, newSessionBtn, clearHistoryBtn;
let toolButtonsContainer, thinkingIndicator;
let embeddingModelSelector, numSourcesInput, minScoreInput, minScoreValue;
let maxCharsInput, maxCharsValue, previewRagBtn;
let modalOverlay, modalContainer, modalTitle, modalText, modalContentHost;
let modalCancelBtn, modalSubmitBtn;
let sourceBrowserOverlay, sourceBrowserContainer, sourceBrowserCloseBtn;
let sourceListEl, chunkListEl, chunkListPlaceholder;
let sourceBrowserTotalChunks, sourceBrowserSourceCount, sourceBrowserSelectedChunkCount;
let sessionSwitchOverlay, sessionSwitchTitle, sessionSwitchMessage, sessionSwitchName, appContainer;

// Initialize DOM references
function initDOMReferences() {
  chatBox = document.getElementById("chat-box");
  userInput = document.getElementById("user-input");
  chatForm = document.getElementById("chat-form");
  sessionSelector = document.getElementById("session-selector");
  newSessionBtn = document.getElementById("new-session-btn");
  clearHistoryBtn = document.getElementById("clear-history-btn");
  toolButtonsContainer = document.getElementById("tool-buttons");
  thinkingIndicator = document.getElementById("thinking-indicator");
  embeddingModelSelector = document.getElementById("embedding-model-selector");
  numSourcesInput = document.getElementById("num-sources-input");
  minScoreInput = document.getElementById("min-score-input");
  minScoreValue = document.getElementById("min-score-value");
  maxCharsInput = document.getElementById("max-chunk-length-input");
  maxCharsValue = document.getElementById("max-chars-value");
  previewRagBtn = document.getElementById("preview-rag-btn");
  modalOverlay = document.getElementById("modal-overlay");
  modalContainer = document.getElementById("modal-container");
  modalTitle = document.getElementById("modal-title");
  modalText = document.getElementById("modal-text");
  modalContentHost = document.getElementById("modal-content-host");
  modalCancelBtn = document.getElementById("modal-btn-cancel");
  modalSubmitBtn = document.getElementById("modal-btn-submit");
  sourceBrowserOverlay = document.getElementById("source-browser-overlay");
  sourceBrowserContainer = document.getElementById("source-browser-container");
  sourceBrowserCloseBtn = document.getElementById("source-browser-close-btn");
  sourceListEl = document.getElementById("source-list");
  chunkListEl = document.getElementById("chunk-list");
  chunkListPlaceholder = document.getElementById("chunk-list-placeholder");
  sourceBrowserTotalChunks = document.getElementById("source-browser-total-chunks");
  sourceBrowserSourceCount = document.getElementById("source-browser-source-count");
  sourceBrowserSelectedChunkCount = document.getElementById("source-browser-selected-chunk-count");
  sessionSwitchOverlay = document.getElementById("session-switch-overlay");
  sessionSwitchTitle = document.getElementById("session-switch-title");
  sessionSwitchMessage = document.getElementById("session-switch-message");
  sessionSwitchName = document.getElementById("session-switch-name");
  appContainer = document.getElementById("app-container");
}

// -----------
// Modal Logic
// -----------
function showModal({ title, text, contentHTML, onSubmit, onCancel, submitText, cancelText, hideCancel }) {

 if (!modalTitle || !modalText || !modalContentHost || !modalOverlay || !modalContainer) {
  console.error("[showModal] Modal elements not found!", {
    modalTitle: !!modalTitle,
    modalText: !!modalText,
    modalContentHost: !!modalContentHost,
    modalOverlay: !!modalOverlay,
    modalContainer: !!modalContainer
  });
  return;
 }

 // Handle HTML in title
 if (title && title.includes('<')) {
  modalTitle.innerHTML = title;
 } else {
 modalTitle.textContent = title || "Modal Title";
 }

 // Handle HTML in text
 if (text && text.includes('<')) {
  modalText.innerHTML = text;
 } else {
 modalText.textContent = text || "";
 }

 modalContentHost.innerHTML = contentHTML || "";

 // Handle submit button FIRST (before cancel to ensure proper state)
 if (modalSubmitBtn) {
  // Update button text FIRST
  if (submitText !== undefined) {
   modalSubmitBtn.textContent = submitText;
  } else {
   modalSubmitBtn.textContent = "Submit";
  }

  if (onSubmit) {
  modalSubmitBtn.onclick = () => {
   onSubmit();
  };
  } else {
  modalSubmitBtn.onclick = () => {
   hideModal();
  };
  }
  modalSubmitBtn.style.display = "block";
 }

 // Handle cancel button
 if (modalCancelBtn) {
  if (hideCancel === true) {
   modalCancelBtn.style.display = "none";
   modalCancelBtn.style.visibility = "hidden";
  } else {
   modalCancelBtn.style.display = "block";
   modalCancelBtn.style.visibility = "visible";
   if (onCancel) {
  modalCancelBtn.onclick = () => {
   onCancel();
  };
   } else {
  modalCancelBtn.onclick = () => {
   hideModal();
  };
   }
   // Update button text
   if (cancelText !== undefined) {
    modalCancelBtn.textContent = cancelText;
   } else {
    modalCancelBtn.textContent = "Cancel";
   }
  }
 }

 // Show modal with animation
 const overlayClassesBefore = modalOverlay.className;
 const containerClassesBefore = modalContainer.className;

 // FORCE SHOW - Remove hiding classes FIRST
 modalOverlay.classList.remove("invisible", "opacity-0");
 modalContainer.classList.remove("scale-95", "opacity-0");

 // Then set inline styles with !important to override everything
 modalOverlay.style.setProperty("display", "flex", "important");
 modalOverlay.style.setProperty("visibility", "visible", "important");
 modalOverlay.style.setProperty("opacity", "1", "important");
 modalOverlay.style.setProperty("z-index", "9999", "important");

 // Force container to be visible too
 modalContainer.style.setProperty("opacity", "1", "important");
 modalContainer.style.setProperty("transform", "scale(1)", "important");

 // Force a reflow to ensure styles are applied
 void modalOverlay.offsetWidth;
 void modalContainer.offsetWidth;

}

function hideModal(immediate = false) {

 if (!modalOverlay || !modalContainer || !modalTitle || !modalText || !modalContentHost) {
  console.warn("[hideModal] Modal elements not found");
  return;
 }

 if (immediate) {
  // FORCE HIDE IMMEDIATELY - no animation
  modalOverlay.style.display = "none";
  modalOverlay.style.visibility = "hidden";
  modalOverlay.style.opacity = "0";
  modalOverlay.style.zIndex = "";
  modalOverlay.classList.add("invisible", "opacity-0");

  // Reset button states
  if (modalCancelBtn) {
    modalCancelBtn.style.display = "block";
    modalCancelBtn.style.visibility = "visible";
    modalCancelBtn.textContent = "Cancel";
  }
  if (modalSubmitBtn) {
    modalSubmitBtn.style.display = "block";
    modalSubmitBtn.style.visibility = "visible";
    modalSubmitBtn.textContent = "Submit";
  }

  // Clear content immediately
  modalTitle.textContent = "";
  modalText.textContent = "";
  modalContentHost.innerHTML = "";
  if (modalSubmitBtn) modalSubmitBtn.onclick = null;
  if (modalCancelBtn) modalCancelBtn.onclick = null;

  // Restore overlay click handler
  if (modalOverlay) {
    modalOverlay.onclick = (e) => {
      if (e.target === modalOverlay) {
        hideModal();
      }
    };
  }

  return;
 }

 // Normal hide with animation
 // Reset button states
 if (modalCancelBtn) {
  modalCancelBtn.style.display = "block";
  modalCancelBtn.style.visibility = "visible";
  modalCancelBtn.textContent = "Cancel";
 }
 if (modalSubmitBtn) {
  modalSubmitBtn.style.display = "block";
  modalSubmitBtn.style.visibility = "visible";
  modalSubmitBtn.textContent = "Submit";
 }

 // Restore overlay click handler
 if (modalOverlay) {
  modalOverlay.onclick = (e) => {
    if (e.target === modalOverlay) {
      hideModal();
    }
  };
 }

 // Remove inline styles that override classes (so classes can take effect)
 modalOverlay.style.opacity = "";
 modalOverlay.style.visibility = "";

 // Add classes for fade out animation immediately
 modalOverlay.classList.add("opacity-0", "invisible");
 modalContainer.classList.add("scale-95", "opacity-0");

 // Wait for animation to complete before clearing content and resetting remaining styles
 setTimeout(() => {
  // Reset remaining inline styles
  modalOverlay.style.zIndex = "";
  modalOverlay.style.display = "";

  modalTitle.textContent = "";
  modalText.textContent = "";
  modalContentHost.innerHTML = "";
  if (modalSubmitBtn) modalSubmitBtn.onclick = null;
  if (modalCancelBtn) modalCancelBtn.onclick = null;

 }, 300); // Match transition duration
}

// ------------------------
// Source Browser Functions
// ------------------------
function openSourceBrowser() {
 if (!sourceBrowserOverlay || !sourceBrowserContainer) {
  console.error("Source browser elements not found");
  return;
 }

 sourceBrowserOverlay.classList.remove("opacity-0", "invisible");
 sourceBrowserContainer.classList.remove("scale-95", "opacity-0");
 if (sourceBrowserTotalChunks) sourceBrowserTotalChunks.textContent = "";
 if (sourceBrowserSourceCount) sourceBrowserSourceCount.textContent = "Total: 0";
 if (sourceBrowserSelectedChunkCount) sourceBrowserSelectedChunkCount.textContent = "Selected: 0";

 fetch(`/sources?session_id=${encodeURIComponent(currentSessionId)}`)
  .then((r) => r.json())
  .then((data) => {
   sourceListEl.innerHTML = "";
   chunkListEl.innerHTML = "";
   chunkListPlaceholder.style.display = "block";

   if (!data || data.length === 0) {
    sourceListEl.innerHTML = "<p class='text-gray-400 text-sm p-2'>No sources found.</p>";
    return;
   }

   sourceBrowserSourceCount.textContent = `Total: ${data.length}`;
   let totalChunks = 0;
   data.forEach(src => totalChunks += (src.chunk_count || 0));
   sourceBrowserTotalChunks.textContent = `(${totalChunks.toLocaleString()} Total Chunks)`;

   data.forEach((src) => {
    const btn = document.createElement("button");
    btn.className = "source-item";

    const chunkCount = src.chunk_count !== undefined ? `${src.chunk_count}` : '?';
    const sourceName = src.name + (src.type ? ` (${src.type})` : "");

    btn.innerHTML = `
     <span class="truncate pr-2" title="${escapeHtml(sourceName)}">${escapeHtml(sourceName)}</span>
     <span class="sb-count-badge flex-shrink-0">${chunkCount}</span>
    `;

    btn.onclick = () => {
     document.querySelectorAll('.source-item').forEach(b => b.classList.remove('active'));
     btn.classList.add('active');
     loadChunksForSource(src.name);
    };
    sourceListEl.appendChild(btn);
   });
  })
  .catch((err) => {
   console.error("Failed to list sources:", err);
   sourceListEl.innerHTML = `<p class='text-red-500 p-2'>Error: ${err.message}</p>`;
  });
}

function loadChunksForSource(sourceUrl) {
  if (!sourceBrowserSelectedChunkCount || !chunkListEl) return;

  sourceBrowserSelectedChunkCount.textContent = "Loading...";
  fetch(`/chunks?session_id=${encodeURIComponent(currentSessionId)}&source_url=` + encodeURIComponent(sourceUrl))
   .then((r) => r.json())
   .then((data) => {
    chunkListEl.innerHTML = "";
    chunkCache.clear();

    if (data.error) {
     chunkListEl.innerHTML = `<p class='text-red-500'>Error: ${data.error}</p>`;
     sourceBrowserSelectedChunkCount.textContent = "Error";
     return;
    }
    if (!data || data.length === 0) {
     chunkListEl.innerHTML = "<p class='text-gray-400 text-sm'>No chunks found for this source.</p>";
     sourceBrowserSelectedChunkCount.textContent = "Selected: 0";
     return;
    }

    sourceBrowserSelectedChunkCount.textContent = `Selected: ${data.length}`;

    chunkListPlaceholder.style.display = "none";
    data.forEach((ch) => {
     chunkCache.set(ch._id, ch);
     const card = document.createElement("div");
     card.className = "chunk-card";
     card.setAttribute("data-chunk-id", ch._id);
     card.innerHTML = `
       <div class="chunk-header">
         <div class="chunk-title">Chunk ID: ${ch._id}</div>
         <div class="chunk-actions flex gap-2">
           <button data-id="${ch._id}" class="chunk-edit-btn text-xs bg-blue-500 hover:bg-blue-600 px-2 py-1 rounded">Edit</button>
           <button data-id="${ch._id}" class="chunk-delete-btn text-xs bg-red-600 hover:bg-red-700 px-2 py-1 rounded">Delete</button>
         </div>
       </div>
       <div class="chunk-content prose prose-invert max-w-none prose-sm">${marked.parse(ch.text || "")}</div>
     `;
     chunkListEl.appendChild(card);
    });
   })
   .catch((err) => {
    console.error("Failed to load chunks:", err);
    chunkListEl.innerHTML = `<p class='text-red-500'>Error: ${err.message}</p>`;
    sourceBrowserSelectedChunkCount.textContent = "Error";
   });
}

// Event listener for chunk buttons - will be attached after DOM ready
function attachChunkListListeners() {
  if (!chunkListEl) return;

  chunkListEl.addEventListener('click', (event) => {
    const editButton = event.target.closest('.chunk-edit-btn');
    if (editButton) {
        const chunkId = editButton.getAttribute('data-id');
        startChunkEdit(chunkId);
        return;
    }

    const deleteButton = event.target.closest('.chunk-delete-btn');
    if (deleteButton) {
        const chunkId = deleteButton.getAttribute('data-id');
        onDeleteChunkClick(chunkId);
        return;
    }

    const saveButton = event.target.closest('.chunk-save-btn');
    if (saveButton) {
        const chunkId = saveButton.getAttribute('data-id');
        saveChunkEdit(chunkId);
        return;
    }

    const cancelButton = event.target.closest('.chunk-cancel-btn');
    if (cancelButton) {
        const chunkId = cancelButton.getAttribute('data-id');
        cancelChunkEdit(chunkId);
        return;
    }
  });
}

function attachSourceBrowserListeners() {
  if (sourceBrowserCloseBtn) {
    sourceBrowserCloseBtn.addEventListener("click", () => {
     closeSourceBrowser();
    });
  }
}

function closeSourceBrowser() {
 if (!sourceBrowserOverlay || !sourceBrowserContainer) return;

 sourceBrowserOverlay.classList.add("opacity-0", "invisible");
 sourceBrowserContainer.classList.add("scale-95", "opacity-0");
 if (sourceListEl) sourceListEl.innerHTML = "";
 if (chunkListEl) chunkListEl.innerHTML = "";
 if (chunkListPlaceholder) chunkListPlaceholder.style.display = "block";
 if (sourceBrowserTotalChunks) sourceBrowserTotalChunks.textContent = "";
 if (sourceBrowserSourceCount) sourceBrowserSourceCount.textContent = "Total: 0";
 if (sourceBrowserSelectedChunkCount) sourceBrowserSelectedChunkCount.textContent = "Selected: 0";
}

// -------------------------------------------------
// --- IN-PLACE CHUNK EDITING LOGIC ---
// -------------------------------------------------

function startChunkEdit(chunkId) {
    if (!chunkListEl) return;

    const chunkCard = chunkListEl.querySelector(`.chunk-card[data-chunk-id='${chunkId}']`);
    if (!chunkCard || chunkCard.classList.contains('is-editing')) return;

    const chunkData = chunkCache.get(chunkId);
    if (!chunkData) {
        alert("Error: Could not find chunk data to edit.");
        return;
    }

    chunkCard.classList.add('is-editing');
    const contentHost = chunkCard.querySelector('.chunk-content');
    const actionsHost = chunkCard.querySelector('.chunk-actions');

    chunkCard.dataset.originalContent = contentHost.innerHTML;
    chunkCard.dataset.originalActions = actionsHost.innerHTML;

    contentHost.innerHTML = `
        <textarea class="chunk-edit-textarea w-full bg-gray-900 text-gray-200 p-2 rounded font-mono text-sm resize-y focus:outline-none focus:ring-2 focus:ring-mongodb-green-500">${escapeHtmlForTextarea(chunkData.text)}</textarea>
    `;
    actionsHost.innerHTML = `
        <button data-id="${chunkId}" class="chunk-cancel-btn text-xs bg-gray-600 hover:bg-gray-700 px-3 py-1 rounded">Cancel</button>
        <button data-id="${chunkId}" class="chunk-save-btn text-xs bg-green-600 hover:bg-green-700 px-3 py-1 rounded">Save</button>
    `;

    const textarea = contentHost.querySelector('textarea');
    const autoResize = () => {
        textarea.style.height = 'auto';
        textarea.style.height = (textarea.scrollHeight) + 'px';
    };
    textarea.addEventListener('input', autoResize);
    autoResize();
    textarea.focus();
}

function cancelChunkEdit(chunkId) {
    if (!chunkListEl) return;

    const chunkCard = chunkListEl.querySelector(`.chunk-card[data-chunk-id='${chunkId}']`);
    if (!chunkCard || !chunkCard.classList.contains('is-editing')) return;

    const contentHost = chunkCard.querySelector('.chunk-content');
    const actionsHost = chunkCard.querySelector('.chunk-actions');

    contentHost.innerHTML = chunkCard.dataset.originalContent;
    actionsHost.innerHTML = chunkCard.dataset.originalActions;

    chunkCard.classList.remove('is-editing');
    delete chunkCard.dataset.originalContent;
    delete chunkCard.dataset.originalActions;
}

function saveChunkEdit(chunkId) {
    if (!chunkListEl) return;

    const chunkCard = chunkListEl.querySelector(`.chunk-card[data-chunk-id='${chunkId}']`);
    if (!chunkCard) return;

    const textarea = chunkCard.querySelector('.chunk-edit-textarea');
    const newText = textarea.value;
    const saveBtn = chunkCard.querySelector('.chunk-save-btn');
    saveBtn.textContent = 'Saving...';
    saveBtn.disabled = true;

    const formData = new FormData();
    formData.append('content', newText);

    fetch("/chunk/" + encodeURIComponent(chunkId), {
        method: "PUT",
        body: formData,
    })
    .then(r => r.json())
    .then(resp => {
        if (resp.error) {
            alert("Error updating chunk: " + resp.error);
            saveBtn.textContent = 'Save';
            saveBtn.disabled = false;
            return;
        }
        const chunkData = chunkCache.get(chunkId);
        chunkData.text = newText;
        chunkCache.set(chunkId, chunkData);

        const contentHost = chunkCard.querySelector('.chunk-content');
        const actionsHost = chunkCard.querySelector('.chunk-actions');

        contentHost.innerHTML = marked.parse(newText);
        actionsHost.innerHTML = chunkCard.dataset.originalActions;

        chunkCard.classList.remove('is-editing');
        delete chunkCard.dataset.originalContent;
        delete chunkCard.dataset.originalActions;
    })
    .catch(err => {
        alert("Error updating chunk: " + err.message);
        saveBtn.textContent = 'Save';
        saveBtn.disabled = false;
    });
}

function onDeleteChunkClick(chunkId) {
 if (!confirm("Are you sure you want to delete this chunk?")) return;
 fetch(`/chunk/${encodeURIComponent(chunkId)}`, { method: "DELETE" })
  .then((r) => r.json())
  .then((resp) => {
   if (resp.error) {
    alert("Error deleting chunk: " + resp.error);
    return;
   }
   if (chunkListEl) {
     const chunkCard = chunkListEl.querySelector(`.chunk-card[data-chunk-id='${chunkId}']`);
     if (chunkCard) {
      chunkCard.remove();
      chunkCache.delete(chunkId);
     }
   }
  })
  .catch((err) => {
   alert("Error deleting chunk: " + err.message);
  });
}

// --------
// Helpers
// --------
function escapeHtml(unsafe) {
 if (!unsafe) return "";
 return unsafe.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function escapeHtmlForTextarea(str) {
 return str.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

// ---------------------------------------------
// Chat Rendering (add messages to the chat box)
// ---------------------------------------------
let messageQueryMap = new Map();

function addBotMessage(message) {
 const content = message.content;
 const sources = message.sources || [];
 const query = message.query || null;
 const chunks = message.chunks || [];


 const messageEl = document.createElement("div");
 messageEl.className = "message bot-message flex flex-col p-4 bg-gray-700 rounded-lg animate-fade-in-up";

 const contentDiv = document.createElement("div");
 contentDiv.className = "prose prose-invert max-w-none";

 if (content.trim().startsWith('<div')) {
  contentDiv.innerHTML = content;
 } else {
  contentDiv.innerHTML = marked.parse(content || "");
 }
 messageEl.appendChild(contentDiv);

 // Always create messageId if we have query, sources, or chunks (for chunk inspection)
  let messageId = null;
 if (query || sources.length > 0 || (chunks && chunks.length > 0)) {
   messageId = `msg-${Date.now()}-${Math.random()}`;
   messageEl.setAttribute('data-message-id', messageId);
  if (query) {
   messageQueryMap.set(messageId, query);
  }

  // Always store chunks map
  window.messageChunksMap = window.messageChunksMap || new Map();
  if (chunks && chunks.length > 0) {
   window.messageChunksMap.set(messageId, chunks);
  } else {
   console.warn('[addBotMessage] ⚠️ No chunks to store for message:', messageId);
   window.messageChunksMap.set(messageId, []);
  }
 }

 // Show sources/chunks section if we have sources or chunks
 if (sources.length > 0 || (chunks && chunks.length > 0)) {
  let sourceLinksHTML = sources.map(source => {
   const href = `/source_content?session_id=${encodeURIComponent(currentSessionId)}&source=${encodeURIComponent(source)}`;
   const target = `target="_blank" rel="noopener noreferrer"`;

   let displayName = source;
   try {
    if (source.startsWith('http')) displayName = new URL(source).hostname;
   } catch (e) { }

   return `
    <a href="${href}" ${target} title="View full source: ${escapeHtml(source)}">
     <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" class="w-4 h-4">
      <path d="M8.75 3.75a.75.75 0 0 0-1.5 0v1.5h-1.5a.75.75 0 0 0 0 1.5h1.5v1.5a.75.75 0 0 0 1.5 0v-1.5h1.5a.75.75 0 0 0 0-1.5h-1.5v-1.5Z" />
      <path fill-rule="evenodd" d="M3 1.75C3 .784 3.784 0 4.75 0h6.5C12.216 0 13 .784 13 1.75v12.5A1.75 1.75 0 0 1 11.25 16h-6.5A1.75 1.75 0 0 1 3 14.25V1.75Zm1.75-.25a.25.25 0 0 0-.25.25v12.5c0 .138.112.25.25.25h6.5a.25.25 0 0 0 .25-.25V1.75a.25.25 0 0 0-.25-.25h-6.5Z" clip-rule="evenodd" />
     </svg>
     <span>${escapeHtml(displayName)}</span>
    </a>
   `;
  }).join('');

  const sourcesContainer = document.createElement("div");
  sourcesContainer.className = "source-links mt-4 pt-4 border-t border-gray-600";

  // Check if we have chunks for this message
  const hasChunksForDisplay = messageId && window.messageChunksMap && window.messageChunksMap.get(messageId) && window.messageChunksMap.get(messageId).length > 0;
  const chunkCount = hasChunksForDisplay ? window.messageChunksMap.get(messageId).length : 0;

  // Create prominent inspect button that shows chunk count
  const inspectButton = messageId ? `
    <button onclick="inspectRetrievedChunks('${messageId}')"
            class="flex items-center gap-2 text-xs ${hasChunksForDisplay ? 'bg-mongodb-green-500/20 hover:bg-mongodb-green-500/30 text-mongodb-green-400 shadow-sm shadow-mongodb-green-500/20 border border-mongodb-green-500/30' : 'bg-gray-600/20 hover:bg-gray-600/30 text-gray-400 border border-gray-600/30'} px-3 py-2 rounded-lg transition-all duration-200 hover:scale-105 font-medium">
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-4 h-4">
        <path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639l4.43-7.29a1.125 1.125 0 011.906 0l4.43 7.29c.356.586.356 1.35 0 1.936l-4.43 7.29a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" />
      </svg>
      <span>${hasChunksForDisplay ? `View Context (${chunkCount})` : 'Inspect Chunks'}</span>
      ${hasChunksForDisplay ? '<span class="ml-0.5 w-2 h-2 bg-mongodb-green-400 rounded-full animate-pulse"></span>' : ''}
    </button>
  ` : '';

  sourcesContainer.innerHTML = `
    <div class="flex justify-between items-center mb-3">
      <div class="flex items-center gap-3">
      <h4 class="text-xs font-bold uppercase text-gray-400">Sources</h4>
        ${hasChunksForDisplay ? `
          <span class="text-xs text-mongodb-green-400 flex items-center gap-1.5 px-2.5 py-1 bg-mongodb-green-500/10 rounded-md border border-mongodb-green-500/20 font-medium">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor" class="w-3.5 h-3.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            ${chunkCount} context chunk${chunkCount !== 1 ? 's' : ''} used
          </span>
        ` : ''}
      </div>
      ${inspectButton}
    </div>
    <div class="flex flex-wrap gap-2">
      ${sourceLinksHTML}
    </div>
  `;
  messageEl.appendChild(sourcesContainer);
 }

 chatBox.appendChild(messageEl);
 chatBox.scrollTop = chatBox.scrollHeight;
}

function addUserMessage(content) {
 if (!chatBox) return;

 const messageEl = document.createElement("div");
 messageEl.className = "message user-message bg-gray-600 p-3 rounded-lg animate-fade-in-up text-right";
 messageEl.textContent = content;
 chatBox.appendChild(messageEl);
 chatBox.scrollTop = chatBox.scrollHeight;
}

function addSystemMessage(content) {
 if (!chatBox) return;

 const div = document.createElement("div");
 div.className = "message system-message bg-yellow-900/50 text-yellow-300 border-l-4 border-yellow-500 p-3 rounded-r-lg animate-fade-in-up";
 div.innerHTML = `<strong>System:</strong> ${content}`;
 chatBox.appendChild(div);
 chatBox.scrollTop = chatBox.scrollHeight;
}

function setThinking(isThinking) {
 if (!thinkingIndicator || !chatBox) return;

 if (isThinking) {
  thinkingIndicator.classList.remove("invisible", "opacity-0");
  chatBox.scrollTop = chatBox.scrollHeight;
 } else {
  thinkingIndicator.classList.add("invisible", "opacity-0");
 }
}

// -------------------------
// Session Switching UI Control
// -------------------------
function showSessionSwitchModal(sessionName, isCreating = false) {

  // Check if modal elements exist
  if (!modalTitle || !modalText || !modalContentHost || !modalOverlay || !modalContainer) {
    console.error("[showSessionSwitchModal] Modal elements not found!", {
      modalTitle: !!modalTitle,
      modalText: !!modalText,
      modalContentHost: !!modalContentHost,
      modalOverlay: !!modalOverlay,
      modalContainer: !!modalContainer
    });
    return;
  }

  const title = isCreating ? "Creating Session" : "Establishing Session";
  const message = isCreating
    ? `Please wait while we set up your new session: <span class="text-mongodb-green-400 font-semibold">${escapeHtml(sessionName)}</span>`
    : `Please wait while we establish your session: <span class="text-mongodb-green-400 font-semibold">${escapeHtml(sessionName)}</span>`;

  const loadingHTML = `
    <div class="flex flex-col items-center justify-center py-8">
      <div class="mb-6">
        <svg class="w-16 h-16 text-mongodb-green-500 animate-spin mx-auto" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
      </div>
      <div class="flex justify-center gap-1.5 mt-4">
        <div class="w-2 h-2 bg-mongodb-green-500 rounded-full animate-bounce" style="animation-delay: 0s"></div>
        <div class="w-2 h-2 bg-mongodb-green-500 rounded-full animate-bounce" style="animation-delay: 0.2s"></div>
        <div class="w-2 h-2 bg-mongodb-green-500 rounded-full animate-bounce" style="animation-delay: 0.4s"></div>
      </div>
    </div>
  `;


  showModal({
    title: title,
    text: message,
    contentHTML: loadingHTML,
    hideCancel: true,
    submitText: "Close",
    onSubmit: null // No action on submit for loading modal
  });

  // Hide submit button completely for loading modal (user can't close it)
  if (modalSubmitBtn) {
    modalSubmitBtn.style.display = "none";
    modalSubmitBtn.style.visibility = "hidden";
  } else {
    console.warn("[showSessionSwitchModal] modalSubmitBtn not found!");
  }

  // Mark modal as non-dismissible during session switch
  if (modalOverlay) {
    modalOverlay.setAttribute("data-session-switching", "true");
  } else {
    console.warn("[showSessionSwitchModal] modalOverlay not found!");
  }

  // Force a reflow to ensure modal is visible
  void modalOverlay.offsetWidth;
}

function showSessionSwitchOverlay(sessionName, isCreating = false) {
  if (!sessionSwitchOverlay || !sessionSwitchTitle || !sessionSwitchName || !appContainer) return;

  // Update overlay content
  if (sessionSwitchTitle) {
    sessionSwitchTitle.textContent = isCreating ? "Creating Session" : "Switching Session";
  }
  if (sessionSwitchMessage) {
    sessionSwitchMessage.innerHTML = isCreating
      ? `Setting up <span id="session-switch-name" class="text-mongodb-green-400 font-semibold">${escapeHtml(sessionName)}</span>`
      : `Loading <span id="session-switch-name" class="text-mongodb-green-400 font-semibold">${escapeHtml(sessionName)}</span>`;
    // Re-get the name element since we replaced it
    sessionSwitchName = document.getElementById("session-switch-name");
  }

  // Disable UI elements
  if (sessionSelector) sessionSelector.disabled = true;
  if (newSessionBtn) newSessionBtn.disabled = true;
  if (clearHistoryBtn) clearHistoryBtn.disabled = true;
  if (chatForm) chatForm.style.pointerEvents = "none";
  if (userInput) userInput.disabled = true;

  // Disable all tool buttons
  if (toolButtonsContainer) {
    const buttons = toolButtonsContainer.querySelectorAll("button");
    buttons.forEach(btn => btn.disabled = true);
  }

  // Fade out app container slightly
  if (appContainer) {
    appContainer.style.opacity = "0.3";
    appContainer.style.pointerEvents = "none";
  }

  // Show overlay with animation
  sessionSwitchOverlay.classList.remove("invisible", "opacity-0");
  sessionSwitchOverlay.classList.add("opacity-100");

  // Force reflow for smooth animation
  void sessionSwitchOverlay.offsetWidth;
}

function hideSessionSwitchOverlay() {

  // Remove session switching flag from modal
  if (modalOverlay) {
    modalOverlay.removeAttribute("data-session-switching");
  }

  // FORCE HIDE MODAL IMMEDIATELY - no animation delay
  hideModal(true);

  // Hide overlay immediately too
  if (sessionSwitchOverlay) {
    sessionSwitchOverlay.style.display = "none";
    sessionSwitchOverlay.style.visibility = "hidden";
    sessionSwitchOverlay.style.opacity = "0";
    sessionSwitchOverlay.classList.add("invisible", "opacity-0");
  }

  // Re-enable UI elements immediately
  if (sessionSelector) sessionSelector.disabled = false;
  if (newSessionBtn) newSessionBtn.disabled = false;
  if (clearHistoryBtn) clearHistoryBtn.disabled = false;
  if (chatForm) chatForm.style.pointerEvents = "";
  if (userInput) {
    userInput.disabled = false;
    userInput.focus();
  }

  // Re-enable tool buttons
  if (toolButtonsContainer) {
    const buttons = toolButtonsContainer.querySelectorAll("button");
    buttons.forEach(btn => btn.disabled = false);
  }

  // Restore app container
  if (appContainer) {
    appContainer.style.opacity = "1";
    appContainer.style.pointerEvents = "";
  }

}

// -------------------------
// Session / State Functions
// -------------------------
let indexStatusCache = {};

// Update the session dropdown
function updateSessionDropdown() {
  if (!sessionSelector) {
    console.warn("[updateSessionDropdown] sessionSelector not found!");
    return;
  }

  const previousValue = sessionSelector.value;
  sessionSelector.innerHTML = "";

  // Ensure we always have at least "default" in the list
  const sessionsToShow = allSessions.length > 0 ? allSessions : ["default"];


  sessionsToShow.forEach((session) => {
    const opt = document.createElement("option");
    opt.value = session;
    opt.textContent = session;
    if (session === currentSessionId) {
      opt.selected = true;
    }
    sessionSelector.appendChild(opt);
  });

  // Ensure currentSessionId is selected even if it's not in the list
  if (currentSessionId && !sessionsToShow.includes(currentSessionId)) {
    const opt = document.createElement("option");
    opt.value = currentSessionId;
    opt.textContent = currentSessionId;
    opt.selected = true;
    sessionSelector.insertBefore(opt, sessionSelector.firstChild);
  }

  // If the value changed, log it for debugging
  if (previousValue !== sessionSelector.value) {
  }

}

function loadSessionsAndState() {
 const sessionIdToFetch = currentSessionId; // Capture current session ID
 return fetch(`/state?session_id=${encodeURIComponent(sessionIdToFetch)}`)
  .then((r) => r.json())
  .then((data) => {
   const previousSessions = [...allSessions];
   const previousCurrentSession = currentSessionId;

   // Update sessions list - merge with existing to preserve newly created sessions
   const stateSessions = data.all_sessions || [];
   const mergedSessions = [...new Set([...allSessions, ...stateSessions])].sort();
   allSessions = mergedSessions;

   // Ensure "default" is always in the list
   if (!allSessions.includes("default")) {
     allSessions.unshift("default");
   }

   availableModels = data.available_embedding_models || [];

   // Update currentSessionId from state, but preserve if we have a non-default session
   // that's not in the state yet (newly created session)
   if (data.current_session) {
     currentSessionId = data.current_session;
   } else if (currentSessionId && currentSessionId !== "default" && allSessions.includes(currentSessionId)) {
     // Preserve current session if it exists in our merged list
   } else {
     currentSessionId = "default";
   }

   indexStatusCache = data.index_status || {};


   // Update session dropdown
   updateSessionDropdown();

   if (embeddingModelSelector) {
     const selectedModel = embeddingModelSelector.value;
     embeddingModelSelector.innerHTML = "";
     availableModels.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      embeddingModelSelector.appendChild(opt);
     });
     if (selectedModel && availableModels.includes(selectedModel)) {
       embeddingModelSelector.value = selectedModel;
     }
   }

   updateIndexStatusIndicator();
   return data; // Return data for promise chaining
  })
  .catch((err) => {
   console.error("Failed to load state:", err);
   throw err; // Re-throw to maintain promise chain
  });
}

function updateIndexStatusIndicator() {
  if (!embeddingModelSelector) return;

  const model = embeddingModelSelector.value;
  const status = indexStatusCache[model];

  const existing = document.getElementById('index-status-indicator');
  if (existing) existing.remove();

  if (!status) {
    return;
  }

  const isReady = status.index_ready;
  const docCount = status.document_count || 0;
  const indexStatus = status.index_status || 'UNKNOWN';
  const indexQueryable = status.index_queryable || false;

  if (docCount === 0) {
    return;
  }

  const indicator = document.createElement('div');
  indicator.id = 'index-status-indicator';
  indicator.className = 'index-status-badge';

  let statusConfig = {};

  if (isReady && indexQueryable) {
    statusConfig = {
      variant: 'success',
      icon: '✓',
      text: `${docCount.toLocaleString()} docs indexed`,
      pulse: false
    };
  } else if (indexStatus === 'NOT_FOUND' && docCount > 0) {
    statusConfig = {
      variant: 'warning',
      icon: '⚠',
      text: `Creating index for ${docCount.toLocaleString()} docs...`,
      pulse: true
    };
    fetch(`/index_status?session_id=${encodeURIComponent(currentSessionId)}&embedding_model=${encodeURIComponent(model)}&auto_create=true`)
      .catch(err => console.error('Failed to trigger index creation:', err));
  } else if (indexStatus === 'CREATING') {
    statusConfig = {
      variant: 'info',
      icon: '⏳',
      text: `Creating index (${docCount.toLocaleString()} docs)...`,
      pulse: true
    };
  } else if (indexStatus === 'BUILDING' || indexStatus === 'PENDING') {
    statusConfig = {
      variant: 'info',
      icon: '⏳',
      text: `Index ${indexStatus === 'BUILDING' ? 'building' : 'pending'} (${docCount.toLocaleString()} docs)...`,
      pulse: true
    };
  } else if (indexStatus === 'STALE') {
    statusConfig = {
      variant: 'warning',
      icon: '🔄',
      text: `Index updating (${docCount.toLocaleString()} docs)...`,
      pulse: true
    };
  } else if (indexStatus === 'CREATION_FAILED' || indexStatus === 'FAILED') {
    statusConfig = {
      variant: 'error',
      icon: '❌',
      text: `Index failed (${docCount.toLocaleString()} docs)`,
      pulse: false
    };
  } else if (docCount > 0 && !indexQueryable) {
    statusConfig = {
      variant: 'warning',
      icon: '⏳',
      text: `Index ${indexStatus.toLowerCase()} (${docCount.toLocaleString()} docs)...`,
      pulse: true
    };
  } else {
    return;
  }

  indicator.innerHTML = `
    <div class="index-status-content ${statusConfig.pulse ? 'pulse-animation' : ''}">
      <span class="index-status-icon">${statusConfig.icon}</span>
      <span class="index-status-text">${statusConfig.text}</span>
      <button class="index-status-debug-btn" onclick="openDebugModal()" title="View debug info">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3.5 h-3.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z" />
          <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
      </button>
    </div>
  `;

  indicator.setAttribute('data-variant', statusConfig.variant);

  const parent = embeddingModelSelector.parentElement;
  if (parent) {
    parent.insertBefore(indicator, embeddingModelSelector.nextSibling);
  }
}

function checkIndexReady(embeddingModel, maxWait = 30000, pollInterval = 2000) {
  return new Promise((resolve) => {
    const startTime = Date.now();

    const check = () => {
      fetch(`/index_status?session_id=${encodeURIComponent(currentSessionId)}&embedding_model=${encodeURIComponent(embeddingModel)}&auto_create=true`)
        .then(r => r.json())
        .then(data => {
          if (data.ready_for_search) {
            resolve(true);
            return;
          }

          if (data.index_status === 'CREATING' || data.index_status === 'BUILDING' || data.index_status === 'PENDING') {
            if (Date.now() - startTime > maxWait) {
              resolve(false);
              return;
            }
            setTimeout(check, pollInterval);
            return;
          }

          if (Date.now() - startTime > maxWait) {
            resolve(false);
            return;
          }

          setTimeout(check, pollInterval);
        })
        .catch(() => {
          if (Date.now() - startTime > maxWait) {
            resolve(false);
          } else {
            setTimeout(check, pollInterval);
          }
        });
    };

    check();
  });
}

function switchSession(sessionId, skipModal = false) {

  // Show modal and disable UI (unless already shown)
  if (!skipModal) {
    showSessionSwitchModal(sessionId, false);

    // Disable UI elements
    if (sessionSelector) sessionSelector.disabled = true;
    if (newSessionBtn) newSessionBtn.disabled = true;
    if (clearHistoryBtn) clearHistoryBtn.disabled = true;
    if (chatForm) chatForm.style.pointerEvents = "none";
    if (userInput) userInput.disabled = true;
    if (toolButtonsContainer) {
      const buttons = toolButtonsContainer.querySelectorAll("button");
      buttons.forEach(btn => btn.disabled = true);
    }
    if (appContainer) {
      appContainer.style.opacity = "0.3";
      appContainer.style.pointerEvents = "none";
    }

    // Ensure modal is on top
    if (modalOverlay) {
      modalOverlay.style.zIndex = "9999";
    }
  }

  // Small delay to let modal animation start (if we just showed it)
  const delay = skipModal ? 0 : 200;
  setTimeout(() => {
    fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: `switch_session ${sessionId}`,
        session_id: currentSessionId,
      }),
    })
     .then((r) => r.json())
     .then((data) => {
      if (data.error) {
        console.error("Error switching session:", data.error);
        hideSessionSwitchOverlay();
        alert("Error switching session: " + data.error);
        return;
      }

      // Update session list and currentSessionId from session_update response
      if (data.session_update) {
        if (data.session_update.all_sessions) {
          allSessions = data.session_update.all_sessions;
        }
        if (data.session_update.current_session) {
          currentSessionId = data.session_update.current_session;
        }
      } else {
        // Fallback: use sessionId parameter
        currentSessionId = sessionId;
      }

      // Update session dropdown immediately
      updateSessionDropdown();

      // FORCE HIDE MODAL IMMEDIATELY - no delay
      hideSessionSwitchOverlay();

      // Show message immediately after modal is hidden
      // Clear and update chat box with fade animation
      if (chatBox) {
        // Fade out current content
        chatBox.style.opacity = "0";
        chatBox.style.transition = "opacity 0.3s ease-out";

        setTimeout(() => {
          chatBox.innerHTML = '';
          const welcomeDiv = document.createElement("div");
          welcomeDiv.className = "message system-message animate-fade-in-up bg-yellow-900/50 text-yellow-300 border-l-4 border-yellow-500 p-4 rounded-r-lg";
          welcomeDiv.innerHTML = `<b>Switched to session: ${sessionId}</b>`;
          chatBox.appendChild(welcomeDiv);

          // Fade in new content
          chatBox.style.opacity = "1";
          chatBox.style.transition = "opacity 0.5s ease-in";
        }, 300);
      }

      // Refresh full state to get index status and other info
      loadSessionsAndState();
     })
     .catch((err) => {
      console.error("Failed to switch session:", err);
      hideSessionSwitchOverlay();
      alert("Failed to switch session: " + err.message);
    });
  }, delay);
}

function createSession(newSessionName) {

 // Show modal and disable UI
 showSessionSwitchModal(newSessionName, true);

 // Disable UI elements
 if (sessionSelector) sessionSelector.disabled = true;
 if (newSessionBtn) newSessionBtn.disabled = true;
 if (clearHistoryBtn) clearHistoryBtn.disabled = true;
 if (chatForm) chatForm.style.pointerEvents = "none";
 if (userInput) userInput.disabled = true;
 if (toolButtonsContainer) {
   const buttons = toolButtonsContainer.querySelectorAll("button");
   buttons.forEach(btn => btn.disabled = true);
 }
 if (appContainer) {
   appContainer.style.opacity = "0.3";
   appContainer.style.pointerEvents = "none";
 }

 // Ensure modal is on top
 if (modalOverlay) {
   modalOverlay.style.zIndex = "9999";
 }

 // Small delay to let modal animation start
 setTimeout(() => {
   fetch("/chat", {
     method: "POST",
     headers: { "Content-Type": "application/json" },
     body: JSON.stringify({
       query: `create_session ${newSessionName}`,
       session_id: currentSessionId,
     }),
   })
    .then((r) => r.json())
    .then((data) => {

     if (data.error) {
      console.error("Error creating session:", data.error);
      hideSessionSwitchOverlay();
      alert("Error creating session: " + data.error);
      return;
     }

     // ALWAYS update currentSessionId to the new session (don't trust backend response)
     currentSessionId = newSessionName;

     // Update session list from session_update if available
     if (data.session_update && data.session_update.all_sessions) {
       allSessions = [...data.session_update.all_sessions]; // Copy array
     }

     // CRITICAL: ALWAYS ensure the new session is in the list (backend might not include it yet)
     if (!allSessions.includes(newSessionName)) {
       allSessions.push(newSessionName);
       allSessions.sort();
     }

     // IMPORTANT: Don't trust current_session from response when creating a new session
     // The backend might return the old session. Always use the new session name we created.
     if (data.session_update && data.session_update.current_session === newSessionName) {
     } else if (data.session_update) {
     }

     // ALWAYS update session dropdown immediately with the new session
     updateSessionDropdown();

     // FORCE HIDE MODAL IMMEDIATELY - no delay
     hideSessionSwitchOverlay();

     // Show message immediately after modal is hidden
     // Clear and update chat box with fade animation
     if (chatBox) {
       chatBox.style.opacity = "0";
       chatBox.style.transition = "opacity 0.3s ease-out";

       setTimeout(() => {
         chatBox.innerHTML = '';
         const welcomeDiv = document.createElement("div");
         welcomeDiv.className = "message system-message animate-fade-in-up bg-yellow-900/50 text-yellow-300 border-l-4 border-yellow-500 p-4 rounded-r-lg";
         welcomeDiv.innerHTML = `<b>Created and switched to new session: ${newSessionName}</b>`;
         chatBox.appendChild(welcomeDiv);

         chatBox.style.opacity = "1";
         chatBox.style.transition = "opacity 0.5s ease-in";
       }, 300);
     }

     // Refresh full state to sync with backend
     loadSessionsAndState();
    })
    .catch((err) => {
     console.error("Failed to create session:", err);
     hideSessionSwitchOverlay();
     alert("Failed to create session: " + err.message);
    });
 }, 200);
}

// ------
// Events
// ------
let indexStatusRefreshInterval = null;

function startIndexStatusRefresh() {
  if (indexStatusRefreshInterval) {
    clearInterval(indexStatusRefreshInterval);
  }

  indexStatusRefreshInterval = setInterval(() => {
    if (!embeddingModelSelector) return;

    const model = embeddingModelSelector.value;
    const status = indexStatusCache[model];

    if (status && status.document_count > 0) {
      const needsRefresh = !status.index_ready ||
                          status.index_status === 'CREATING' ||
                          status.index_status === 'BUILDING' ||
                          status.index_status === 'PENDING' ||
                          status.index_status === 'STALE' ||
                          status.index_status === 'NOT_FOUND';

      if (needsRefresh) {
        loadSessionsAndState();
      }
    }
  }, 3000);
}

// Initialize all event listeners after DOM is ready
function initializeEventListeners() {
  // Modal overlay click handler
  if (modalOverlay) {
    modalOverlay.addEventListener("click", (e) => {
     if (e.target === modalOverlay) {
      // Don't close if session is switching
      if (modalOverlay.getAttribute("data-session-switching") === "true") {
        return;
      }
      hideModal();
     }
    });
  }

  // Source browser listeners
  attachSourceBrowserListeners();
  attachChunkListListeners();

  // Chat listeners
  attachChatListeners();

  // Session selector dropdown
  if (sessionSelector) {
    sessionSelector.addEventListener("change", () => {
      const sel = sessionSelector.value;
      if (sel !== currentSessionId) {
        // Call switchSession - it will handle showing the modal and disabling UI
        switchSession(sel, false);
      } else {
        // If same session selected, revert dropdown to current session
        sessionSelector.value = currentSessionId;
      }
    });
  }

  // New session button
  if (newSessionBtn) {
    newSessionBtn.addEventListener("click", () => {
     const name = prompt("Enter new session name:");
     if (name) {
      createSession(name.trim());
     }
    });
  }

  // Clear history button
  if (clearHistoryBtn) {
    clearHistoryBtn.addEventListener("click", () => {
     if (!confirm("Clear chat history for this session?")) return;
     fetch("/history/clear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: currentSessionId }),
     })
      .then((r) => r.json())
      .then((data) => {
       if (data.error) {
        console.error("Error clearing history:", data.error);
       } else {
        if (chatBox) {
          chatBox.innerHTML = "";
          const welcomeDiv = document.createElement("div");
          welcomeDiv.className = "message system-message animate-fade-in-up bg-yellow-900/50 text-yellow-300 border-l-4 border-yellow-500 p-4 rounded-r-lg";
          welcomeDiv.innerHTML = "<b>Welcome!</b> Use the Control Panel on the right to manage sessions, add data, and fine-tune retrieval settings.";
          chatBox.appendChild(welcomeDiv);
        }
       }
      })
      .catch((err) => console.error("Failed to clear history:", err));
    });
  }

  // Embedding model selector
  if (embeddingModelSelector) {
    embeddingModelSelector.addEventListener("change", () => {
      loadSessionsAndState();
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initDOMReferences();
  initializeEventListeners();

  // Initialize dropdown with at least "default" before loading state
  if (allSessions.length === 0) {
    allSessions = ["default"];
  }
  updateSessionDropdown();

  loadSessionsAndState();
  startIndexStatusRefresh();
});

function attachChatListeners() {
  if (chatForm) {
    chatForm.addEventListener("submit", async (event) => {
     event.preventDefault();
     if (!userInput) return;

     const text = userInput.value.trim();
     if (!text) return;

     addUserMessage(text);
     setThinking(true);

     const embeddingModel = embeddingModelSelector ? embeddingModelSelector.value : "text-embedding-3-small";
     const numSources = numSourcesInput ? parseInt(numSourcesInput.value) || 3 : 3;
     const maxChunkLen = maxCharsInput ? parseInt(maxCharsInput.value) || 2000 : 2000;

     const status = indexStatusCache[embeddingModel];
     if (status && !status.index_ready && status.document_count > 0) {
      addSystemMessage(`⏳ Waiting for index to be ready (${status.document_count} documents indexed)...`);
      const isReady = await checkIndexReady(embeddingModel, 30000, 2000);
      if (!isReady) {
       addSystemMessage(`⚠️ Index may still be building. Trying search anyway...`);
      } else {
       addSystemMessage(`✅ Index is ready!`);
       loadSessionsAndState();
      }
     }

     const payload = {
      query: text,
      session_id: currentSessionId,
      embedding_model: embeddingModel,
      rag_params: {
       num_sources: numSources,
       max_chunk_length: maxChunkLen,
      },
     };

     fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
     })
      .then((r) => r.json())
      .then((data) => {
       if (data.error) {
        addBotMessage({ content: `Error: ${data.error}` });
        return;
       }
       const msgs = data.messages || [];
       msgs.forEach((m) => {
        if (m.type === "bot-message") {
         addBotMessage(m);
        } else if (m.type === "system-message") {
         addSystemMessage(m.content);
        }
       });
       if (data.session_update) {
        loadSessionsAndState();
       }
      })
      .catch((err) => {
       addBotMessage({ content: `Error: ${err.message}` });
      })
      .finally(() => {
       setThinking(false);
       if (userInput) {
         userInput.value = "";
         userInput.focus();
         userInput.style.height = 'auto';
       }
      });
    });
  }

  if (userInput) {
    userInput.addEventListener('input', () => {
      userInput.style.height = 'auto';
      userInput.style.height = (userInput.scrollHeight) + 'px';
    });

    userInput.addEventListener("keydown", (event) => {
     if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (chatForm) chatForm.dispatchEvent(new Event('submit'));
     }
    });
  }

  if (toolButtonsContainer) {
    toolButtonsContainer.addEventListener("click", (event) => {
     const btn = event.target.closest("button[data-action]");
     if (!btn) return;
     const action = btn.getAttribute("data-action");
     handleToolAction(action);
    });
  }

  if (previewRagBtn) {
    previewRagBtn.addEventListener("click", () => {
     if (!userInput) return;

     const text = userInput.value.trim();
     if (!text) {
      alert("Type your query in the box first.");
      return;
     }
     const embeddingModel = embeddingModelSelector ? embeddingModelSelector.value : "text-embedding-3-small";
     const numSources = numSourcesInput ? parseInt(numSourcesInput.value) || 3 : 3;
     const minScore = minScoreInput ? parseFloat(minScoreInput.value) || 0 : 0;
     fetch("/preview_search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
       query: text,
       session_id: currentSessionId,
       embedding_model: embeddingModel,
       num_sources: numSources,
      }),
     })
      .then((r) => r.json())
      .then((data) => {
       if (data.error) {
        alert(`Preview error: ${data.error}`);
        return;
       }
       const filteredData = data.filter(res => res.score >= minScore);
       if (!Array.isArray(filteredData) || filteredData.length === 0) {
        showModal({
          title: "RAG Context Preview",
          text: `<div class="text-sm text-gray-400">Query: <span class="text-gray-200">${escapeHtml(userInput.value.trim())}</span></div>`,
          contentHTML: `
            <div class="flex flex-col items-center justify-center py-12">
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-16 h-16 text-gray-500 mb-4">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
              </svg>
              <p class="text-gray-400 text-center max-w-md">No chunks found matching the minimum score threshold (${minScore.toFixed(2)}).</p>
            </div>
          `,
          hideCancel: true,
          submitText: "Close"
        });
        return;
       }

       // Calculate score statistics
       const scores = filteredData.map(r => r.score || 0);
       const maxScore = scores.length > 0 ? Math.max(...scores) : 1;
       const minScoreFound = scores.length > 0 ? Math.min(...scores) : 0;

       // Build clean tab navigation
       const tabNavHTML = filteredData.map((res, idx) => {
         const score = res.score !== undefined ? res.score.toFixed(3) : 'N/A';
         const scoreNum = res.score || 0;
         const scorePercent = maxScore > 0 ? (scoreNum / maxScore) * 100 : 0;

         let scoreColor = 'text-gray-400';
         if (scorePercent >= 80) {
           scoreColor = 'text-green-400';
         } else if (scorePercent >= 60) {
           scoreColor = 'text-yellow-400';
         } else if (scorePercent >= 40) {
           scoreColor = 'text-orange-400';
         }

         return `
           <li class="nav-item" role="presentation">
             <button onclick="switchPreviewTab('preview-${idx}')"
                     class="nav-link chunk-tab ${idx === 0 ? 'active' : ''}"
                     id="preview-tab-${idx}"
                     type="button"
                     role="tab"
                     aria-selected="${idx === 0 ? 'true' : 'false'}"
                     data-tab-index="${idx}">
               <span class="chunk-tab-number">${idx + 1}</span>
               <span class="chunk-tab-score ${scoreColor}">${score}</span>
             </button>
           </li>
         `;
       }).join('');

       // Build preview panels
       const previewPanelsHTML = filteredData.map((res, idx) => {
         const score = res.score !== undefined ? res.score.toFixed(4) : 'N/A';
         const source = escapeHtml(res.source || 'N/A');
         const content = escapeHtml(res.content || '');

         let sourceDisplay = source;
         try {
           if (res.source && res.source.startsWith('http')) {
             const url = new URL(res.source);
             sourceDisplay = url.hostname.replace('www.', '');
           }
         } catch (e) {}

         const scoreNum = res.score || 0;
         const scorePercent = maxScore > 0 ? (scoreNum / maxScore) * 100 : 0;
         let scoreTextColor = 'text-gray-400';
         if (scorePercent >= 80) {
           scoreTextColor = 'text-green-400';
         } else if (scorePercent >= 60) {
           scoreTextColor = 'text-yellow-400';
         } else if (scorePercent >= 40) {
           scoreTextColor = 'text-orange-400';
         }

         return `
           <div class="tab-pane fade ${idx === 0 ? 'show active' : ''}"
                id="preview-panel-${idx}"
                role="tabpanel"
                aria-labelledby="preview-tab-${idx}">
             <div class="chunk-panel-content flex flex-col h-full">
               <div class="flex items-center justify-between mb-4 pb-3 border-b border-gray-700 flex-shrink-0">
                 <div class="flex items-center gap-3">
                   <span class="text-sm font-semibold text-gray-300">Chunk ${idx + 1} of ${filteredData.length}</span>
                   <span class="text-xs font-mono ${scoreTextColor}">${score}</span>
                 </div>
                 <div class="flex items-center gap-2">
                   <a href="/source_content?session_id=${encodeURIComponent(currentSessionId)}&source=${encodeURIComponent(res.source)}"
                      target="_blank"
                      class="text-xs text-blue-400 hover:text-blue-300 transition-colors">
                     ${sourceDisplay}
                   </a>
                 </div>
               </div>
               <div class="chunk-content-area flex-1 overflow-y-auto">
                 <div class="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap bg-gray-900/50 rounded p-4 border border-gray-700">
                   ${content}
                 </div>
               </div>
             </div>
           </div>
         `;
       }).join('');

       showModal({
        title: `
          <div class="flex items-center gap-3">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6 text-mongodb-green-400">
              <path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639l4.43-7.29a1.125 1.125 0 011.906 0l4.43 7.29c.356.586.356 1.35 0 1.936l-4.43 7.29a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" />
            </svg>
            <span>RAG Context Preview</span>
            <span class="text-sm font-normal text-gray-400">(${filteredData.length} ${filteredData.length === 1 ? 'chunk' : 'chunks'})</span>
          </div>
        `,
        text: `
          <div class="text-sm text-gray-400">Query: <span class="text-gray-200">${escapeHtml(userInput.value.trim())}</span></div>
          <div class="text-xs text-gray-500 mt-1">Score Range: ${minScoreFound.toFixed(4)} - ${maxScore.toFixed(4)}</div>
        `,
        contentHTML: `
          <div class="chunk-inspection-modal-content">
            <ul class="nav nav-tabs chunk-tabs-nav border-b border-gray-700 mb-4" role="tablist">
              ${tabNavHTML}
            </ul>
            <div class="tab-content chunk-tab-content" style="height: 500px;">
              ${previewPanelsHTML}
            </div>
          </div>
        `,
        hideCancel: true,
        submitText: "Close"
       });

       // Initialize first tab
       setTimeout(() => {
         if (filteredData.length > 0) {
           window.switchPreviewTab('preview-0');
         }
       }, 100);
      })
      .catch((err) => {
       alert(`Preview request failed: ${err.message}`);
      });
    });
  }

  if (minScoreInput && minScoreValue) {
    minScoreInput.addEventListener("input", () => {
     minScoreValue.textContent = parseFloat(minScoreInput.value).toFixed(2);
    });
  }

  if (maxCharsInput && maxCharsValue) {
    maxCharsInput.addEventListener("input", () => {
     maxCharsValue.textContent = parseInt(maxCharsInput.value);
    });
  }
}

function handleToolAction(action) {
 if (action === "read_url") {
  handleReadUrlAndChunking();
 } else if (action === "read_file") {
  handleReadFile();
 } else if (action === "browse_sources") {
  openSourceBrowser();
 } else if (action === "search_web") {
  handleWebSearch();
 } else if (action === "list_sources" || action === "remove_all") {
   const command = action === "list_sources" ? "list_sources" : "remove_all_sources";
   if (action === "remove_all" && !confirm("Are you sure you want to remove all sources in this session?")) {
     return;
   }

   addUserMessage(command);
   setThinking(true);
   fetch("/chat", {
     method: "POST",
     headers: { "Content-Type": "application/json" },
     body: JSON.stringify({ query: command, session_id: currentSessionId }),
   })
   .then(r => r.json())
   .then(data => {
     if (data.error) {
       addBotMessage({ content: `Error: ${data.error}` });
     } else {
       (data.messages || []).forEach(m => {
         if (m.type === "bot-message" || m.type === "system-message") {
           addBotMessage(m);
         }
       });
     }
   })
   .catch(err => addBotMessage({ content: `Error: ${err.message}` }))
   .finally(() => setThinking(false));
 }
}

// Additional helper functions and ingestion logic would continue here...
// Due to length, I'm including the core structure. The full version would include:
// - renderChunkPreview
// - handleReadFile
// - handleReadUrlAndChunking
// - handleWebSearch
// - pollIngestionTask
// - Debug modal functions
// - Chunk inspection functions
// - Source content modal functions

// ------------------------------------
// --- INGESTION MODAL LOGIC ---
// ------------------------------------

async function renderChunkPreview(content, chunkSize, chunkOverlap, targetElementId, countElementId) {
  const targetEl = document.getElementById(targetElementId);
  const countEl = document.getElementById(countElementId);
  if (!targetEl || !countEl) return;

  targetEl.innerHTML = '<div class="flex justify-center items-center h-full"><div class="spinner-large"></div></div>';
  countEl.textContent = 'Total Chunks: ...';

  if (chunkOverlap >= chunkSize) {
    targetEl.innerHTML = '<p class="text-red-500 p-4 text-center">Error: Chunk overlap must be smaller than chunk size.</p>';
    countEl.textContent = 'Total Chunks: 0';
    return false;
  }

  try {
    const formData = new FormData();
    formData.append('content', content);
    formData.append('chunk_size', chunkSize);
    formData.append('chunk_overlap', chunkOverlap);

    const response = await fetch("/chunk_preview", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();

    if (data.error) {
      targetEl.innerHTML = `<p class="text-red-500 p-4 text-center">Error chunking: ${escapeHtml(data.error)}</p>`;
      countEl.textContent = 'Total Chunks: 0';
      return false;
    }

    if (!data.chunks || data.chunks.length === 0) {
      targetEl.innerHTML = '<p class="text-gray-400 p-4 text-center">Could not generate any chunks from the source content.</p>';
      countEl.textContent = 'Total Chunks: 0';
      return false;
    }

    const chunkHtml = data.chunks.map((c, i) => `
          <div class="chunk-card">
            <div class="chunk-header"><div class="chunk-title">Chunk ${i + 1}</div></div>
            <div class="chunk-content">${escapeHtml(c)}</div>
          </div>
    `).join('');

    targetEl.innerHTML = `<div class="chunk-list-container animate-fade-in-up">${chunkHtml}</div>`;
    countEl.textContent = `Total Chunks: ${data.chunks.length}`;
    return true;
  } catch (err) {
    targetEl.innerHTML = `<p class="text-red-500 p-4 text-center">Request error: ${escapeHtml(err.message)}</p>`;
    countEl.textContent = 'Total: 0';
    return false;
  }
}

function handleReadFile() {
  let sourceName = '';
  let currentFile = null;

  const modalHTML = `
    <div id="file-drop-zone" class="w-full p-6 border-2 border-dashed border-gray-600 rounded-lg text-center cursor-pointer hover:border-mongodb-green-500 transition-all duration-200">
      <input type="file" id="ingestion-file-input" class="hidden" />
      <div id="file-drop-zone-prompt" class="flex flex-col items-center justify-center text-gray-400">
         <svg xmlns="http://www.w3.org/2000/svg" class="w-12 h-12 mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
           <path stroke-linecap="round" stroke-linejoin="round" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M12 15l-4-4m0 0l4-4m-4 4h12" />
         </svg>
        <p class="font-semibold text-gray-200">Drag & drop your file here</p>
        <p class="text-sm">or click to browse</p>
      </div>
      <div id="file-drop-zone-display" class="hidden flex-col items-center justify-center">
         <svg xmlns="http://www.w3.org/2000/svg" class="w-12 h-12 mb-2 text-mongodb-green-500" viewBox="0 0 20 20" fill="currentColor">
             <path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4zm2 6a1 1 0 011-1h6a1 1 0 110 2H7a1 1 0 01-1-1zm1 3a1 1 0 100 2h6a1 1 0 100-2H7z" clip-rule="evenodd" />
         </svg>
         <p id="file-name-display" class="font-semibold text-gray-200"></p>
         <p class="text-sm text-gray-400">Click again or drop another file to replace</p>
      </div>
    </div>
    <div class="flex gap-4 mt-4 h-[50vh]">
      <div class="w-1/2 flex flex-col bg-gray-900/50 rounded-lg">
        <div class="flex justify-between items-center border-b border-gray-700 p-3">
          <h4 class="font-bold text-mongodb-green-500">Source Content</h4>
        </div>
        <div class="flex-grow p-1">
         <textarea id="ingestion-source-content-textarea" class="w-full h-full bg-transparent text-gray-200 p-2 rounded-md resize-none focus:outline-none focus:ring-1 focus:ring-mongodb-green-500" placeholder="Select a file to begin..."></textarea>
        </div>
      </div>
      <div class="w-1/2 flex flex-col bg-gray-900/50 rounded-lg">
        <div class="flex justify-between items-center border-b border-gray-700 p-3">
          <h4 class="font-bold text-mongodb-green-500">Chunk Preview</h4>
          <span id="ingestion-chunk-count" class="text-sm font-mono bg-gray-700 text-mongodb-green-500 px-2 py-1 rounded">Total: 0</span>
        </div>
        <div id="ingestion-chunk-preview-host" class="flex-grow overflow-y-auto p-3">
          <p class="text-gray-400 text-center pt-10">Chunks will appear here.</p>
        </div>
      </div>
    </div>
    <div id="ingestion-controls" class="grid grid-cols-3 gap-4 text-sm p-4 border-t border-gray-700 mt-4 items-center">
      <div class="flex items-center gap-2">
        <label class="font-medium text-gray-300">Chunk Size:</label>
        <input type="number" id="ingestion-chunk-size" value="1000" min="100" step="100" class="w-24 bg-gray-700 border border-gray-600 rounded-md px-2 py-1 text-sm">
      </div>
      <div class="flex items-center gap-2">
        <label class="font-medium text-gray-300">Overlap:</label>
        <input type="number" id="ingestion-chunk-overlap" value="150" min="0" step="50" class="w-24 bg-gray-700 border border-gray-600 rounded-md px-2 py-1 text-sm">
      </div>
      <button id="ingestion-rechunk-btn" class="btn btn-secondary w-full">Update Chunk Preview</button>
    </div>`;

  showModal({
    title: "Add File to Knowledge Base",
    text: "Drop a file or click the area below, edit content if needed, adjust chunking, and submit to ingest.",
    contentHTML: modalHTML,
    onSubmit: () => {
      const content = document.getElementById('ingestion-source-content-textarea').value;
      if (!content || !sourceName) {
        alert('Please select and load a file first.');
        return;
      }
      const chunkSize = parseInt(document.getElementById('ingestion-chunk-size').value);
      const chunkOverlap = parseInt(document.getElementById('ingestion-chunk-overlap').value);

      if (chunkOverlap >= chunkSize) {
        alert("Chunk overlap must be less than chunk size.");
        return;
      }

      fetch("/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: content,
          source: sourceName,
          source_type: "file",
          session_id: currentSessionId,
          chunk_size: chunkSize,
          chunk_overlap: chunkOverlap,
        }),
      }).then(r => r.json()).then(resp => {
        if (resp.error) {
          alert(`Error ingesting file: ${resp.error}`);
        } else if (resp.task_id) {
          hideModal();
          pollIngestionTask(resp.task_id);
        }
      }).catch(err => alert(`Error: ${err.message}`));
    }
  });

  const dropZone = document.getElementById('file-drop-zone');
  const fileInput = document.getElementById('ingestion-file-input');
  const dropZonePrompt = document.getElementById('file-drop-zone-prompt');
  const dropZoneDisplay = document.getElementById('file-drop-zone-display');
  const fileNameDisplay = document.getElementById('file-name-display');
  const contentTextarea = document.getElementById('ingestion-source-content-textarea');
  const rechunkBtn = document.getElementById('ingestion-rechunk-btn');

  const processFile = (file) => {
    if (!file) return;
    currentFile = file;

    fileNameDisplay.textContent = file.name;
    dropZonePrompt.classList.add('hidden');
    dropZoneDisplay.classList.remove('hidden');
    dropZoneDisplay.classList.add('flex');

    contentTextarea.value = 'Loading file content...';
    const formData = new FormData();
    formData.append('file', file);

    fetch('/preview_file', { method: 'POST', body: formData })
      .then(r => r.json()).then(data => {
        if (data.error) {
          contentTextarea.value = `Error: ${escapeHtml(data.error)}`;
          return;
        }
        sourceName = data.filename;
        contentTextarea.value = data.content;

        const chunkSize = parseInt(document.getElementById('ingestion-chunk-size').value);
        const chunkOverlap = parseInt(document.getElementById('ingestion-chunk-overlap').value);
        renderChunkPreview(data.content, chunkSize, chunkOverlap, 'ingestion-chunk-preview-host', 'ingestion-chunk-count');
      }).catch(err => {
        contentTextarea.value = `Fetch error: ${escapeHtml(err.message)}`;
      });
  }

  if (dropZone && fileInput) {
    dropZone.addEventListener('click', () => fileInput.click());
    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropZone.classList.add('drop-zone-dragover');
    });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drop-zone-dragover'));
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('drop-zone-dragover');
      if (e.dataTransfer.files.length > 0) {
        fileInput.files = e.dataTransfer.files;
        processFile(e.dataTransfer.files[0]);
      }
    });
    fileInput.addEventListener('change', () => {
      if (fileInput.files.length > 0) {
        processFile(fileInput.files[0]);
      }
    });
  }

  if (rechunkBtn) {
    rechunkBtn.addEventListener('click', () => {
      const content = contentTextarea.value;
      if (!content) { alert('Load a file first.'); return; }
      const chunkSize = parseInt(document.getElementById('ingestion-chunk-size').value);
      const chunkOverlap = parseInt(document.getElementById('ingestion-chunk-overlap').value);
      renderChunkPreview(content, chunkSize, chunkOverlap, 'ingestion-chunk-preview-host', 'ingestion-chunk-count');
      rechunkBtn.classList.remove('needs-update');
    });
  }

  if (contentTextarea) {
    contentTextarea.addEventListener('input', () => {
      if (rechunkBtn) rechunkBtn.classList.add('needs-update');
    });
  }
  const chunkSizeInput = document.getElementById('ingestion-chunk-size');
  const chunkOverlapInput = document.getElementById('ingestion-chunk-overlap');
  if (chunkSizeInput) chunkSizeInput.addEventListener('input', () => {
    if (rechunkBtn) rechunkBtn.classList.add('needs-update');
  });
  if (chunkOverlapInput) chunkOverlapInput.addEventListener('input', () => {
    if (rechunkBtn) rechunkBtn.classList.add('needs-update');
  });
}

function handleReadUrlAndChunking(initialUrl = '') {
  const modalHTML = `
    <div class="mb-4 flex gap-2">
      <input type="text" id="ingestion-url-input" value="${initialUrl}" placeholder="Enter URL..." class="flex-grow bg-gray-700 border border-gray-600 rounded-md px-3 py-2 text-sm focus:ring-2 focus:ring-mongodb-green-500 focus:outline-none">
      <button id="ingestion-load-url-btn" class="btn btn-primary">Load Content</button>
    </div>
    <div class="flex gap-4 mt-4 h-[55vh]">
      <div class="w-1/2 flex flex-col bg-gray-900/50 rounded-lg">
        <div class="flex justify-between items-center border-b border-gray-700 p-3">
          <h4 class="font-bold text-mongodb-green-500">Source Content</h4>
        </div>
        <div class="flex-grow p-1">
         <textarea id="ingestion-source-content-textarea" class="w-full h-full bg-transparent text-gray-200 p-2 rounded-md resize-none focus:outline-none focus:ring-1 focus:ring-mongodb-green-500" placeholder="Enter a URL and click 'Load Content'..."></textarea>
        </div>
      </div>
      <div class="w-1/2 flex flex-col bg-gray-900/50 rounded-lg">
        <div class="flex justify-between items-center border-b border-gray-700 p-3">
          <h4 class="font-bold text-mongodb-green-500">Chunk Preview</h4>
          <span id="ingestion-chunk-count" class="text-sm font-mono bg-gray-700 text-mongodb-green-500 px-2 py-1 rounded">Total: 0</span>
        </div>
        <div id="ingestion-chunk-preview-host" class="flex-grow overflow-y-auto p-3">
          <p class="text-gray-400 text-center pt-10">Chunks will appear here.</p>
        </div>
      </div>
    </div>
    <div id="ingestion-controls" class="grid grid-cols-3 gap-4 text-sm p-4 border-t border-gray-700 mt-4 items-center">
      <div class="flex items-center gap-2">
        <label class="font-medium text-gray-300">Chunk Size:</label>
        <input type="number" id="ingestion-chunk-size" value="1000" min="100" step="100" class="w-24 bg-gray-700 border border-gray-600 rounded-md px-2 py-1 text-sm">
      </div>
      <div class="flex items-center gap-2">
        <label class="font-medium text-gray-300">Overlap:</label>
        <input type="number" id="ingestion-chunk-overlap" value="150" min="0" step="50" class="w-24 bg-gray-700 border border-gray-600 rounded-md px-2 py-1 text-sm">
      </div>
      <button id="ingestion-rechunk-btn" class="btn btn-secondary w-full">Update Chunk Preview</button>
    </div>`;

  showModal({
    title: "Add URL to Knowledge Base",
    text: "Fetch content, edit if needed, adjust chunking, and submit to ingest.",
    contentHTML: modalHTML,
    onSubmit: () => {
      const url = document.getElementById('ingestion-url-input').value.trim();
      const content = document.getElementById('ingestion-source-content-textarea').value;
      if (!url || !content) {
        alert('Please load the URL content first.');
        return;
      }
      const chunkSize = parseInt(document.getElementById('ingestion-chunk-size').value);
      const chunkOverlap = parseInt(document.getElementById('ingestion-chunk-overlap').value);

      if (chunkOverlap >= chunkSize) {
        alert("Chunk overlap must be less than chunk size.");
        return;
      }

      fetch("/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: content,
          source: url,
          source_type: "url",
          session_id: currentSessionId,
          chunk_size: chunkSize,
          chunk_overlap: chunkOverlap,
        }),
      }).then(r => r.json()).then(resp => {
        if (resp.error) {
          alert(`Error ingesting URL: ${resp.error}`);
        } else if (resp.task_id) {
          hideModal();
          pollIngestionTask(resp.task_id);
        }
      }).catch(err => alert(`Error: ${err.message}`));
    }
  });

  const urlInput = document.getElementById('ingestion-url-input');
  const loadBtn = document.getElementById('ingestion-load-url-btn');
  const contentTextarea = document.getElementById('ingestion-source-content-textarea');
  const rechunkBtn = document.getElementById('ingestion-rechunk-btn');

  const loadUrlContent = () => {
    const url = urlInput.value.trim();
    if (!url) return;
    contentTextarea.value = 'Loading URL content...';

    fetch(`/preview_url?url=${encodeURIComponent(url)}`)
      .then(r => r.json()).then(data => {
        if (data.error) {
          contentTextarea.value = `Error: ${escapeHtml(data.error)}`;
          return;
        }
        contentTextarea.value = data.markdown;

        const chunkSize = parseInt(document.getElementById('ingestion-chunk-size').value);
        const chunkOverlap = parseInt(document.getElementById('ingestion-chunk-overlap').value);
        renderChunkPreview(data.markdown, chunkSize, chunkOverlap, 'ingestion-chunk-preview-host', 'ingestion-chunk-count');
      }).catch(err => {
        contentTextarea.value = `Fetch error: ${escapeHtml(err.message)}`;
      });
  };

  if (loadBtn) loadBtn.addEventListener('click', loadUrlContent);
  if (rechunkBtn) {
    rechunkBtn.addEventListener('click', () => {
      const content = contentTextarea.value;
      if (!content) { alert('Load URL content first.'); return; }
      const chunkSize = parseInt(document.getElementById('ingestion-chunk-size').value);
      const chunkOverlap = parseInt(document.getElementById('ingestion-chunk-overlap').value);
      renderChunkPreview(content, chunkSize, chunkOverlap, 'ingestion-chunk-preview-host', 'ingestion-chunk-count');
      rechunkBtn.classList.remove('needs-update');
    });
  }

  if (contentTextarea) {
    contentTextarea.addEventListener('input', () => {
      if (rechunkBtn) rechunkBtn.classList.add('needs-update');
    });
  }
  const chunkSizeInput = document.getElementById('ingestion-chunk-size');
  const chunkOverlapInput = document.getElementById('ingestion-chunk-overlap');
  if (chunkSizeInput) chunkSizeInput.addEventListener('input', () => {
    if (rechunkBtn) rechunkBtn.classList.add('needs-update');
  });
  if (chunkOverlapInput) chunkOverlapInput.addEventListener('input', () => {
    if (rechunkBtn) rechunkBtn.classList.add('needs-update');
  });

  if (initialUrl) {
    loadUrlContent();
  }
}

function handleWebSearch() {
 showModal({
  title: "Search the Web",
  text: "Enter your search query to do a DuckDuckGo-based web search:",
  contentHTML: `<input type="text" id="web-search-input" class="w-full bg-gray-700 p-2 rounded" placeholder="Search...">`,
  onSubmit: () => {
   const query = document.getElementById("web-search-input").value.trim();
   if (!query) {
    alert("No query provided");
    return;
   }
   hideModal();
   addUserMessage(`web_search ${query}`);
   setThinking(true);

   fetch("/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, num_results: 5 }),
   })
    .then((r) => r.json())
    .then((data) => {
     if (data.error) {
      addBotMessage({ content: `Web search error: ${data.error}` });
     } else if (data.results && data.results.length > 0) {
      const resultsHtml = data.results.map((r) => {
       const isValidUrl = r.href && (r.href.startsWith('http://') || r.href.startsWith('https://'));
       const url = isValidUrl ? r.href : '#';
       let host = 'N/A';
       if (isValidUrl) {
        try {
         host = new URL(url).hostname;
        } catch (e) { }
       }

       return `
          <div class="web-result-card animate-fade-in-up">
           <div class="flex justify-between items-start mb-2">
            <h4 class="text-white font-bold text-lg leading-tight">
             <a href="${url}" target="_blank" class="hover:underline">${escapeHtml(r.title)}</a>
            </h4>
            <button data-url="${url}" class="read-url-btn text-xs px-3 py-1 rounded-full transition-colors font-medium flex-shrink-0 flex items-center gap-1">
             <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="w-4 h-4">
              <path stroke-linecap="round" stroke-linejoin="round" d="M13.19 8.688a4.5 4.5 0 011.242 7.244l-4.5 4.5a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" />
             </svg>
             Read & Ingest
            </button>
           </div>
           <p class="text-gray-300 text-sm mb-2">${escapeHtml(r.body)}</p>
           <a href="${url}" target="_blank" class="url-link hover:underline">${host}</a>
          </div>
         `;
      }).join('');

      addBotMessage({ content: `<div><p>Web Search Results:</p><div class="mt-4">${resultsHtml}</div></div>` });

      document.querySelectorAll('.read-url-btn').forEach(button => {
       button.addEventListener('click', (e) => {
        const url = e.target.closest('button').getAttribute('data-url');
        if (url && url !== '#') {
         handleReadUrlAndChunking(url);
        }
       });
      });

     } else {
      addBotMessage({ content: "No web search results found." });
     }
    })
    .catch((err) => {
     addBotMessage({ content: `Web search error: ${err.message}` });
    })
    .finally(() => {
     setThinking(false);
    });
  },
 });
}


// ---------------------------
// Ingestion Progress Overlay
// ---------------------------
let ingestionOverlay = null;
let ingestionStatusInterval = null;

function createIngestionOverlay() {
  if (ingestionOverlay) return ingestionOverlay;

  const overlay = document.createElement('div');
  overlay.id = 'ingestion-overlay';
  overlay.className = 'fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4 z-50';
  overlay.innerHTML = `
    <div class="bg-gray-800 rounded-lg shadow-xl w-full max-w-md p-6 border border-gray-700">
      <div class="flex items-center gap-3 mb-4">
        <div class="spinner-large"></div>
        <h3 class="text-xl font-bold text-white">Processing Ingestion</h3>
      </div>
      <div id="ingestion-status-text" class="text-gray-300 mb-4 min-h-[3rem]">
        Starting ingestion...
      </div>
      <div class="w-full bg-gray-700 rounded-full h-2 mb-2">
        <div id="ingestion-progress-bar" class="bg-mongodb-green-500 h-2 rounded-full transition-all duration-300" style="width: 0%"></div>
      </div>
      <p class="text-xs text-gray-400 text-center">Please wait while your content is being processed...</p>
    </div>
  `;
  document.body.appendChild(overlay);
  ingestionOverlay = overlay;
  return overlay;
}

function showIngestionOverlay() {
  const overlay = createIngestionOverlay();
  overlay.classList.remove('hidden');
  overlay.style.display = 'flex';
}

function hideIngestionOverlay() {
  if (ingestionOverlay) {
    ingestionOverlay.classList.add('hidden');
    ingestionOverlay.style.display = 'none';
  }

  if (ingestionStatusInterval) {
    clearInterval(ingestionStatusInterval);
    ingestionStatusInterval = null;
  }
}

function updateIngestionStatus(status, step, progress = 0) {
  const statusText = document.getElementById('ingestion-status-text');
  const progressBar = document.getElementById('ingestion-progress-bar');

  if (statusText) {
    const stepMessages = {
      'pending': 'Initializing...',
      'processing': step || 'Processing...',
      'complete': 'Complete!',
      'failed': 'Failed'
    };

    let message = stepMessages[status] || status;
    if (status === 'processing' && step) {
      message = step;
    } else if (status === 'complete') {
      message = '✅ Ingestion completed successfully!';
    } else if (status === 'failed') {
      message = `❌ Ingestion failed: ${step || 'Unknown error'}`;
    }

    statusText.textContent = message;
  }

  if (progressBar) {
    let progressPercent = progress;
    if (status === 'pending') progressPercent = 10;
    else if (status === 'processing') progressPercent = Math.max(20, Math.min(90, progress));
    else if (status === 'complete') progressPercent = 100;
    else if (status === 'failed') progressPercent = 0;

    progressBar.style.width = `${progressPercent}%`;
  }
}

function pollIngestionTask(taskId) {
  showIngestionOverlay();
  updateIngestionStatus('pending', 'Starting ingestion...', 10);

  let pollCount = 0;
  const maxPolls = 300;

  const checkStatus = () => {
    pollCount++;
    if (pollCount > maxPolls) {
      hideIngestionOverlay();
      addSystemMessage(`Ingestion timeout: Task ${taskId} took too long. Please check server logs.`);
      return;
    }

    fetch(`/ingest/status/${taskId}`)
     .then(r => r.json())
     .then(data => {
      const status = data.status || 'pending';
      const step = data.step || data.message || '';

      let progress = 20;
      if (step.includes('Chunking')) progress = 30;
      else if (step.includes('Generating embeddings')) progress = 50;
      else if (step.includes('Verifying') || step.includes('Checking')) progress = 60;
      else if (step.includes('Preparing')) progress = 70;
      else if (step.includes('Saving')) progress = 85;
      else if (step.includes('Verifying')) progress = 95;

      updateIngestionStatus(status, step, progress);

      if (status === 'complete') {
        hideIngestionOverlay();
        addSystemMessage(`✅ Ingestion successful! ${data.message || ''}`);
        loadSessionsAndState();
      } else if (status === 'failed') {
        hideIngestionOverlay();
        addSystemMessage(`❌ Ingestion failed: ${data.message || 'Unknown error'}`);
      } else {
        ingestionStatusInterval = setTimeout(checkStatus, 2000);
      }
     })
     .catch(err => {
      console.error('Failed to get ingestion status:', err);
      ingestionStatusInterval = setTimeout(checkStatus, 2000);
     });
  };

  ingestionStatusInterval = setTimeout(checkStatus, 1000);
}

// Debug and inspection functions (simplified versions)
window.openDebugModal = function() {
  const model = embeddingModelSelector.value;
  showModal({
    title: "Debug & Insights",
    text: `Debug information for session: ${currentSessionId}, model: ${model}`,
    contentHTML: `<p class="text-gray-400">Debug modal - full implementation would show detailed debug info</p>`,
  });
};

window.inspectRetrievedChunks = function(messageId) {

  const query = messageQueryMap.get(messageId);
  const chunks = (window.messageChunksMap && window.messageChunksMap.get(messageId)) || [];


  if (!query) {
    alert('Query information not available for this message.');
    return;
  }

  if (!chunks || chunks.length === 0) {
    console.warn('[Chunk Inspection] No chunks found for message:', messageId);
    console.warn('[Chunk Inspection] Available message IDs in chunks map:', window.messageChunksMap ? Array.from(window.messageChunksMap.keys()) : 'map does not exist');
    console.warn('[Chunk Inspection] Available message IDs in query map:', Array.from(messageQueryMap.keys()));

    // Show debug info
    const debugInfo = window.messageChunksMap ?
      `Map has ${window.messageChunksMap.size} entries. Keys: ${Array.from(window.messageChunksMap.keys()).join(', ')}` :
      'Chunks map does not exist';

  showModal({
    title: "Retrieved Chunks",
    text: `Query: ${query}`,
      contentHTML: `
        <div class="flex flex-col items-center justify-center py-12">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-16 h-16 text-gray-500 mb-4">
            <path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
          </svg>
          <p class="text-gray-400 text-center max-w-md mb-4">No chunks available for this message. The query may not have retrieved any results, or chunks were not stored during retrieval.</p>
          <div class="text-xs text-gray-500 mt-4 p-3 bg-gray-800 rounded border border-gray-700 max-w-md">
            <p class="font-mono text-left">Debug: ${debugInfo}</p>
            <p class="font-mono text-left mt-2">Message ID: ${messageId}</p>
          </div>
        </div>
      `,
    });
    return;
  }

  // Calculate score statistics for visualization
  const scores = chunks.map(c => c.score || 0).filter(s => s > 0);
  const maxScore = scores.length > 0 ? Math.max(...scores) : 1;
  const minScore = scores.length > 0 ? Math.min(...scores) : 0;

  // Build clean, minimal tab navigation
  const tabNavHTML = chunks.map((chunk, index) => {
    const score = chunk.score !== undefined ? chunk.score.toFixed(3) : 'N/A';
    const scoreNum = chunk.score || 0;
    const scorePercent = maxScore > 0 ? (scoreNum / maxScore) * 100 : 0;

    // Simple color based on score
    let scoreColor = 'text-gray-400';
    let scoreBg = 'bg-gray-600';
    if (scorePercent >= 80) {
      scoreColor = 'text-green-400';
      scoreBg = 'bg-green-500';
    } else if (scorePercent >= 60) {
      scoreColor = 'text-yellow-400';
      scoreBg = 'bg-yellow-500';
    } else if (scorePercent >= 40) {
      scoreColor = 'text-orange-400';
      scoreBg = 'bg-orange-500';
    }

    return `
      <li class="nav-item" role="presentation">
        <button onclick="switchChunkTab('${messageId}', ${index})"
                class="nav-link chunk-tab ${index === 0 ? 'active' : ''}"
                id="chunk-tab-${messageId}-${index}"
                type="button"
                role="tab"
                aria-selected="${index === 0 ? 'true' : 'false'}"
                data-tab-index="${index}">
          <span class="chunk-tab-number">${index + 1}</span>
          <span class="chunk-tab-score ${scoreColor}">${score}</span>
        </button>
      </li>
    `;
  }).join('');

  // Build chunk content panels (Bootstrap tab content style)
  const chunkPanelsHTML = chunks.map((chunk, index) => {
    const score = chunk.score !== undefined ? chunk.score.toFixed(4) : 'N/A';
    const scoreNum = chunk.score || 0;
    const source = escapeHtml(chunk.source || 'N/A');
    const text = escapeHtml(chunk.text || '');
    const isLong = text.length > 600;
    const previewLength = 600;
    const displayText = isLong ? text.substring(0, previewLength) : text;
    const remainingText = isLong ? text.substring(previewLength) : '';

    // Calculate score visualization
    const scorePercent = maxScore > 0 ? (scoreNum / maxScore) * 100 : 0;
    let scoreBadgeColor = 'bg-gray-500';
    let scoreTextColor = 'text-gray-400';
    if (scorePercent >= 80) {
      scoreBadgeColor = 'bg-gradient-to-r from-green-500 to-emerald-500';
      scoreTextColor = 'text-green-400';
    } else if (scorePercent >= 60) {
      scoreBadgeColor = 'bg-gradient-to-r from-yellow-500 to-amber-500';
      scoreTextColor = 'text-yellow-400';
    } else if (scorePercent >= 40) {
      scoreBadgeColor = 'bg-gradient-to-r from-orange-500 to-red-500';
      scoreTextColor = 'text-orange-400';
    }

    // Extract domain from source URL
    let sourceDisplay = source;
    try {
      if (chunk.source && chunk.source.startsWith('http')) {
        const url = new URL(chunk.source);
        sourceDisplay = url.hostname.replace('www.', '');
      }
    } catch (e) {}

    const chunkId = `chunk-${messageId}-${index}`;
    const panelId = `chunk-panel-${messageId}-${index}`;

    return `
      <div class="tab-pane fade ${index === 0 ? 'show active' : ''}"
           id="${panelId}"
           role="tabpanel"
           aria-labelledby="chunk-tab-${messageId}-${index}">
        <div class="chunk-panel-content flex flex-col h-full">
          <!-- Clean header -->
          <div class="flex items-center justify-between mb-4 pb-3 border-b border-gray-700 flex-shrink-0">
            <div class="flex items-center gap-3">
              <span class="text-sm font-semibold text-gray-300">Chunk ${index + 1} of ${chunks.length}</span>
              <span class="text-xs font-mono ${scoreTextColor}">${score}</span>
            </div>
            <div class="flex items-center gap-2">
              <a href="/source_content?session_id=${encodeURIComponent(currentSessionId)}&source=${encodeURIComponent(chunk.source)}"
                 target="_blank"
                 class="text-xs text-blue-400 hover:text-blue-300 transition-colors">
                ${sourceDisplay}
              </a>
              <button onclick="copyChunkText('${chunkId}')"
                      class="text-xs text-gray-400 hover:text-mongodb-green-400 transition-colors px-2 py-1">
                Copy
              </button>
            </div>
          </div>

          <!-- Chunk content - fixed height with scroll -->
          <div class="chunk-content-area flex-1 overflow-y-auto">
            <div class="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap bg-gray-900/50 rounded p-4 border border-gray-700" id="chunk-text-${chunkId}">
              ${displayText}${isLong ? '<span class="text-gray-500">...</span>' : ''}
            </div>
            ${isLong ? `
              <div id="chunk-expanded-${chunkId}" class="hidden mt-2">
                <div class="text-sm text-gray-200 leading-relaxed whitespace-pre-wrap bg-gray-900/50 rounded p-4 border border-gray-700">${remainingText}</div>
              </div>
              <button onclick="toggleChunkExpand('${chunkId}')"
                      class="mt-2 text-xs text-mongodb-green-400 hover:text-mongodb-green-300 transition-colors">
                <span class="expand-text">Show more</span>
              </button>
            ` : ''}
          </div>
        </div>
      </div>
    `;
  }).join('');

  showModal({
    title: `
      <div class="flex items-center gap-3">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-6 h-6 text-mongodb-green-400">
          <path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639l4.43-7.29a1.125 1.125 0 011.906 0l4.43 7.29c.356.586.356 1.35 0 1.936l-4.43 7.29a4.5 4.5 0 01-6.364-6.364l1.757-1.757m13.35-.622l1.757-1.757a4.5 4.5 0 00-6.364-6.364l-4.5 4.5a4.5 4.5 0 001.242 7.244" />
        </svg>
        <span>Retrieved Context</span>
        <span class="text-sm font-normal text-gray-400">(${chunks.length} ${chunks.length === 1 ? 'chunk' : 'chunks'})</span>
      </div>
    `,
    text: `
      <div class="text-sm text-gray-400">Query: <span class="text-gray-200">${escapeHtml(query)}</span></div>
    `,
    contentHTML: `
      <div class="chunk-inspection-modal-content">
        <!-- Clean tab navigation -->
        <ul class="nav nav-tabs chunk-tabs-nav border-b border-gray-700 mb-4" role="tablist">
          ${tabNavHTML}
        </ul>

        <!-- Tab content - fixed height container -->
        <div class="tab-content chunk-tab-content" style="height: 500px;">
          ${chunkPanelsHTML}
        </div>
      </div>
    `,
    hideCancel: true,
    submitText: "Close"
  });

  // Force button updates immediately after modal is shown
  setTimeout(() => {
    if (modalCancelBtn) {
      modalCancelBtn.style.display = "none";
      modalCancelBtn.style.visibility = "hidden";
    }
    if (modalSubmitBtn) {
      modalSubmitBtn.textContent = "Close";
    }
  }, 0);

  // Initialize first tab as active
  setTimeout(() => {
    if (chunks.length > 0) {
      window.switchChunkTab(messageId, 0);
    }
  }, 100);
};

// Preview tab switching function
window.switchPreviewTab = function(tabId) {
  const tabIndex = parseInt(tabId.replace('preview-', ''));

  // Hide all preview panels
  document.querySelectorAll('[id^="preview-panel-"]').forEach(panel => {
    panel.classList.remove('show', 'active');
  });

  // Remove active from all preview tab buttons
  document.querySelectorAll('[id^="preview-tab-"]').forEach(btn => {
    btn.classList.remove('active');
    btn.setAttribute('aria-selected', 'false');
  });

  // Show selected panel
  const selectedPanel = document.getElementById(`preview-panel-${tabIndex}`);
  if (selectedPanel) {
    selectedPanel.classList.add('show', 'active');
  }

  // Activate selected tab button
  const selectedTab = document.getElementById(`preview-tab-${tabIndex}`);
  if (selectedTab) {
    selectedTab.classList.add('active');
    selectedTab.setAttribute('aria-selected', 'true');
    selectedTab.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }
};

// Clean tab switching function
window.switchChunkTab = function(messageId, tabIndex) {
  // Hide all tab panes
  document.querySelectorAll(`[id^="chunk-panel-${messageId}-"]`).forEach(panel => {
    panel.classList.remove('show', 'active');
  });

  // Remove active from all tab buttons
  document.querySelectorAll(`[id^="chunk-tab-${messageId}-"]`).forEach(btn => {
    btn.classList.remove('active');
    btn.setAttribute('aria-selected', 'false');
  });

  // Show selected panel
  const selectedPanel = document.getElementById(`chunk-panel-${messageId}-${tabIndex}`);
  if (selectedPanel) {
    selectedPanel.classList.add('show', 'active');
  }

  // Activate selected tab button
  const selectedTab = document.getElementById(`chunk-tab-${messageId}-${tabIndex}`);
  if (selectedTab) {
    selectedTab.classList.add('active');
    selectedTab.setAttribute('aria-selected', 'true');
    // Scroll tab into view if needed
    selectedTab.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }
};

// Helper functions for chunk interaction
window.toggleChunkExpand = function(chunkId) {
  const expandedDiv = document.getElementById(`chunk-expanded-${chunkId}`);
  const button = event.target.closest('button');
  const expandText = button.querySelector('.expand-text');
  const expandIcon = button.querySelector('.expand-icon');

  if (expandedDiv && expandedDiv.classList.contains('hidden')) {
    expandedDiv.classList.remove('hidden');
    expandText.textContent = 'Show less';
    if (expandIcon) expandIcon.classList.add('rotate-180');
    button.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } else if (expandedDiv) {
    expandedDiv.classList.add('hidden');
    expandText.textContent = expandText.textContent.replace('Show less', 'Show more');
    if (expandIcon) expandIcon.classList.remove('rotate-180');
  }
};

window.copyChunkText = function(chunkId) {
  const button = event.target.closest('button');

  // Extract messageId and chunkIndex from chunkId (format: chunk-msg-123-0)
  const parts = chunkId.split('-');
  const chunkIndex = parseInt(parts[parts.length - 1]);
  const messageId = parts.slice(1, -1).join('-');

  const chunks = (window.messageChunksMap && window.messageChunksMap.get(messageId)) || [];
  const chunk = chunks[chunkIndex];

  if (chunk && chunk.text) {
    navigator.clipboard.writeText(chunk.text).then(() => {
      const originalHTML = button.innerHTML;
      button.innerHTML = `
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="w-3.5 h-3.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M4.5 12.75l6 6 9-13.5" />
        </svg>
        Copied!
      `;
      button.classList.add('text-mongodb-green-400');
      setTimeout(() => {
        button.innerHTML = originalHTML;
        button.classList.remove('text-mongodb-green-400');
      }, 2000);
    }).catch(err => {
      console.error('Failed to copy:', err);
    });
  }
};

// Collapsible section functionality
window.toggleSection = function(sectionId) {
  const section = document.getElementById(sectionId);
  if (!section) return;

  const header = section.previousElementSibling;
  const chevron = header?.querySelector('.section-chevron');

  const isCollapsed = section.classList.contains('collapsed');

  if (isCollapsed) {
    section.classList.remove('collapsed');
    if (chevron) chevron.classList.remove('rotated');
  } else {
    section.classList.add('collapsed');
    if (chevron) chevron.classList.add('rotated');
  }

  // Save state to localStorage
  try {
    localStorage.setItem(`section-${sectionId}`, isCollapsed ? 'expanded' : 'collapsed');
  } catch (e) {
    // Ignore localStorage errors
  }
};

// Restore section states from localStorage on page load
function restoreSectionStates() {
  const sections = ['session-section', 'retrieval-section'];
  sections.forEach(sectionId => {
    try {
      const state = localStorage.getItem(`section-${sectionId}`);
      if (state === 'collapsed') {
        const section = document.getElementById(sectionId);
        const header = section?.previousElementSibling;
        const chevron = header?.querySelector('.section-chevron');
        if (section) {
          section.classList.add('collapsed');
          if (chevron) chevron.classList.add('rotated');
        }
      }
    } catch (e) {
      // Ignore localStorage errors
    }
  });
}

// Initialize on DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', restoreSectionStates);
} else {
  restoreSectionStates();
}

// Interactive RAG UI initialized
