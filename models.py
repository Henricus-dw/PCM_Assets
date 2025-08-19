from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class VodacomSubscription(Base):
    __tablename__ = "Vodacom_subscription"

    # new primary key column
    id = Column(Integer, primary_key=True, autoincrement=True)
    Name_ = Column(String(50))
    Surname_ = Column(String(50))
    Personnel_nr = Column(String(50))
    Company = Column(String(100))
    Client_Division = Column(String(100))
    Contract_Type = Column(String(50))
    Monthly_Costs = Column(Float)
    Contract_Term = Column(String(50))
    Sim_Card_Number = Column(String(50))
    Inception_Date = Column(DateTime)
    Termination_Date = Column(DateTime)
