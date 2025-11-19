import sqlite3
import os

DB_PATH = 'gre_practice.db'

def fix_database():
    if not os.path.exists(DB_PATH):
        print(f"Database file {DB_PATH} not found.")
        return

    print(f"Connecting to {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    try:
        # Check existing columns
        c.execute("PRAGMA table_info(user_progress)")
        columns = [info[1] for info in c.fetchall()]
        print(f"Current columns in user_progress: {columns}")
        
        if 'attempted_questions' not in columns:
            print("Column 'attempted_questions' is missing. Adding it...")
            # Add the new column with a default empty JSON list
            c.execute("ALTER TABLE user_progress ADD COLUMN attempted_questions TEXT DEFAULT '[]'")
            print("Successfully added 'attempted_questions' column.")
        else:
            print("Column 'attempted_questions' already exists.")
            
        conn.commit()
        print("Database fix completed successfully.")
        
    except Exception as e:
        print(f"An error occurred: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    fix_database()
