function toggleAuth() { 
    document.getElementById('form-login').classList.toggle('hidden'); 
    document.getElementById('form-register').classList.toggle('hidden'); 
}

function renderAvatar(containerId, url, username) {
    const container = document.getElementById(containerId);
    container.innerHTML = "";
    if(url && url.trim() !== "") {
        const img = document.createElement('img');
        img.src = url; img.alt = username; img.className = "w-full h-full object-cover";
        img.onerror = () => renderMonogram(container, username);
        container.appendChild(img);
    } else {
        renderMonogram(container, username);
    }
}

function renderMonogram(container, username) {
    container.innerHTML = `<span class="text-xl font-bold text-white uppercase">${username.substring(0, 2)}</span>`;
}

function openProfileModal() {
    fetch(`${API}/profile/me`, { headers: { 'Authorization': `Bearer ${ACCESS_TOKEN}` } })
        .then(r => r.json())
        .then(user => {
            document.getElementById('prof-full-name').value = user.full_name || "";
            document.getElementById('prof-email').value = user.email || "";
            document.getElementById('prof-avatar-url').value = user.avatar_url || "";
            updateModalAvatarPreview(user.avatar_url, user.username);
            document.getElementById('profile-modal').classList.remove('hidden');
        });
}

function closeProfileModal() { document.getElementById('profile-modal').classList.add('hidden'); }

function updateModalAvatarPreview(url) {
    const username = document.getElementById('sidebar-display-username').innerText;
    renderAvatar('modal-avatar-preview', url, username);
}

function switchView(v) {
    document.querySelectorAll('.view-section').forEach(s => s.classList.add('hidden'));
    document.getElementById('view-'+v).classList.remove('hidden');
    document.getElementById('chat-bar').style.display = (v === 'chat' ? 'block' : 'none');
    document.getElementById('view-title').innerText = v === 'ontology' ? 'Smart Ontology' : v.charAt(0).toUpperCase() + v.slice(1);
    
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.replace('bg-blue-50', 'text-gray-600'));
    document.getElementById('btn-'+v).classList.replace('text-gray-600', 'bg-blue-50');
    document.getElementById('btn-'+v).classList.add('text-blue-700');
    
    if(v==='map') loadMap(); 
    if(v==='manager') loadDocs();
    if(v==='ontology') loadOntology();
}

function updateFileLabel() {
    const fileInput = document.getElementById('upload-file');
    const btn = document.getElementById('btn-select-pdf');
    if (fileInput.files.length > 0) {
        btn.innerHTML = `<i class="far fa-file-pdf text-red-500 mr-2"></i>${fileInput.files[0].name}`;
        btn.classList.replace('text-blue-600', 'text-gray-800');
    } else {
        btn.innerHTML = "Select PDF";
        btn.classList.replace('text-gray-800', 'text-blue-600');
    }
}

function resetUploadUX() {
    document.getElementById('upload-file').value = "";
    updateFileLabel();
    const btn = document.getElementById('btn-upload');
    btn.disabled = false; btn.innerText = "Upload & Ingest";
}

function handleChatKeyPress(e) { if (e.key === 'Enter') askQuestion(); }
function showTyping() {
    const box = document.getElementById('view-chat');
    box.innerHTML += `<div id="typing-bubble" class="flex justify-start w-full mb-2"><div class="bg-white border border-gray-200 p-4 rounded-2xl rounded-tl-none shadow-sm typing-indicator"><span></span><span></span><span></span></div></div>`;
    box.scrollTop = box.scrollHeight;
}
function removeTyping() { const b = document.getElementById('typing-bubble'); if(b) b.remove(); }

function openTransferModal(docId) {
    document.getElementById('transferDocId').value = docId;
    document.getElementById('transferUsernameInput').value = '';
    document.getElementById('transferResults').classList.add('hidden');
    document.getElementById('transferModal').classList.remove('hidden');
}

function closeTransferModal() {
    document.getElementById('transferModal').classList.add('hidden');
}

// Autocomplete eseménykezelő
let transferSearchTimeout = null;
document.addEventListener('DOMContentLoaded', () => {
    const transferInput = document.getElementById('transferUsernameInput');
    if(transferInput) {
        transferInput.addEventListener('input', (e) => {
            clearTimeout(transferSearchTimeout);
            const query = e.target.value;
            const resultsDiv = document.getElementById('transferResults');

            if (query.length < 2) {
                resultsDiv.classList.add('hidden'); resultsDiv.innerHTML = '';
                return;
            }

            transferSearchTimeout = setTimeout(async () => {
                try {
                    const response = await fetch(`${API}/users/search?q=${query}`);
                    const data = await response.json();

                    resultsDiv.innerHTML = '';
                    if (data.users.length === 0) {
                        resultsDiv.innerHTML = '<div class="autocomplete-item text-gray-500">No users found.</div>';
                    } else {
                        data.users.forEach(user => {
                            const div = document.createElement('div'); div.className = 'autocomplete-item';
                            div.innerHTML = `<strong>${user.username}</strong>`; 
                            div.onclick = () => {
                                document.getElementById('transferUsernameInput').value = user.username;
                                resultsDiv.classList.add('hidden');
                            };
                            resultsDiv.appendChild(div);
                        });
                    }
                    resultsDiv.classList.remove('hidden');
                } catch (error) { console.error("Search error:", error); }
            }, 300);
        });
    }
});