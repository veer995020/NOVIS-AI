import os
from pathlib import Path
from supabase import create_client, Client

PROJECT_DIR = r"C:\Users\Renewfy\Links\NOVIS"
dotenv_path = Path(PROJECT_DIR) / "supabase.env"

# --- 1. Load Environment Variables ---
encodings_to_try = ["utf-8", "utf-16", "utf-16-le", "utf-16-be", "cp1252"]
file_content_read = False

if dotenv_path.exists():
    for enc in encodings_to_try:
        try:
            with open(dotenv_path, "r", encoding=enc, errors="strict") as f:
                lines = f.readlines()
                if lines and any(line.strip() for line in lines):
                    for line in lines:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            key, val = line.split("=", 1)
                            os.environ[key.strip()] = val.strip("'\" ")
                    file_content_read = True
                    break
        except (UnicodeDecodeError, LookupError):
            continue

url: str = os.environ.get("SUPABASE_URL")
publishable_key: str = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_KEY")

if not url or not publishable_key:
    raise ValueError(f"❌ Verification Failed! Keys missing from {dotenv_path}")

# --- 2. Define the Client Globally ---
# This fixes the "name 'supabase' is not defined" error
supabase: Client = create_client(url, publishable_key)
print("🚀 Success! Supabase client initialized.")


# --- 3. Test Database Fetch Function ---
def test_database_fetch():
    try:
        # Note: Replace 'your_table_name' with an actual table name from your dashboard
        response = supabase.table("Main Table").select("*").limit(1).execute()
        print("📊 Database connection verified! Data sample:", response.data)
    except Exception as e:
        print("❌ Query failed:", str(e))


# --- 4. Script Entry Point ---
if __name__ == "__main__":
    test_database_fetch()
