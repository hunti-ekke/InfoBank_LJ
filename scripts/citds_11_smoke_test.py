#!/usr/bin/env python3
"""Automated CITDS 11 smoke test.

Run against a local backend:

    cd backend_python
    uvicorn main:app --reload

Then in another terminal:

    python scripts/citds_11_smoke_test.py

The test creates two users, uploads a generated PDF, checks Owner/Aggregate/
Metadata behavior, seeds evidence units, tests classifier, browser-history upload,
and prints a PASS/FAIL summary.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import string
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import fitz
import requests


DEFAULT_API = "http://127.0.0.1:8000/api"


class SmokeFailure(Exception):
    pass


class Smoke:
    def __init__(self, api_base: str):
        self.api = api_base.rstrip("/")
        self.results: List[Dict[str, Any]] = []
        self.owner_token = ""
        self.reader_token = ""
        self.owner_id = ""
        self.reader_id = ""
        self.doc_id = ""

    def step(self, name: str, func):
        print(f"\n[TEST] {name}")
        try:
            value = func()
            self.results.append({"name": name, "status": "PASS", "details": value})
            print(f"[PASS] {name}")
            return value
        except Exception as e:
            self.results.append({"name": name, "status": "FAIL", "error": str(e)})
            print(f"[FAIL] {name}: {e}")
            raise

    def request(self, method: str, path: str, *, token: str | None = None, expected: int = 200, **kwargs) -> requests.Response:
        headers = kwargs.pop("headers", {}) or {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"{self.api}{path}"
        response = requests.request(method, url, headers=headers, timeout=90, **kwargs)
        if response.status_code != expected:
            raise SmokeFailure(f"{method} {path} returned {response.status_code}: {response.text[:1000]}")
        return response

    def register_login_users(self):
        suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))
        users = [
            (f"citds_owner_{suffix}", f"citds_owner_{suffix}@example.com"),
            (f"citds_reader_{suffix}", f"citds_reader_{suffix}@example.com"),
        ]
        password = "SmokeTest123!"
        tokens = []
        ids = []
        for username, email in users:
            self.request("POST", "/register", json={"username": username, "email": email, "password": password})
            login = self.request("POST", "/login", json={"email": email, "password": password}).json()
            tokens.append(login["access_token"])
            ids.append(login["user_id"])
        self.owner_token, self.reader_token = tokens
        self.owner_id, self.reader_id = ids
        return {"owner_id": self.owner_id, "reader_id": self.reader_id}

    def create_test_pdf(self) -> Path:
        text = """
CITDS 11 Test Document

Project codename: ALBA-17.

Official request. Action: review the PhD dissertation draft for candidate Anna Toth. Deadline: 2026-06-03.
Course obligation. Action: announce the Data Science exam dates. Deadline: 2026-06-05.
Invitation accepted for a three-day research trip. Action: book a hotel in Kosice for the three-day research trip. Deadline: 2026-06-10.
Five unanswered student emails about the database systems course. Action: answer five unanswered student emails about the database systems course.
The previous-trip documentation task was completed and closed.

Aggregate pilot statistics:
The average approval time in the aggregate pilot statistics was 3.4 days.
The median approval time was 3.0 days.
The total aggregate count was 25 pilot cases.

Browser history note:
Browser history and activity traces can provide contextual support only. They cannot create an action item by themselves.
""".strip()
        out = Path(tempfile.gettempdir()) / "citds_11_smoke_test.pdf"
        doc = fitz.open()
        page = doc.new_page()
        rect = fitz.Rect(50, 50, 550, 780)
        page.insert_textbox(rect, text, fontsize=11)
        doc.save(out)
        doc.close()
        return out

    def upload_owner_pdf(self):
        pdf = self.create_test_pdf()
        with pdf.open("rb") as f:
            response = self.request(
                "POST",
                "/upload",
                token=self.owner_token,
                files={"file": (pdf.name, f, "application/pdf")},
                data={"permission_type": "Owner"},
            )
        data = response.json()
        self.doc_id = data["document_id"]
        return data

    def ask(self, question: str, token: str) -> Dict[str, Any]:
        response = self.request("POST", "/ask", token=token, data={"question": question})
        return response.json()

    def owner_primary_question(self):
        data = self.ask("What is the project codename?", self.owner_token)
        answer = data.get("answer", "")
        if "ALBA-17" not in answer:
            raise SmokeFailure(f"Expected ALBA-17 in answer, got: {answer}")
        roles = data.get("source_role_summary", {})
        if roles.get("primary", 0) < 1:
            raise SmokeFailure(f"Expected primary source role, got: {roles}")
        return {"answer": answer, "roles": roles}

    def controlled_failure(self):
        data = self.ask("What color is the deployment server rack?", self.owner_token)
        answer = data.get("answer", "")
        if "cannot be found" not in answer.lower():
            raise SmokeFailure(f"Expected controlled failure, got: {answer}")
        return {"answer": answer}

    def set_visibility(self, mode: str):
        response = self.request(
            "POST",
            "/documents/update-permission",
            token=self.owner_token,
            data={"doc_id": self.doc_id, "new_perm": mode},
        )
        data = response.json()
        expected = "Private" if mode == "Owner" else mode
        if data.get("visibility") != expected:
            raise SmokeFailure(f"Expected visibility {expected}, got {data}")
        return data

    def aggregate_other_user(self):
        self.set_visibility("Aggregate")
        data = self.ask("What was the average approval time in the aggregate pilot statistics?", self.reader_token)
        answer = data.get("answer", "")
        if "3.4" not in answer:
            raise SmokeFailure(f"Expected aggregate answer with 3.4, got: {answer}")
        roles = data.get("source_role_summary", {})
        if roles.get("aggregate-only", 0) < 1:
            raise SmokeFailure(f"Expected aggregate-only role, got: {roles}")
        raw_sources = json.dumps(data.get("sources", []))
        if "Project codename: ALBA-17" in raw_sources:
            raise SmokeFailure("Aggregate-only source leaked raw codename content")
        return {"answer": answer, "roles": roles}

    def metadata_other_user(self):
        self.set_visibility("Metadata")
        data = self.ask("What is the project codename?", self.reader_token)
        answer = data.get("answer", "")
        if "ALBA-17" in answer:
            raise SmokeFailure(f"Metadata-only leaked codename: {answer}")
        governance = data.get("governance", {})
        if not governance.get("metadata_only_doc_ids"):
            raise SmokeFailure(f"Expected metadata_only_doc_ids, got: {governance}")
        return {"answer": answer, "governance": governance}

    def policy_resolve_document(self):
        data = self.request("GET", f"/policy/resolve/document/{self.doc_id}", token=self.reader_token).json()
        resolution = data.get("resolution", {})
        if resolution.get("use_decision") != "metadata":
            raise SmokeFailure(f"Expected metadata resolution, got: {resolution}")
        return resolution

    def seed_action_list(self):
        return self.request("POST", "/evidence/demo-action-list", token=self.owner_token).json()

    def action_list(self):
        data = self.request("GET", "/evidence/action-list", token=self.owner_token).json()
        counts = data.get("counts", {})
        if counts.get("open", 0) < 1:
            raise SmokeFailure(f"Expected at least one open action item, got: {counts}")
        classified = data.get("classified_units", [])
        if not classified:
            raise SmokeFailure("Expected classified_units in action-list output")
        browser_primary = [u for u in classified if u.get("source_type") == "BrowserHistory" and u.get("role") == "primary"]
        if browser_primary:
            raise SmokeFailure("BrowserHistory became primary evidence")
        return {"counts": counts}

    def self_test(self):
        data = self.request("GET", "/citds/self-test", token=self.owner_token).json()
        score = data.get("summary", {}).get("score", 0)
        if score < 0.8:
            raise SmokeFailure(f"Expected CITDS self-test score >= 0.8, got {score}: {data}")
        return data.get("summary")

    def classifier(self):
        data = self.request(
            "POST",
            "/citds/classify",
            token=self.owner_token,
            json={
                "text": "Browser history: visited Kosice hotel search. This is contextual support only.",
                "task_intent": "current_action_list",
                "use_decision": "full",
                "use_llm": False,
            },
        ).json()
        role = data.get("classification", {}).get("source_role")
        if role != "contextual":
            raise SmokeFailure(f"Expected contextual classifier role, got {role}: {data}")
        return data.get("classification")

    def browser_history_upload(self):
        payload = [
            {
                "url": "https://example.com/hotel-kosice",
                "title": "Kosice hotel search",
                "visited_at": "2026-05-28T11:00:00",
                "visit_count": 2,
                "relation_key": "kosice-trip",
            }
        ]
        path = Path(tempfile.gettempdir()) / "citds_history.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with path.open("rb") as f:
            data = self.request(
                "POST",
                "/connectors/browser-history/upload",
                token=self.owner_token,
                files={"file": ("citds_history.json", f, "application/json")},
            ).json()
        if data.get("imported", 0) < 1:
            raise SmokeFailure(f"Expected browser history import, got: {data}")
        return data

    def admin_coverage(self):
        data = self.request("GET", "/admin/citds-coverage", token=self.owner_token).json()
        coverage = data.get("coverage", {})
        if not coverage.get("primary"):
            raise SmokeFailure(f"Expected primary coverage after tests, got: {coverage}")
        return data.get("summary")

    def implementation_status(self):
        data = self.request("GET", "/citds/implementation-status", token=self.owner_token).json()
        coverage = data.get("summary", {}).get("coverage", 0)
        if coverage < 0.95:
            raise SmokeFailure(f"Expected implementation coverage >= .95, got {coverage}: {data}")
        return data.get("summary")

    def run(self):
        self.step("backend health", lambda: self.request("GET", "/test-db").json())
        self.step("register and login two users", self.register_login_users)
        self.step("upload owner PDF", self.upload_owner_pdf)
        self.step("owner primary question", self.owner_primary_question)
        self.step("controlled failure", self.controlled_failure)
        self.step("aggregate-only other user", self.aggregate_other_user)
        self.step("metadata-only other user", self.metadata_other_user)
        self.step("document policy resolve", self.policy_resolve_document)
        self.step("seed evidence demo", self.seed_action_list)
        self.step("action-list reconstruction", self.action_list)
        self.step("CITDS self-test", self.self_test)
        self.step("classifier endpoint", self.classifier)
        self.step("browser-history upload", self.browser_history_upload)
        self.step("admin coverage", self.admin_coverage)
        self.step("implementation status", self.implementation_status)
        return self.results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default=os.getenv("INFOBANK_API", DEFAULT_API))
    parser.add_argument("--json-out", default="")
    args = parser.parse_args()

    smoke = Smoke(args.api)
    try:
        results = smoke.run()
    except Exception:
        results = smoke.results
        print("\n=== CITDS 11 SMOKE TEST FAILED ===")
        print(json.dumps(results, indent=2, ensure_ascii=False))
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        return 1

    print("\n=== CITDS 11 SMOKE TEST PASSED ===")
    print(json.dumps(results, indent=2, ensure_ascii=False))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
