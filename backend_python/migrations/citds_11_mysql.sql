-- CITDS 11 MySQL migration helper
-- Run this manually if your existing development database was created before the CITDS 11 branch.
-- The app still uses SQLAlchemy create_all for new tables, but MySQL ENUMs and existing columns may need explicit updates.

-- 1) user_document_permission: allow Metadata if the table already exists with the old enum.
ALTER TABLE user_document_permission
MODIFY permission_type ENUM('Owner', 'Reader', 'Aggregate', 'Metadata') NOT NULL;

-- 2) documents: make sure visibility exists and can store governance visibility modes.
ALTER TABLE documents
MODIFY visibility VARCHAR(50) DEFAULT 'Private';

-- 3) evidence_units: create if missing.
CREATE TABLE IF NOT EXISTS evidence_units (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    source_type ENUM('Email', 'BrowserHistory', 'Calendar', 'ActivityTrace', 'DocumentNote', 'Other') NOT NULL DEFAULT 'Other',
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    source_timestamp DATETIME NULL,
    thread_id VARCHAR(255) NULL,
    relation_key VARCHAR(255) NULL,
    metadata_json TEXT NULL,
    created_at DATETIME NULL,
    INDEX ix_evidence_units_user_id (user_id),
    INDEX ix_evidence_units_thread_id (thread_id),
    INDEX ix_evidence_units_relation_key (relation_key),
    CONSTRAINT fk_evidence_units_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 4) policy_rules: create if missing.
CREATE TABLE IF NOT EXISTS policy_rules (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    owner_user_id VARCHAR(36) NOT NULL,
    target_type VARCHAR(50) NOT NULL,
    target_id VARCHAR(255) NOT NULL,
    purpose VARCHAR(100) NOT NULL DEFAULT 'any',
    access_mode ENUM('Full', 'Aggregate', 'Metadata', 'Deny') NOT NULL DEFAULT 'Full',
    valid_from DATETIME NULL,
    valid_until DATETIME NULL,
    created_at DATETIME NULL,
    INDEX ix_policy_rules_owner_user_id (owner_user_id),
    INDEX ix_policy_rules_target_id (target_id),
    CONSTRAINT fk_policy_rules_owner_user_id FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 5) connector_accounts: create if missing.
CREATE TABLE IF NOT EXISTS connector_accounts (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    provider VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'connected',
    token_json TEXT NOT NULL,
    metadata_json TEXT NULL,
    created_at DATETIME NULL,
    updated_at DATETIME NULL,
    INDEX ix_connector_accounts_user_id (user_id),
    INDEX ix_connector_accounts_provider (provider),
    CONSTRAINT fk_connector_accounts_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 6) sanity checks.
SELECT 'documents visibility modes' AS check_name, visibility, COUNT(*) AS count
FROM documents
GROUP BY visibility;

SELECT 'policy rules count' AS check_name, COUNT(*) AS count
FROM policy_rules;

SELECT 'evidence units count' AS check_name, COUNT(*) AS count
FROM evidence_units;

SELECT 'connector accounts count' AS check_name, COUNT(*) AS count
FROM connector_accounts;
