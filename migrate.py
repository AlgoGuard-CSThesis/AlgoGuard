from config import get_config
from services.database_service import initialize_database, migration_status

if __name__ == "__main__":
    initialize_database()
    print(f"Database ready: {get_config().database_path}")
    for migration in migration_status():
        print(
            f"Migration {migration['version']}: {migration['name']} ({migration['applied_at']} UTC)"
        )
