import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()
def run_migration():
    """
    Connects to MySQL, ensures the _migrations table exists,
    and executes any pending .sql migration files in the 'migration' directory.
    """
    # 1. Establish Database Connection
    try:
        config = {
            'host': os.getenv('DB_HOST'),
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASSWORD'),
            'database': os.getenv('DB_NAME')
        }
        conn = mysql.connector.connect(**config)
        # dictionary=True returns rows as dicts: {'file_name': '001_initial.sql'}
        curse = conn.cursor(dictionary=True)
    except Exception as e:
        print("❌ Database connection failed:", e)
        return

    try:
        # 2. Create tracking table if it doesn't exist
        create_table_sql = """
            CREATE TABLE IF NOT EXISTS _migrations (
                id INT AUTO_INCREMENT PRIMARY KEY,
                file_name VARCHAR(255) NOT NULL UNIQUE,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        curse.execute(create_table_sql)

        # 3. Fetch executed migration files and extract file names as a set of strings
        curse.execute("SELECT file_name FROM _migrations")
        rows = curse.fetchall()

        # Extract only the string value of 'file_name' to avoid unhashable dict errors
        executed_files = {str(row['file_name']) for row in rows if 'file_name' in row}

        # 4. Check or create migration directory
        migration_dir = os.path.join(os.path.dirname(__file__), 'migration')
        if not os.path.exists(migration_dir):
            os.makedirs(migration_dir, exist_ok=True)
            print("📁 Created missing 'migration' directory. Add your .sql files here.")
            return

        # 5. Read and execute pending .sql files in order
        migration_files = sorted([f for f in os.listdir(migration_dir) if f.endswith('.sql')])

        for file_name in migration_files:
            if file_name not in executed_files:
                file_path = os.path.join(migration_dir, file_name)
                print(f"🔄 Executing migration: {file_name}")

                with open(file_path, 'r', encoding='utf-8') as sql_file:
                    sql_statements = sql_file.read()

                # Execute individual SQL statements split by semicolon
                for statement in sql_statements.split(';'):
                    if statement.strip():
                        curse.execute(statement)

                # Record execution in tracking table
                curse.execute("INSERT INTO _migrations (file_name) VALUES (%s)", (file_name,))
                conn.commit()
                print(f"✅ Migration successful: {file_name}")

    except Exception as e:
        conn.rollback()
        print("❌ Migration failed:", e)
    finally:
        # 6. Cleanly close database resources
        curse.close()
        conn.close()



if __name__ == "__main__":
    run_migration()