from config import DB_PATH
from inex.store.database import Store

store = Store(DB_PATH)
store.clear()
store.close()
print("Database flushed.")
