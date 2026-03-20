CREATE DATABASE IF NOT EXISTS infobank_db 
CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE infobank_db;

CREATE TABLE IF NOT EXISTS users (
    id CHAR(36) PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    username VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR(255) PRIMARY KEY,
    owner_id CHAR(36) NOT NULL,
    file_path VARCHAR(512) NOT NULL,
    upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    default_access_rule ENUM('private', 'anonymous_agg') DEFAULT 'private',
    FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS keywords (
    id INT AUTO_INCREMENT PRIMARY KEY,
    word VARCHAR(100) UNIQUE NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS document_keywords (
    document_id VARCHAR(255) NOT NULL,
    keyword_id INT NOT NULL,
    PRIMARY KEY (document_id, keyword_id),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY (keyword_id) REFERENCES keywords(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS document_chunks (
    id CHAR(36) PRIMARY KEY,
    document_id VARCHAR(255) NOT NULL,
    chunk_index INT NOT NULL,
    text_content TEXT NOT NULL,
    vector_id VARCHAR(255) NOT NULL, 
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS document_permissions (
    id CHAR(36) PRIMARY KEY,
    document_id VARCHAR(255) NOT NULL,
    granted_to_user_id CHAR(36) NOT NULL,
    granted_by_user_id CHAR(36) NOT NULL,
    permission_type ENUM('full_transfer', 'query_only', 'anonymous_agg') NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
    FOREIGN KEY (granted_to_user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (granted_by_user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;