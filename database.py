from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# URL koneksi menggunakan kredensial Anda
SQLALCHEMY_DATABASE_URL = "postgresql://postgres:postgree@localhost:5433/sistem_kinerja"

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()