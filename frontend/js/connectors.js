function connectorOutput(data) {
    const el = document.getElementById('connector-output');
    if (!el) return;
    el.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
}

async function loadConnectorStatus() {
    try {
        const r = await fetch(`${API}/connectors/gmail/status`, { headers: authHeaders() });
        const data = await readApiResponse(r);
        const box = document.getElementById('gmail-status');
        if (box) {
            box.innerHTML = data.connected
                ? `<span class="font-bold text-green-700">Connected</span> · provider: ${escapeHtml(data.provider)} · account: ${escapeHtml(data.account_id || 'N/A')}`
                : `<span class="font-bold text-amber-700">Not connected</span> · Gmail OAuth credentials may be missing or not completed.`;
        }
        connectorOutput(data);
    } catch (e) {
        connectorOutput(`Gmail status failed: ${e.message}`);
    }
}

async function connectGmail() {
    try {
        const r = await fetch(`${API}/connectors/gmail/auth-url`, { headers: authHeaders() });
        const data = await readApiResponse(r);
        connectorOutput(data);
        if (data.authorization_url) {
            window.open(data.authorization_url, '_blank');
        }
    } catch (e) {
        connectorOutput(`Gmail OAuth start failed: ${e.message}\n\nFor real Gmail OAuth set these in backend .env:\nGOOGLE_CLIENT_ID=...\nGOOGLE_CLIENT_SECRET=...\nGOOGLE_REDIRECT_URI=http://127.0.0.1:8000/api/connectors/gmail/callback`);
    }
}

async function syncGmail() {
    try {
        const r = await fetch(`${API}/connectors/gmail/sync?max_results=5`, {
            method: 'POST',
            headers: authHeaders(),
        });
        const data = await readApiResponse(r);
        connectorOutput(data);
    } catch (e) {
        connectorOutput(`Gmail sync failed: ${e.message}\n\nUse 'Import demo email' if you only want to test the CITDS email EvidenceUnit pipeline without Google OAuth.`);
    }
}

async function importDemoGmail() {
    const payload = {
        messages: [
            {
                message_id: `frontend-demo-${Date.now()}`,
                thread_id: `frontend-thread-${Date.now()}`,
                subject: 'Please review the draft',
                sender: 'boss@example.com',
                recipients: ['me@example.com'],
                snippet: 'Please review the draft by 2026-06-03.',
                body: 'Official request. Action: review the draft. Deadline: 2026-06-03.',
                sent_at: '2026-05-28T10:00:00',
                direction: 'inbound',
                labels: ['INBOX'],
                relation_key: `frontend-demo-draft-review-${Date.now()}`,
            }
        ]
    };
    try {
        const r = await fetch(`${API}/evidence/import/gmail`, {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
        });
        const data = await readApiResponse(r);
        connectorOutput({ message: 'Demo Gmail evidence imported. Ask: What is my current action list?', ...data });
    } catch (e) {
        connectorOutput(`Demo Gmail import failed: ${e.message}`);
    }
}

async function uploadBrowserHistory() {
    const input = document.getElementById('browser-history-file');
    if (!input || !input.files || !input.files[0]) {
        alert('Please select a .json or .csv browser history export first.');
        return;
    }
    const fd = new FormData();
    fd.append('file', input.files[0]);
    try {
        const r = await fetch(`${API}/connectors/browser-history/upload`, {
            method: 'POST',
            headers: authHeaders(),
            body: fd,
        });
        const data = await readApiResponse(r);
        connectorOutput({ message: 'Browser history uploaded. Ask: Can browser history alone create an action item?', ...data });
    } catch (e) {
        connectorOutput(`Browser history upload failed: ${e.message}`);
    }
}

async function importDemoGoogleSearch() {
    const payload = {
        items: [
            {
                url: 'https://www.google.com/search?q=kosice+hotel',
                title: 'Google search: kosice hotel',
                visited_at: '2026-05-28T11:00:00',
                visit_count: 1,
                relation_key: `frontend-google-search-${Date.now()}`,
                metadata: { demo: true, source: 'frontend_connector_panel' },
            }
        ]
    };
    try {
        const r = await fetch(`${API}/evidence/import/browser-history`, {
            method: 'POST',
            headers: authHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
        });
        const data = await readApiResponse(r);
        connectorOutput({ message: 'Demo Google search / browser-history evidence imported. It should stay contextual-only.', ...data });
    } catch (e) {
        connectorOutput(`Demo Google search import failed: ${e.message}`);
    }
}
