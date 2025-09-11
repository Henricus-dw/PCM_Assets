from sqlalchemy import Column, Integer, String, Float, DateTime, func, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


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
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint('email', name='uq_users_email'),)
