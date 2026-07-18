from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, func

from src.shared.db.database import Base


class MediaArtifact(Base):
    __tablename__ = "media_artifacts"

    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    raw_event_id = Column(String, ForeignKey("raw_events.id"), nullable=False, index=True)
    source_channel = Column(String, nullable=False, index=True)
    message_id = Column(String, nullable=True, index=True)
    media_type = Column(String, nullable=False, index=True)
    original_name = Column(String, nullable=True)
    mime_type = Column(String, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    sha256 = Column(String, nullable=True, index=True)
    storage_path = Column(String, nullable=True)
    source_url = Column(Text, nullable=True)
    wecom_media_id = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, default="received", index=True)
    extractor_name = Column(String, nullable=True)
    extractor_version = Column(String, nullable=True)
    extracted_text_path = Column(String, nullable=True)
    extracted_json_path = Column(String, nullable=True)
    artifact_metadata = Column(JSON, default=dict)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
