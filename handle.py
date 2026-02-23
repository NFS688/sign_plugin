import datetime
from typing import Optional

from src.common.logger import get_logger

from .database import SignData, WalletData

logger = get_logger("sign_handle")


def get_target_user_id(event_data) -> Optional[str]:
    """获取目标用户ID"""
    user_id = ""

    try:
        if hasattr(event_data, "stream_id") and event_data.stream_id:
            try:
                from src.chat.message_receive.chat_stream import get_chat_manager

                chat_manager = get_chat_manager()
                target_stream = chat_manager.get_stream(event_data.stream_id)

                if target_stream and target_stream.context:
                    last_message = target_stream.context.get_last_message()
                    if last_message:
                        if hasattr(last_message, "reply") and last_message.reply:
                            raw_user_id = last_message.reply.message_info.user_info.user_id
                            user_id = str(raw_user_id)
                        else:
                            raw_user_id = last_message.message_info.user_info.user_id
                            user_id = str(raw_user_id)
            except Exception as e:
                logger.warning(f"从ChatStream获取用户ID失败: {e}")

        if not user_id:
            if (
                hasattr(event_data, "reply")
                and event_data.reply
                and hasattr(event_data.reply, "user_id")
            ):
                user_id = str(event_data.reply.user_id)
            elif hasattr(event_data, "message_base_info"):
                user_id = str(event_data.message_base_info.get("user_id", ""))
            elif hasattr(event_data, "user_id"):
                user_id = str(event_data.user_id)
            elif hasattr(event_data, "user_info") and hasattr(event_data.user_info, "user_id"):
                user_id = str(event_data.user_info.user_id)
    except Exception as e:
        logger.error(f"提取用户ID异常: {e}")

    return user_id


def get_target_nickname(event_data) -> Optional[str]:
    """获取目标用户昵称"""
    nickname = ""

    try:
        if hasattr(event_data, "stream_id") and event_data.stream_id:
            try:
                from src.chat.message_receive.chat_stream import get_chat_manager

                chat_manager = get_chat_manager()
                target_stream = chat_manager.get_stream(event_data.stream_id)

                if target_stream and target_stream.context:
                    last_message = target_stream.context.get_last_message()
                    if last_message:
                        if hasattr(last_message, "reply") and last_message.reply:
                            raw_nickname = last_message.reply.message_info.user_info.user_nickname
                            nickname = str(raw_nickname)
                        else:
                            raw_nickname = last_message.message_info.user_info.user_nickname
                            nickname = str(raw_nickname)
            except Exception as e:
                logger.warning(f"从ChatStream获取用户昵称失败: {e}")

        if not nickname:
            if (
                hasattr(event_data, "reply")
                and event_data.reply
                and hasattr(event_data.reply, "user_nickname")
            ):
                nickname = str(event_data.reply.user_nickname)
            elif hasattr(event_data, "message_base_info"):
                nickname = str(event_data.message_base_info.get("user_nickname", ""))
            elif hasattr(event_data, "user_nickname"):
                nickname = str(event_data.user_nickname)
            elif hasattr(event_data, "user_info") and hasattr(event_data.user_info, "user_nickname"):
                nickname = str(event_data.user_info.user_nickname)
    except Exception as e:
        logger.error(f"提取用户昵称异常: {e}")

    return nickname


class DataHandle:
    def __init__(
        self,
        userid: str = "0",
        add_coins: Optional[int] = 0,
        add_impression: Optional[float] = 0,
        next_score: Optional[float] = 25,
    ):
        self.userid = userid
        self.sign_db = SignData()
        self.wallet_db = WalletData()
        self.userdata = None
        self.add_coins = add_coins
        self.add_impression = add_impression
        self.next_score = next_score

    async def load_data(self):
        sign_data = await self.sign_db._get_user_data(self.userid)
        wallet_data = await self.wallet_db._get_wallet_data(self.userid)

        if not sign_data and not wallet_data:
            self.userdata = None
            return self.userdata

        merged_data = {}
        if sign_data:
            merged_data.update(sign_data)
        else:
            merged_data["user_id"] = self.userid

        merged_data["coins"] = 0
        if wallet_data:
            merged_data["coins"] = wallet_data.get("coins", 0) or 0

        self.userdata = merged_data
        return self.userdata

    async def close(self):
        if self.sign_db:
            await self.sign_db._close()
        if self.wallet_db:
            await self.wallet_db._close()

    def _update_impression(self, add):
        try:
            return self.userdata.get("impression", 0.00) + add
        except Exception:
            return add

    def _update_coins(self, add):
        try:
            return self.userdata.get("coins", 0) + add
        except Exception:
            return add

    def _update_total_days(self):
        try:
            return self.userdata.get("total_days") + 1
        except Exception:
            return 1

    def _update_continuous(self):
        try:
            if self._is_continuous():
                return self.userdata.get("continuous_days") + 1
            return 1
        except Exception:
            return 1

    def _update_level(self):
        try:
            impression = self._update_impression(self.add_impression)
            level = int(impression / self.next_score) + 1
            logger.debug(
                f"当前好感度{impression} \n升级所需好感度{self.next_score} \n当前好感度等级{level}"
            )
            if level > 8:
                level = 8
            return level
        except Exception as e:
            logger.error(f"更新好感度出错: {e}")
            return 1

    def _update_last_sign(self):
        if not self._is_today():
            return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _is_continuous(self):
        try:
            yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime(
                "%Y-%m-%d"
            )
            last_sign = self.userdata.get("last_sign")
            if last_sign and str(last_sign).startswith(yesterday_str):
                return True
            return False
        except Exception:
            return False

    def _is_today(self):
        try:
            today_str = datetime.datetime.now().strftime("%Y-%m-%d")
            last_sign = self.userdata.get("last_sign")
            if last_sign and str(last_sign).startswith(today_str):
                return True
            return False
        except Exception:
            return False

    async def _update_data(self):
        try:
            new_coins = self._update_coins(self.add_coins)
            await self.sign_db._update_user_data(
                self.userid,
                total_days=self._update_total_days(),
                last_sign=self._update_last_sign(),
                continuous_days=self._update_continuous(),
                impression=self._update_impression(self.add_impression),
                level=self._update_level(),
            )
            await self.wallet_db._update_wallet_data(self.userid, new_coins)
        except Exception as e:
            logger.error(f"更新用户数据出现错误: {e}")


def _parse_sign_date(raw_value) -> Optional[datetime.date]:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    date_text = text[:10]
    try:
        return datetime.date.fromisoformat(date_text)
    except ValueError:
        return None


async def use_resign_card(user_id: str, card_name: str, max_break_days: int) -> tuple[bool, str]:
    sign_db = SignData()
    try:
        user_data = await sign_db._get_user_data(user_id)
        if not user_data:
            return False, "你还没有签到记录，暂时无法使用补签卡。"

        last_sign_date = _parse_sign_date(user_data.get("last_sign"))
        if last_sign_date is None:
            return False, "当前签到记录异常，无法使用补签卡。"

        today = datetime.date.today()
        missed_days = (today - last_sign_date).days - 1
        if missed_days <= 0:
            return False, "你当前没有断签，无需使用补签卡。"
        if missed_days > max_break_days:
            return False, f"连续{missed_days}未签到！\n{card_name}最多仅支持补签 {max_break_days} 天。"

        previous_streak = int(user_data.get("continuous_days") or 0)
        if previous_streak <= 0:
            return False, "当前连续签到记录为空，无法使用补签卡。"

        restored_last_sign = datetime.datetime.combine(
            today - datetime.timedelta(days=1),
            datetime.time(hour=23, minute=59, second=59),
        ).strftime("%Y-%m-%d %H:%M:%S")

        await sign_db._update_user_data(
            user_id,
            last_sign=restored_last_sign,
            continuous_days=previous_streak,
        )
        return (
            True,
            f"{card_name}使用成功，已恢复连续签到 {previous_streak} 天！",
        )
    except Exception as e:
        logger.error(f"使用补签卡失败: {e}")
        return False, "补签失败，请稍后再试。"
    finally:
        await sign_db._close()


async def auto_resign_with_owned_card(user_id: str) -> bool:
    try:
        from plugins.shop_plugin.database import ShopInventoryDB
    except Exception:
        return False

    sign_db = SignData()
    missed_days = 0
    try:
        user_data = await sign_db._get_user_data(user_id)
        if not user_data:
            return False
        last_sign_date = _parse_sign_date(user_data.get("last_sign"))
        if last_sign_date is None:
            return False
        today = datetime.date.today()
        missed_days = (today - last_sign_date).days - 1
    except Exception as e:
        logger.error(f"自动补签检查失败: {e}")
        return False
    finally:
        await sign_db._close()

    if missed_days <= 0 or missed_days > 3:
        return False

    candidates = []
    if missed_days <= 1:
        candidates.extend(
            [
                ("sign_resign_card_primary", "初级补签卡", 1),
                ("sign_resign_card_intermediate", "中级补签卡", 2),
                ("sign_resign_card_advanced", "高级补签卡", 3),
            ]
        )
    elif missed_days == 2:
        candidates.extend(
            [
                ("sign_resign_card_intermediate", "中级补签卡", 2),
                ("sign_resign_card_advanced", "高级补签卡", 3),
            ]
        )
    else:
        candidates.append(("sign_resign_card_advanced", "高级补签卡", 3))

    inventory_db = ShopInventoryDB()
    try:
        for item_key, card_name, max_break_days in candidates:
            quantity = await inventory_db.get_quantity(user_id, item_key)
            if quantity <= 0:
                continue

            removed = await inventory_db.remove_item(user_id, item_key, 1)
            if not removed:
                continue

            success, _ = await use_resign_card(
                user_id=user_id,
                card_name=card_name,
                max_break_days=max_break_days,
            )
            if success:
                return True

            await inventory_db.add_item(user_id, item_key, 1)
    except Exception as e:
        logger.error(f"自动补签执行失败: {e}")
        return False
    finally:
        await inventory_db.close()

    return False


def register_resign_cards_to_shop(
    primary_price: int = 100,
    intermediate_price: int = 300,
    advanced_price: int = 1000,
) -> None:
    try:
        from plugins.shop_plugin.shop_api import (
            ShopCategory,
            ShopItem,
            UseItemContext,
            UseItemResult,
            register_shop_class,
            register_shop_item,
        )
    except Exception as exc:
        logger.debug(f"商店 API 不可用，跳过补签卡注册: {exc}")
        return

    register_shop_class(
        ShopCategory(
            key="sign_shop",
            display_name="签到商店",
            provider="sign_plugin",
            sort_order=10,
        ),
        overwrite=True,
    )

    def make_handler(card_name: str, max_break_days: int):
        async def handler(context: UseItemContext) -> UseItemResult:
            success, message = await use_resign_card(
                user_id=context.user_id,
                card_name=card_name,
                max_break_days=max_break_days,
            )
            return UseItemResult(
                success=success,
                message=message,
                consume_count=1 if success else 0,
            )

        return handler

    cards = [
        (
            "sign_resign_card_primary",
            "初级补签卡",
            "补签1天",
            1,
            max(0, int(primary_price)),
        ),
        (
            "sign_resign_card_intermediate",
            "中级补签卡",
            "补签2天",
            2,
            max(0, int(intermediate_price)),
        ),
        (
            "sign_resign_card_advanced",
            "高级补签卡",
            "补签3天",
            3,
            max(0, int(advanced_price)),
        ),
    ]
    for key, name, description, max_break_days, price in cards:
        register_shop_item(
            ShopItem(
                key=key,
                name=name,
                price=price,
                description=description,
                category_key="sign_shop",
                category="sign_card",
                provider="sign_plugin",
                aliases=[name],
            ),
            use_handler=make_handler(name, max_break_days),
            overwrite=True,
        )
