from sqlalchemy import Column, Integer, String
from sqlalchemy import Column, Integer, String, Float, DateTime, func, UniqueConstraint, Text, Boolean, text

from database import Base


class VodacomSubscription(Base):
    __tablename__ = "Vodacom_subscription"

    id = Column(Integer, primary_key=True, autoincrement=True)
    Name_ = Column(String(50))
    Surname_ = Column(String(50))
    Personnel_nr = Column(String(50))
    Company = Column(String(100))
    Client_Division = Column(String(100))
    Contract_Type = Column(String(50))
    Monthly_Costs = Column(Float)
    VAT = Column(Float)  # ✅ Fixed
    Monthly_Cost_Excl_VAT = Column(Float)  # ✅ Fixed
    Contract_Term = Column(String(50))
    Sim_Card_Number = Column(String(50))
    Inception_Date = Column(DateTime)
    Termination_Date = Column(DateTime)
    due_upgrade = Column(String(250))
    created_at = Column(DateTime, server_default=func.now())


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vd_id = Column(Integer)
    Name_ = Column(String(250))
    Surname_ = Column(String(250))
    Personnel_nr = Column(String(250))
    Company = Column(String(250))
    Client_Division = Column(String(250))
    Device_Name = Column(String(250))
    Serial_Number = Column(String(250))
    Device_Description = Column(String(250))
    insurance = Column(String(10))
    created_at = Column(DateTime, server_default=func.now())


# ... your existing Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(100), nullable=True)      # New field
    surname = Column(String(100), nullable=True)   # New field
    created_at = Column(DateTime, server_default=func.now())
    is_admin = Column(Boolean, nullable=False,
                      server_default=text("0"), default=False)

    __table_args__ = (UniqueConstraint('email', name='uq_users_email'),)


class PendingUser(Base):
    __tablename__ = "pending_users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    # hashed, never plaintext
    password_hash = Column(String(255), nullable=False)
    name = Column(String(100), nullable=False)
    surname = Column(String(100), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint(
        'email', name='uq_pending_users_email'),)


# Edit requests go through admin approval before touching devices
class DeviceEditRequest(Base):
    __tablename__ = "device_edit_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Integer, nullable=False)
    requester_email = Column(String(255), nullable=False)
    # JSON blob of requested changes, e.g. {"Company": "PCM", "Device_Name": "iPhone 15"}
    changes_json = Column(Text, nullable=False)
    # pending | approved | denied
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, server_default=func.now())
    processed_by = Column(Integer, nullable=True)  # user id of approver
    processed_at = Column(DateTime, nullable=True)


class ContractEditRequest(Base):
    __tablename__ = "contract_edit_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    contract_id = Column(Integer, nullable=False)
    requester_email = Column(String(255), nullable=False)
    changes_json = Column(Text, nullable=False)
    # pending|approved|denied
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, server_default=func.now())
    processed_by = Column(Integer, nullable=True)
    processed_at = Column(DateTime, nullable=True)


class PastDeviceOwners(Base):
    __tablename__ = "Past_device_owners"

    # using d_id as PK for SQLite only
    d_id = Column(Integer, primary_key=True, index=True)

    Name_ = Column(String(250))
    Surname_ = Column(String(250))
    Personnel_nr = Column(String(250))
    Company = Column(String(250))
    Client_Division = Column(String(250))

    created_at = Column(DateTime, server_default=func.now())
