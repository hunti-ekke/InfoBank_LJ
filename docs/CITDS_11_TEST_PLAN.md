# CITDS 11 Full Smoke Test Plan

Branch: `feature/citds-11-hardening`

PR: `feat: complete CITDS 11 hardening package`

This test plan verifies the implementation of the CITDS PDF concepts: usable relevance, source roles, governance, aggregate-only handling, metadata-only handling, evidence reconstruction, connector imports, and audit coverage.

---

## 0. Pull branch

```bash
git fetch origin
git checkout feature/citds-11-hardening
git pull origin feature/citds-11-hardening
```

---

## 1. Install dependencies

```bash
cd backend_python
pip install -r requirements.txt
```

---

## 2. Apply MySQL helper migration if needed

If the database already existed before CITDS 11, run:

```sql
SOURCE backend_python/migrations/citds_11_mysql.sql;
```

Or paste the file manually in phpMyAdmin.

Critical expected DB support:

- `user_document_permission.permission_type` supports `Metadata`
- `documents.visibility` supports `Private`, `Aggregate`, `Metadata`
- `evidence_units` table exists
- `policy_rules` table exists
- `connector_accounts` table exists

---

## 3. Start backend

```bash
cd backend_python
uvicorn main:app --reload
```

Open:

```text
http://127.0.0.1:8000/docs
```

Expected:

- Swagger loads
- `/api/test-db` works
- No backend import error

---

## 4. Frontend hard refresh

Open frontend and press:

```text
Ctrl + F5
```

Expected:

- Login screen appears if logged out
- Existing session is restored if token is valid

---

## 5. Session persistence

1. Login.
2. Refresh page.
3. Expected: still logged in.
4. Click Log Out.
5. Refresh page.
6. Expected: login screen remains.

---

## 6. Owner PDF flow

Upload the CITDS test PDF as:

```text
Owner / Private
```

Ask:

```text
What is the project codename?
```

Expected:

- Answer contains the exact codename from the PDF.
- Source role includes `primary`.
- Raw source text may be visible because owner has Full access.

Ask:

```text
What color is the deployment server rack?
```

Expected:

```text
The answer cannot be found in the document.
```

This verifies controlled failure / no hallucination.

---

## 7. Aggregate-only flow

As owner, change document Visibility to:

```text
Aggregate
```

In DB, verify:

```sql
SELECT file_path, visibility FROM documents;
```

Expected:

```text
visibility = Aggregate
```

Login as another user and ask:

```text
What was the average approval time in the aggregate pilot statistics?
```

Expected:

- Answer includes `3.4 days` if using the prepared CITDS test PDF.
- Source role: `aggregate-only`.
- Source panel does not show raw document text.
- Source panel shows content hidden / aggregate-only message.

---

## 8. Metadata-only flow

As owner, change document Visibility to:

```text
Metadata Only
```

In DB:

```sql
SELECT file_path, visibility FROM documents;
```

Expected:

```text
visibility = Metadata
```

Login as another user and ask:

```text
What is the project codename?
```

Expected:

- The answer must NOT reveal the codename.
- It should explain that only metadata-level sources are available and content is withheld by policy.
- Trace should show metadata-only source count.

---

## 9. Policy resolve: document

Swagger authorize with Bearer token.

Call:

```text
GET /api/policy/resolve/document/{doc_id}
```

Expected for owner/private:

```json
{
  "use_decision": "full",
  "source_role": "primary"
}
```

Expected for another user + aggregate visibility:

```json
{
  "use_decision": "aggregate",
  "source_role": "aggregate-only"
}
```

Expected for another user + metadata visibility:

```json
{
  "use_decision": "metadata"
}
```

---

## 10. Seed evidence demo

Swagger:

```text
POST /api/evidence/demo-action-list
```

Expected:

- `imported`: 8 or more, depending on previous seeded data.

Then call:

```text
GET /api/evidence/action-list
```

Expected:

- open items exist
- browser history records appear as contextual only
- completed previous-trip task appears closed/contrastive
- classified units include classifier output
- policy object appears in reconstruction output

---

## 11. CITDS self-test

Call:

```text
GET /api/citds/self-test
```

Expected:

- `status = success`
- score should be close to or equal to 1.0 for clean seeded demo
- checks include:
  - primary evidence required for open items
  - contextual-only does not create open task
  - contrastive evidence closes items
  - no browser history primary role

---

## 12. Classifier endpoint

Call:

```text
POST /api/citds/classify
```

Body:

```json
{
  "text": "Browser history: visited Kosice hotel search. This is contextual support only.",
  "task_intent": "current_action_list",
  "use_decision": "full",
  "use_llm": false
}
```

Expected:

- `source_role = contextual`
- warning includes contextual support / not direct proof

Call again with:

```json
{
  "text": "Official request. Action: review the draft. Deadline: 2026-06-03.",
  "task_intent": "current_action_list",
  "use_decision": "full",
  "use_llm": false
}
```

Expected:

- `source_role = primary`
- speech act includes request/assignment-like signal

---

## 13. Gmail import contract

Call:

```text
POST /api/evidence/import/gmail
```

Body:

```json
{
  "messages": [
    {
      "message_id": "msg-001",
      "thread_id": "thread-demo-001",
      "subject": "Please review the draft",
      "sender": "boss@example.com",
      "recipients": ["me@example.com"],
      "snippet": "Please review the draft by 2026-06-03.",
      "body": "Official request. Action: review the draft. Deadline: 2026-06-03.",
      "sent_at": "2026-05-28T10:00:00",
      "direction": "inbound",
      "labels": ["INBOX"],
      "relation_key": "demo-draft-review"
    }
  ]
}
```

Expected:

- imported = 1
- `/api/evidence/units` includes source_type `Email`
- `/api/evidence/action-list` can reconstruct it as primary/open unless closed by contrastive evidence

---

## 14. Browser history JSON/CSV upload

Create `history.json`:

```json
[
  {
    "url": "https://example.com/hotel-kosice",
    "title": "Kosice hotel search",
    "visited_at": "2026-05-28T11:00:00",
    "visit_count": 2,
    "relation_key": "kosice-trip"
  }
]
```

Call:

```text
POST /api/connectors/browser-history/upload
```

Upload `history.json`.

Expected:

- parsed = 1
- imported = 1
- evidence unit source_type = BrowserHistory
- action-list does not create a task from this alone

---

## 15. Gmail OAuth connector path

Set `.env`:

```env
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://127.0.0.1:8000/api/connectors/gmail/callback
```

Call:

```text
GET /api/connectors/gmail/auth-url
```

Expected:

- returns Google authorization URL

Complete OAuth in browser.

Then call:

```text
GET /api/connectors/gmail/status
POST /api/connectors/gmail/sync?max_results=5
```

Expected:

- status connected
- sync imports Gmail messages as EvidenceUnit records

Note: This requires valid Google Cloud OAuth credentials and Gmail API enabled.

---

## 16. Admin trace and coverage

Call:

```text
GET /api/admin/citds-traces
GET /api/admin/citds-coverage
```

Expected:

- only current user's traces are visible
- coverage reports relevance/source-role/governance dimensions

---

## 17. Implementation status

Call:

```text
GET /api/citds/implementation-status
```

Expected:

- lists all implemented CITDS components
- coverage should report all listed implementation components as implemented/contract/self-test

---

## 18. Merge readiness checklist

Merge only if these pass:

- [ ] Backend starts cleanly
- [ ] Swagger loads
- [ ] Login/session restore works
- [ ] Owner PDF answers direct facts
- [ ] Controlled failure works
- [ ] Aggregate-only hides raw content and allows aggregate-safe fact
- [ ] Metadata-only hides raw content and refuses content claim
- [ ] Policy resolve works for document and evidence unit
- [ ] Evidence demo action-list works
- [ ] BrowserHistory stays contextual-only
- [ ] Closed/completed task becomes contrastive/closed
- [ ] Gmail import contract works
- [ ] Browser history upload works
- [ ] CITDS self-test passes
- [ ] Admin traces are user-scoped
- [ ] No critical backend exceptions
