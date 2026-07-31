import os

def get_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL não definido. Ex: postgresql://intelireg:intelireg@localhost:5432/intelireg")
    return url
