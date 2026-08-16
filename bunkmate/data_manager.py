import json
import os
import tempfile
import glob
import zipfile
import shutil
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")

if MONGO_URI:
    from pymongo import MongoClient
    client = MongoClient(MONGO_URI)
    try:
        # Some URIs include a default database, some don't.
        db = client.get_database()
    except Exception:
        db = client.get_database("bunkmate_db")
    users_col = db["users"]
    banned_col = db["banned_users"]
else:
    client = None

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
BANNED_DIR = os.path.join(SCRIPT_DIR, "banned_data")
BANNED_FILE = os.path.join(DATA_DIR, "banned_users.txt")

if not client:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(BANNED_DIR, exist_ok=True)

DEFAULT_TARGET = 75
CURRENT_SCHEMA_VERSION = 1

def get_data_file(user_id: str) -> str:
    return os.path.join(DATA_DIR, f"user_{user_id}.json")

def migrate_data(data: dict) -> dict:
    """Migrate older data schemas to the current version."""
    schema_version = data.get("schema_version", 0)
    
    if schema_version < 1:
        data["schema_version"] = 1
        for subject_name, info in data.get("subjects", {}).items():
            for entry in info.get("history", []):
                if "date" in entry:
                    try:
                        dt = datetime.strptime(entry["date"], "%Y-%m-%d %H:%M")
                        entry["date"] = dt.isoformat()
                    except ValueError:
                        pass
    return data

def _ensure_integrity(data: dict) -> dict:
    if "target_percentage" not in data:
        data["target_percentage"] = DEFAULT_TARGET
    if "subjects" not in data:
        data["subjects"] = {}
    if "current_semester" not in data:
        data["current_semester"] = None
        
    for name, info in data["subjects"].items():
        if "present" not in info:
            info["present"] = 0
        if "absent" not in info:
            info["absent"] = 0
        if "cancelled" not in info:
            info["cancelled"] = 0
        if "history" not in info:
            info["history"] = []
        elif len(info["history"]) > 100:
            info["history"] = info["history"][-100:]
    return data

def load_data(user_id: str) -> dict:
    """Load saved attendance data from MongoDB or JSON file."""
    data = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "target_percentage": DEFAULT_TARGET,
        "subjects": {}
    }
    
    if client:
        doc = users_col.find_one({"user_id": user_id})
        if doc:
            doc.pop("_id", None)
            data = doc
    else:
        data_file = get_data_file(user_id)
        if os.path.exists(data_file):
            try:
                with open(data_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data = loaded
            except (json.JSONDecodeError, ValueError) as e:
                print(f"  ⚠ Saved data file is corrupted or empty ({e}). Starting fresh...")
            
    data = migrate_data(data)
    data = _ensure_integrity(data)
    return data

def save_data(data: dict, print_msg: bool = True, user_id: str = "") -> None:
    """Save attendance data to MongoDB or JSON file atomically."""
    data["user_id"] = user_id
    if client:
        users_col.update_one({"user_id": user_id}, {"$set": data}, upsert=True)
        if print_msg:
            print("  ✔ Data saved to MongoDB.")
    else:
        data_file = get_data_file(user_id)
        temp_fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(data_file), suffix=".tmp")
        try:
            with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
            os.replace(temp_path, data_file)
            if print_msg:
                print("  ✔ Data saved.")
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            print(f"  ⚠ Failed to save data: {e}")
            raise e

def get_all_users() -> list:
    """Returns a list of all user data dicts"""
    if client:
        users = list(users_col.find({}))
        for u in users:
            u.pop("_id", None)
        return users
    else:
        users = []
        for f in glob.glob(os.path.join(DATA_DIR, "user_*.json")):
            uid = os.path.basename(f).replace("user_", "").replace(".json", "")
            users.append(load_data(uid))
        return users

def delete_user(user_id: str) -> bool:
    if client:
        result = users_col.delete_one({"user_id": user_id})
        banned_col.delete_one({"user_id": user_id})
        return result.deleted_count > 0
    else:
        deleted = False
        f1 = get_data_file(user_id)
        f2 = os.path.join(BANNED_DIR, f"user_{user_id}.json")
        if os.path.exists(f1):
            os.remove(f1)
            deleted = True
        if os.path.exists(f2):
            os.remove(f2)
            deleted = True
        return deleted

def is_banned(user_id: str) -> bool:
    if client:
        return banned_col.find_one({"user_id": user_id}) is not None
    else:
        if not os.path.exists(BANNED_FILE): return False
        with open(BANNED_FILE, "r") as f:
            return user_id in f.read().splitlines()

def ban_user(user_id: str):
    if client:
        user_data = users_col.find_one({"user_id": user_id})
        banned_doc = {"user_id": user_id}
        if user_data:
            user_data.pop("_id", None)
            banned_doc["data"] = user_data
            users_col.delete_one({"user_id": user_id})
        banned_col.update_one({"user_id": user_id}, {"$set": banned_doc}, upsert=True)
    else:
        with open(BANNED_FILE, "a") as f:
            f.write(user_id + "\n")
        f1 = get_data_file(user_id)
        f2 = os.path.join(BANNED_DIR, f"user_{user_id}.json")
        if os.path.exists(f1):
            shutil.move(f1, f2)

def unban_user(user_id: str):
    if client:
        banned_doc = banned_col.find_one({"user_id": user_id})
        if banned_doc and "data" in banned_doc:
            users_col.update_one({"user_id": user_id}, {"$set": banned_doc["data"]}, upsert=True)
        banned_col.delete_one({"user_id": user_id})
    else:
        if os.path.exists(BANNED_FILE):
            with open(BANNED_FILE, "r") as f:
                banned = f.read().splitlines()
            if user_id in banned:
                banned.remove(user_id)
                with open(BANNED_FILE, "w") as f:
                    for b in banned:
                        f.write(b + "\n")
        f1 = get_data_file(user_id)
        f2 = os.path.join(BANNED_DIR, f"user_{user_id}.json")
        if os.path.exists(f2):
            shutil.move(f2, f1)

def kill_switch():
    if client:
        users_col.drop()
        banned_col.drop()
    else:
        for f in glob.glob(os.path.join(DATA_DIR, "*")):
            if os.path.isfile(f): os.remove(f)
        for f in glob.glob(os.path.join(BANNED_DIR, "*")):
            if os.path.isfile(f): os.remove(f)

def backup_data(zip_path: str):
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        if client:
            users = list(users_col.find({}))
            for u in users:
                u.pop("_id", None)
                uid = u.get("user_id")
                zipf.writestr(f"user_{uid}.json", json.dumps(u, indent=4))
        else:
            for root, dirs, files in os.walk(DATA_DIR):
                for file in files:
                    if file.endswith(".json") or file.endswith(".txt"):
                        if file != os.path.basename(zip_path):
                            zipf.write(os.path.join(root, file), file)
