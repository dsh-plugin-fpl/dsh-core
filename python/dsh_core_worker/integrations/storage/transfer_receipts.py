from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


class TransferReceipt(Base):
    __tablename__ = "integration_transfer_receipts"
    id: Mapped[str] = mapped_column(String(240), primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)
