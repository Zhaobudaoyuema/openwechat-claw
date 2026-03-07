from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    token: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    # open           — discoverable, anyone can send first message
    # friends_only   — hidden from discovery, only accepted friends can message
    # do_not_disturb — hidden from discovery, nobody can message (even friends)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"
    # ix_msg_to_created: covers the primary GET /messages query (to_id filter + created_at order)
    # ix_msg_from:       covers the optional from_id filter
    __table_args__ = (
        Index("ix_msg_to_created", "to_id", "created_at"),
        Index("ix_msg_from", "from_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Nullable: system messages have no sender
    from_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    to_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # chat           — normal message between friends
    # friend_request — first message from a stranger (pending friendship)
    # system         — server-generated event notification
    msg_type: Mapped[str] = mapped_column(String(16), nullable=False, default="chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Friendship(Base):
    """
    One row per user pair; user_a_id is always the smaller id.

    status:
      pending  — initiated_by sent first message, waiting for the other to reply
      accepted — the other party replied, mutual friendship established
      blocked  — blocked_by has blocked the other; no messages allowed
    """
    __tablename__ = "friendships"
    # ix_friendship_a/b_status: covers GET /friends queries that filter by user id + status
    __table_args__ = (
        UniqueConstraint("user_a_id", "user_b_id", name="uq_friendship"),
        Index("ix_friendship_a_status", "user_a_id", "status"),
        Index("ix_friendship_b_status", "user_b_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_a_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    user_b_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    initiated_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    blocked_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Stats(Base):
    """Global counters for /stats endpoint (e.g. total_messages)."""
    __tablename__ = "stats"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
