
import aiosqlite

class Database:
    _instance = None
    def __new__(self, *args, **kwargs):
        if self._instance is None:
            self._instance = super().__new__(self, *args, **kwargs)
        return self._instance
    
    @classmethod
    async def create(cls):
        # if not os.path.isfile("downloads.db"):
        conn = await aiosqlite.connect("downloads.db")
        await conn.execute("CREATE TABLE IF NOT EXISTS downloads (date DATETIME, user_id STRING, url STRING, video BOOLEAN, file_name STRING, file_size BIGINT, title STRING)")
        await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS url_and_type ON downloads (url, video)")
        await conn.commit()
        inst = cls()
        inst._conn = conn
        return inst

    async def find_url(self, url, video = False):
        cursor = await self._conn.execute("SELECT date, file_name, file_size, title FROM downloads WHERE url = ? and video = ?", [url, int(video)])
        fetch_data = await cursor.fetchone()
        return {
            "date" : datetime.strptime(fetch_data[0][:10], "%Y-%m-%d").date(),
            "file_name" : fetch_data[1],
            "file_size" : fetch_data[2],
            "title" : fetch_data[3],
        } if fetch_data else None

    async def save(self, on_date, user_id, url, video, file_name, file_size, title):
        await self._conn.execute("INSERT INTO downloads(date, user_id, url, video, file_name, file_size, title) VALUES(?, ?, ?, ?, ?, ?, ?)",
            [on_date, user_id, url, video, file_name, file_size, title]
        )
        await self._conn.commit()
