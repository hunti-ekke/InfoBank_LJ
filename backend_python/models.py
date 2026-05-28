from sqlalchemy import Column, String, Integer, Text, Enum, ForeignKey, TIMESTAMP, DateTime, func
from sqlalchemy.orm import relationship
from database import Base
import enum
import datetime

class PermissionType(str, enum.Enum):
    Owner = 'Owner'
    Reader = 'Reader'
    Aggregate = 'Aggregate'
    Metadata = 'Metadata'

class EvidenceSourceType(str, enum.Enum):
    Email = 'Email'
    BrowserHistory = 'BrowserHistory'
    Calendar = 'Calendar'
    ActivityTrace = 'ActivityTrace'
    DocumentNote = 'DocumentNote'
    Other = 'Other'

class PolicyAccessMode(str, enum.Enum):
    Full = 'Full'
    Aggregate = 'Aggregate'
    Metadata = 'Metadata'
    Deny = 'Deny'

class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    username = Column(String(100), nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())
    full_name = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)

class Document(Base):
    __tablename__ = "documents"
    id = Column(String(255), primary_key=True)
    file_path = Column(String(512), nullable=False)
    upload_date = Column(TIMESTAMP, server_default=func.now())
    visibility = Column(String(50), default="Private") 
    
    permissions = relationship("UserDocumentPermission", back_populates="document")
    chunks = relationship("DocumentChunk", back_populates="document")

class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, primary_key=True, autoincrement=True)
    word = Column(String(100), unique=True, nullable=False)

class DocumentKeyword(Base):
    __tablename__ = "document_keywords"
    document_id = Column(String(255), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    keyword_id = Column(Integer, ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True)

class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id = Column(String(36), primary_key=True)
    document_id = Column(String(255), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    text_content = Column(Text, nullable=False)
    vector_id = Column(String(255), nullable=False)
    
    document = relationship("Document", back_populates="chunks")

class UserDocumentPermission(Base):
    __tablename__ = "user_document_permission"
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    document_id = Column(String(255), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    permission_type = Column(Enum(PermissionType), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())
    
    document = relationship("Document", back_populates="permissions")

class EvidenceUnit(Base):
    """Owned evidence unit for the CITDS action-list scenario.

    This table lets the prototype ingest email messages, browser-history items,
    calendar events, and other activity traces without connecting to external
    private services. It is the implementation hook for the paper's owned email
    and browser-history reconstruction scenario.
    """

    __tablename__ = "evidence_units"
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source_type = Column(Enum(EvidenceSourceType), nullable=False, default=EvidenceSourceType.Other)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    source_timestamp = Column(DateTime, nullable=True)
    thread_id = Column(String(255), nullable=True, index=True)
    relation_key = Column(String(255), nullable=True, index=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class PolicyRule(Base):
    """Purpose-aware policy rule for InfoBank governance experiments.

    The current application still primarily uses document visibility, but these
    rules allow a document or evidence unit to be marked Full/Aggregate/Metadata/Deny
    for a purpose such as action reconstruction or grounded question answering.
    """

    __tablename__ = "policy_rules"
    id = Column(String(36), primary_key=True)
    owner_user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    target_type = Column(String(50), nullable=False)  # Document or EvidenceUnit
    target_id = Column(String(255), nullable=False, index=True)
    purpose = Column(String(100), nullable=False, default="any")
    access_mode = Column(Enum(PolicyAccessMode), nullable=False, default=PolicyAccessMode.Full)
    valid_from = Column(DateTime, nullable=True)
    valid_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class ConnectorAccount(Base):
    """OAuth connector account state for external evidence sources.

    Used by the Gmail connector. Tokens are stored as JSON for the prototype;
    production should encrypt this column or move it to a secrets vault.
    """

    __tablename__ = "connector_accounts"
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(50), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="connected")
    token_json = Column(Text, nullable=False)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(String(50), primary_key=True, index=True)
    user_id = Column(String(50), index=True)
    action = Column(String(50), index=True)
    target_id = Column(String(50), nullable=True)
    details = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
