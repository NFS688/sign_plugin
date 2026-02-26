import os
from typing import Any, Dict, Optional

import aiosqlite

data_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
)

sign_data_dir = os.path.join(data_dir, "signdata")
wallet_data_dir = os.path.join(data_dir, "wallet")


class SignData:
    def __init__(self):
        if not os.path.exists(sign_data_dir):
            os.makedirs(sign_data_dir)
        self.db_path = os.path.join(sign_data_dir, "sign.db")
        self.conn = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.db_path)
        await self._init_db()

    async def _init_db(self):
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sign_data (
                uid INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                total_days INTEGER DEFAULT 0,
                last_sign TEXT DEFAULT '',
                continuous_days INTEGER DEFAULT 0,
                impression FLOAT DEFAULT 0.00,
                level INTEGER DEFAULT 0
            )
            """
        )
        await self.conn.execute(
            """
            DELETE FROM sign_data
            WHERE uid NOT IN (
                SELECT MAX(uid) FROM sign_data GROUP BY user_id
            )
            """
        )
        await self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sign_data_user_id
            ON sign_data(user_id)
            """
        )
        await self.conn.commit()

    async def _get_user_data(self, user_id: str) -> Optional[Dict[str, Any]]:
        if not self.conn:
            await self.connect()

        async with self.conn.execute(
            """
            SELECT uid, user_id, total_days, last_sign, continuous_days, impression, level
            FROM sign_data
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

            columns = [
                "uid",
                "user_id",
                "total_days",
                "last_sign",
                "continuous_days",
                "impression",
                "level",
            ]
            return dict(zip(columns, row))

    async def _ensure_user_data(self, user_id: str):
        if not self.conn:
            await self.connect()
        await self.conn.execute(
            "INSERT OR IGNORE INTO sign_data (user_id) VALUES (?)",
            (user_id,),
        )
        await self.conn.commit()

    async def _update_user_data(self, user_id: str, **kwargs):
        if not self.conn:
            await self.connect()

        await self._ensure_user_data(user_id)

        if not kwargs:
            return

        update_fields = []
        values = []
        for key, value in kwargs.items():
            update_fields.append(f"{key} = ?")
            values.append(value)
        values.append(user_id)

        sql = f"UPDATE sign_data SET {', '.join(update_fields)} WHERE user_id = ?"
        await self.conn.execute(sql, values)
        await self.conn.commit()

    async def _get_ranking(self, limit: int = 10):
        if not self.conn:
            await self.connect()

        async with self.conn.execute(
            """
            SELECT user_id, impression FROM sign_data
            ORDER BY impression DESC LIMIT ?
            """,
            (limit,),
        ) as cursor:
            return await cursor.fetchall()

    async def _close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None


class WalletData:
    def __init__(self):
        if not os.path.exists(wallet_data_dir):
            os.makedirs(wallet_data_dir)
        self.db_path = os.path.join(wallet_data_dir, "wallet.db")
        self.conn = None

    async def connect(self):
        self.conn = await aiosqlite.connect(self.db_path)
        await self._init_db()

    async def _init_db(self):
        await self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_data (
                uid INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL UNIQUE,
                coins INTEGER DEFAULT 0
            )
            """
        )
        await self.conn.commit()

    async def _get_wallet_data(self, user_id: str) -> Optional[Dict[str, Any]]:
        if not self.conn:
            await self.connect()

        async with self.conn.execute(
            """
            SELECT uid, user_id, coins
            FROM wallet_data
            WHERE user_id = ?
            """,
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

            columns = ["uid", "user_id", "coins"]
            return dict(zip(columns, row))

    async def _update_wallet_data(self, user_id: str, coins: int):
        if not self.conn:
            await self.connect()

        await self.conn.execute(
            """
            INSERT INTO wallet_data (user_id, coins)
            VALUES (?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET coins = excluded.coins
            """,
            (user_id, coins),
        )
        await self.conn.commit()

    async def _close(self):
        if self.conn:
            await self.conn.close()
            self.conn = None
