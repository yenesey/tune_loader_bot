
import aiosqlite
from datetime import datetime
import logging


class Database:
    _instance = None
    
    downloads_attr = ["date", "user_id", "url", "video", "file_name", "file_size", "title", "artist", "channel", "delivered"]

    def __new__(self, *args, **kwargs):
        if self._instance is None:
            self._instance = super().__new__(self, *args, **kwargs)
        return self._instance
    
    @classmethod
    async def create(cls):
        # if not os.path.isfile("downloads.db"):
        conn = await aiosqlite.connect("downloads.db")
        await conn.execute("CREATE TABLE IF NOT EXISTS downloads (date DATETIME, user_id STRING, url STRING, video BOOLEAN, file_name STRING, file_size BIGINT, title STRING)")
        await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS `idx_downloads` ON `downloads` (`url`, `video`, `user_id`)")
        await conn.execute("CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER  NOT NULL PRIMARY KEY AUTOINCREMENT, user_id INTEGER, url STRING, keywords STRING, interval INTEGER, last_check DATETIME)")
        await conn.commit()
        inst = cls()
        inst._conn = conn
        return inst

    async def save_download(self, download):
        row = {}
        for key in self.downloads_attr:
            if not key in download:
                row[key] = None
                logging.info(f"save_download reconciliate: key {key} not pesent in given dictionary, appended empty one")
            else:
                row[key] = download[key]
        await self._conn.execute("""INSERT INTO 
            downloads(date, user_id, url, video, file_name, file_size, title, artist, channel, delivered) 
            VALUES(:date, :user_id, :url, :video, :file_name, :file_size, :title, :artist, :channel, :delivered)
            ON CONFLICT(url, video, user_id) DO UPDATE SET
            delivered = excluded.delivered""",
            row
        )
        await self._conn.commit()

    async def find_url(self, url, video = False, user_id = None):
        if user_id:
            cursor = await self._conn.execute("SELECT date, file_name, file_size, title, artist, channel, delivered FROM downloads WHERE url = ? and video = ? and user_id = ?", [url, int(video), user_id])
        else:
            cursor = await self._conn.execute("SELECT date, file_name, file_size, title, artist, channel, delivered FROM downloads WHERE url = ? and video = ?", [url, int(video)])
        fetch = await cursor.fetchone()
        return {
            "date" : datetime.strptime(fetch[0][:10], "%Y-%m-%d").date(),
            "file_name" : fetch[1],
            "file_size" : fetch[2],
            "title" : fetch[3],
            "artist" : fetch[4],
            "channel" : fetch[5],
            "delivered" : fetch[6],
        } if fetch else None

    async def get_subscriptions(self):
        cursor = await self._conn.execute("SELECT id, user_id, url, keywords, interval, last_check FROM subscriptions")
        fetch = await cursor.fetchall()
        subs = [{
            "id": ft[0],
            "user_id" : ft[1],
            "url": ft[2],
            "keywords": ft[3],
            "interval": ft[4],
            "last_check": datetime.strptime(ft[5], "%Y-%m-%d %H:%M:%S.%f") if ft[5] else None
        } for ft in fetch]
        return subs

    async def save_subscription(self, subscription):
        await self._conn.execute("""INSERT INTO subscriptions(id, user_id, url, keywords, interval, last_check) 
            VALUES(:id, :user_id, :url, :keywords, :interval, :last_check)
            ON CONFLICT(id) DO UPDATE SET
                keywords =  excluded.keywords,
                interval =  excluded.interval,
                last_check = excluded.last_check
            """,
            subscription
        )
        await self._conn.commit()
