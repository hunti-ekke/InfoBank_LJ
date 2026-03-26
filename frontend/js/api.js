const API = "http://127.0.0.1:8000/api";
let CURRENT_USER_ID = "";
let ACCESS_TOKEN = "";

async function doRegister() {
    const d = { username: document.getElementById('reg-name').value, email: document.getElementById('reg-email').value, password: document.getElementById('reg-pass').value };
    const r = await fetch(`${API}/register`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(d) });
    if(r.ok) { alert("Registration successful! You can now log in."); toggleAuth(); }
    else { const err = await r.json(); alert(err.detail || "Registration failed."); }
}

async function doLogin() {
    const d = { email: document.getElementById('log-email').value, password: document.getElementById('log-pass').value };
    const r = await fetch(`${API}/login`, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(d) });
    const res = await r.json();
    if(r.ok) { 
        CURRENT_USER_ID = res.user_id; 
        ACCESS_TOKEN = res.access_token;
        document.getElementById('login-screen').classList.add('hidden'); 
        document.getElementById('app-wrapper').classList.remove('hidden'); 
        loadProfile(); 
    } else alert("Login failed");
}

async function loadProfile() {
    try {
        const r = await fetch(`${API}/profile/me`, { headers: { 'Authorization': `Bearer ${ACCESS_TOKEN}` } });
        const user = await r.json();
        if(!r.ok) throw new Error(user.detail);

        document.getElementById('sidebar-display-username').innerText = user.username;
        if(user.full_name) {
            document.getElementById('sidebar-display-fullname').innerText = user.full_name;
            document.getElementById('sidebar-display-fullname').classList.remove('hidden');
        } else {
            document.getElementById('sidebar-display-fullname').classList.add('hidden');
        }
        renderAvatar('sidebar-avatar-container', user.avatar_url, user.username);
    } catch(e) { console.error("Profile load failed", e); }
}

async function saveProfile() {
    const btn = document.getElementById('btn-save-profile');
    const data = {
        full_name: document.getElementById('prof-full-name').value,
        email: document.getElementById('prof-email').value,
        avatar_url: document.getElementById('prof-avatar-url').value
    };
    btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i> Saving...';
    try {
        const r = await fetch(`${API}/profile/update`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${ACCESS_TOKEN}` },
            body: JSON.stringify(data)
        });
        if(r.ok) { alert("Profile updated successfully!"); closeProfileModal(); loadProfile(); } 
        else { const err = await r.json(); alert("Update failed: " + err.detail); }
    } catch(e) { alert("Server error."); }
    finally { btn.disabled = false; btn.innerHTML = '<i class="fas fa-save"></i><span>Save Changes</span>'; }
}

async function askQuestion() {
    const input = document.getElementById('chat-input');
    const box = document.getElementById('view-chat');
    const btn = document.getElementById('btn-send-chat');
    const q = input.value.trim(); if(!q) return;

    box.innerHTML += `<div class="flex justify-end w-full mb-2"><div class="bg-blue-600 text-white p-3 px-5 rounded-2xl rounded-tr-none max-w-[80%] md:max-w-2xl shadow-sm text-sm">${q}</div></div>`;
    input.value = ""; input.disabled = true; btn.disabled = true;
    box.scrollTop = box.scrollHeight; showTyping();

    const fd = new FormData(); fd.append("question", q); fd.append("user_id", CURRENT_USER_ID);
    try {
        const r = await fetch(`${API}/ask`, { method: 'POST', body: fd });
        const res = await r.json(); removeTyping();
        if (r.ok) {
            if (res.status === "success") {
                const formattedText = res.answer.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

                let sourcesHTML = "";
                if (res.sources && res.sources.length > 0) {
                    let srcBlocks = res.sources.map(src => `
                        <div class="bg-gray-50 p-3 rounded-lg text-xs text-gray-600 border border-gray-200 shadow-sm">
                            <div class="font-bold text-gray-700 mb-1 flex items-center gap-1">
                                <i class="far fa-file-pdf text-red-500"></i> ${src.file_name}
                            </div>
                            <div class="italic leading-relaxed max-h-24 overflow-y-auto pr-1 text-[11px]">
                                "${src.text}"
                            </div>
                        </div>
                    `).join('');

                    sourcesHTML = `
                        <div class="mt-4 pt-3 border-t border-gray-100">
                            <details class="group">
                                <summary class="text-xs text-blue-500 font-bold cursor-pointer list-none flex items-center gap-1 hover:text-blue-700 transition">
                                    <i class="fas fa-chevron-down transition-transform duration-300 group-open:rotate-180"></i>
                                    View Retrieved Sources
                                </summary>
                                <div class="mt-3 space-y-2">${srcBlocks}</div>
                            </details>
                        </div>`;
                }
                box.innerHTML += `
                    <div class="flex justify-start w-full mb-2">
                        <div class="bg-white border border-gray-200 p-5 rounded-2xl rounded-tl-none max-w-[80%] md:max-w-2xl text-gray-800 shadow-sm text-sm leading-relaxed">
                            <div class="flex items-center space-x-2 mb-3 pb-3 border-b border-gray-100 text-[10px] text-gray-500 uppercase tracking-widest font-bold">
                                <i class="fas fa-filter text-blue-500"></i>
                                <span>Semantic Routing: ${res.extracted_keywords?.join(', ') || 'N/A'}</span>
                            </div>
                            <p style="white-space: pre-wrap;">${formattedText}</p>
                            ${sourcesHTML} </div>
                    </div>`;
            } else if (res.status === "controlled_failure") {
                box.innerHTML += `<div class="flex justify-start w-full mb-2"><div class="bg-red-50 border-l-4 border-red-500 p-4 rounded-r-2xl max-w-[80%] md:max-w-xl text-red-800 shadow-sm text-sm"><h3 class="font-bold mb-1"><i class="fas fa-shield-alt mr-2"></i>Governance Control</h3><p>${res.message}</p></div></div>`;
            }
        } else box.innerHTML += `<div class="flex justify-start w-full mb-2"><div class="bg-red-50 text-red-800 p-3 rounded-2xl max-w-xl text-sm">Error: ${res.detail || "Unknown"}</div></div>`;
    } catch(e) { removeTyping(); box.innerHTML += `<div class="flex justify-start w-full mb-2"><div class="bg-red-50 text-red-800 p-3 rounded-2xl max-w-xl text-sm">Connection Error.</div></div>`; }
    finally { input.disabled = false; btn.disabled = false; input.focus(); box.scrollTop = box.scrollHeight; }
}

async function uploadDocument() {
    const fd = new FormData(); const fileInput = document.getElementById('upload-file');
    if(!fileInput.files[0]) return alert("Please select a file first.");

    fd.append("file", fileInput.files[0]); fd.append("user_id", CURRENT_USER_ID); fd.append("permission_type", document.getElementById('upload-permission').value);
    const btn = document.getElementById('btn-upload'); btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i> Ingesting...';
    
    try {
        const r = await fetch(`${API}/upload`, { method: 'POST', body: fd });
        if(r.ok) { alert("Document vectorized successfully!"); loadDocs(); } 
        else { const err = await r.json(); alert("Error: " + err.detail); }
    } catch(e) { alert("Upload failed."); }
    resetUploadUX();
}

async function loadDocs() {
    const r = await fetch(`${API}/documents/${CURRENT_USER_ID}`); const data = await r.json();
    const tbody = document.getElementById('docs-tbody'); tbody.innerHTML = "";
    data.documents.forEach(doc => {
        const isOwner = doc.permission === "Owner";
        const iconClass = isOwner ? 'fa-trash-alt' : 'fa-unlink';
        const iconTitle = isOwner ? 'Permanent Delete' : 'Unsubscribe';
        const transferBtn = isOwner ? `<button onclick="openTransferModal('${doc.document_id}')" class="text-blue-500 hover:text-blue-700 transition ml-3" title="Transfer Ownership"><i class="fas fa-exchange-alt"></i></button>` : '';

        tbody.innerHTML += `
            <tr class="hover:bg-gray-50 transition">
                <td class="px-6 py-4 font-medium text-gray-800 whitespace-nowrap"><i class="far fa-file-pdf text-red-500 mr-2"></i>${doc.file_name}</td>
                <td class="px-6 py-4"><div class="flex items-center space-x-2"><input type="text" id="kw-${doc.document_id}" value="${doc.keywords.join(', ')}" class="flex-1 border border-gray-300 rounded-md px-3 py-1.5 text-xs outline-none focus:ring-2 focus:ring-blue-400"><button onclick="saveKW('${doc.document_id}')" class="text-white bg-blue-500 hover:bg-blue-600 rounded-md p-1.5 transition"><i class="fas fa-save"></i></button></div></td>
                <td class="px-6 py-4"><select onchange="savePerm('${doc.document_id}',this.value)" ${!isOwner?'disabled':''} class="text-xs border border-gray-300 rounded-md p-2 outline-none ${!isOwner?'opacity-50 cursor-not-allowed bg-gray-100':'bg-white focus:ring-2 focus:ring-blue-400'}"><option value="Owner" ${doc.permission==='Owner'?'selected':''}>Owner</option><option value="Reader" ${doc.permission==='Reader'?'selected':''}>Reader</option><option value="Aggregate" ${doc.permission==='Aggregate'?'selected':''}>Aggregate</option></select></td>
                <td class="px-6 py-4 text-center">
                    <button onclick="deleteDoc('${doc.document_id}', '${isOwner}')" class="text-gray-400 hover:text-red-600 transition" title="${iconTitle}"><i class="fas ${iconClass}"></i></button>
                    ${transferBtn}
                </td>
            </tr>`;
    });
}

async function saveKW(id) { const fd = new FormData(); fd.append("doc_id", id); fd.append("keywords", document.getElementById('kw-'+id).value); await fetch(`${API}/documents/update-keywords`, { method: 'POST', body: fd }); alert("Keywords updated!"); }
async function savePerm(id, p) { const fd = new FormData(); fd.append("doc_id", id); fd.append("user_id", CURRENT_USER_ID); fd.append("new_perm", p); await fetch(`${API}/documents/update-permission`, { method: 'POST', body: fd }); alert("Permission updated!"); loadDocs(); }

async function deleteDoc(id, isOwnerStr) {
    const isOwner = isOwnerStr === 'true';
    if (!confirm(isOwner ? "WARNING: Permanently delete document and AI vectors?" : "Unsubscribe from document?")) return;
    const fd = new FormData(); fd.append("doc_id", id); fd.append("user_id", CURRENT_USER_ID); 
    try { const r = await fetch(`${API}/documents/delete`, { method: 'DELETE', body: fd }); if(r.ok) { loadDocs(); loadMap(); } else alert("Error"); } catch(e) { alert("Server error"); }
}

async function submitTransfer() {
    const docId = document.getElementById('transferDocId').value;
    const newUsername = document.getElementById('transferUsernameInput').value;
    
    if (!newUsername) { alert("Please select a new owner!"); return; }

    if (confirm(`Are you sure you want to transfer this document to ${newUsername}? This cannot be undone!`)) {
        const btn = document.getElementById('btn-submit-transfer');
        btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i> Transferring...';
        
        const formData = new FormData();
        formData.append("doc_id", docId);
        formData.append("user_id", CURRENT_USER_ID);
        formData.append("new_username", newUsername);

        try {
            const response = await fetch(`${API}/documents/transfer`, { method: 'POST', body: formData });
            const result = await response.json();
            if (response.ok) {
                alert(result.message); closeTransferModal(); loadDocs();
            } else { alert("Error: " + result.detail); }
        } catch (error) { console.error("Transfer error:", error); alert("Server error."); } 
        finally { btn.disabled = false; btn.innerHTML = '<i class="fas fa-check"></i><span>Transfer</span>'; }
    }
}

async function loadMap() {
    const r = await fetch(`${API}/knowledge-map/${CURRENT_USER_ID}`); const data = await r.json();
    const box = document.getElementById('view-map'); box.innerHTML = "";
    if (data.map.length === 0) return box.innerHTML = '<div class="text-gray-500">Empty map.</div>';
    data.map.forEach(k => {
        let s = k.count > 3 ? "text-xl font-bold py-3 px-6 shadow-md" : (k.count > 1 ? "text-base font-semibold py-2 px-5" : "text-sm py-2 px-4");
        box.innerHTML += `<span class="${s} bg-white border border-blue-200 text-blue-700 rounded-full hover:scale-110 transition cursor-default shadow-sm m-1">${k.keyword} <span class="bg-blue-100 text-blue-800 text-xs px-2 py-0.5 rounded-full ml-1">${k.count}</span></span>`;
    });
}