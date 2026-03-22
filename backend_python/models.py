from sqlalchemy import Column, String, Integer, Text, Enum, ForeignKey, TIMESTAMP, func
from sqlalchemy.orm import relationship
from database import Base
import enum

class PermissionType(str, enum.Enum):
    Owner = 'Owner'
    Reader = 'Reader'
    Aggregate = 'Aggregate'

class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    username = Column(String(100), nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now())

class Document(Base):
    __tablename__ = "documents"
    id = Column(String(255), primary_key=True)
    file_path = Column(String(512), nullable=False)
    upload_date = Column(TIMESTAMP, server_default=func.now())
    
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