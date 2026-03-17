from tools.database import InExTool
from config import DB_PATH

db = InExTool(DB_PATH)
print("Tables created successfully")