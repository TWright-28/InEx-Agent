from config import DB_PATH
from inex.store.database import Store

store = Store(DB_PATH)
store.reset()
store.close()
print("Database dropped and rebuilt.")
