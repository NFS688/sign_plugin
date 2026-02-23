from typing import (
    List, 
    Tuple, 
    Type, 
    Optional
    )

from src.plugin_system import (
    BasePlugin, 
    BaseAction, 
    BaseCommand, 
    register_plugin, 
    ComponentInfo, 
    ConfigField,
    ActionActivationType,
    generator_api,
    MaiMessages,
    BaseEventHandler,
    CustomEventHandlerResult,
    EventType
    )

from src.common.logger import get_logger
import datetime
import traceback
import random
import base64
import re
from .handle import (
    DataHandle,
    auto_resign_with_owned_card,
    get_target_user_id,
    get_target_nickname,
    register_resign_cards_to_shop,
)
from .draw import ImageGen, init_draw, get_background

logger = get_logger("sign")

DEFAULT_LEVEL = {
    "lv1": "警惕", 
    "lv2": "排斥", 
    "lv3": "可以交流", 
    "lv4": "一般", 
    "lv5": "是个好人", 
    "lv6": "好朋友", 
    "lv7": "可以分享小秘密", 
    "lv8": "恋人"}

class ImpressionInjectHandle(BaseEventHandler):

    event_type = EventType.POST_LLM
    handler_name = "impression_inject_handler"
    handler_description = "在 LLM 调用前自动注入好感度信息到 prompt"
    weight = 10
    intercept_message = True

    async def execute(
        self, message: MaiMessages | None
    ) -> Tuple[bool, bool, Optional[str], Optional[CustomEventHandlerResult], Optional[MaiMessages]]:
        """
                    执行好感度注入
        在 LLM 调用前，将好感度信息注入到 prompt 中
        """
        if not message or not message.llm_prompt:
            logger.info("未找到消息")
            return True, True, None, None, None
        
        userid = get_target_user_id(message)
        nickname = get_target_nickname(message)
        if not userid or not nickname:
            logger.info("未找到用户")
            return True, True, None, None, None

        db = DataHandle(userid=userid)
        userdata = await db.load_data()
        await db.close()
        if not userdata:
            logger.info("未找到用户数据")
            return True, True, None, None, None

        try: 
            level = ImageGen(userdata=userdata,next_score=self.get_config("components.next_score"),level_word=self.get_config("components.level_word"))
            levelw = level._get_level(userdata.get("level"))

            impression_str = f"[签到好感度]你对用户{nickname}的回复态度是: {levelw}\n"
            new_prompt = impression_str + message.llm_prompt
            message.modify_llm_prompt(new_prompt, suppress_warning=True)

            return True, True, None, None, message

        except Exception as e:
            logger.error(f"签到好感度注入失败: {e}")
            logger.error(traceback.format_exc())
            return True, True, None, None, None

class get_sign_background(BaseCommand):
    """获得签到背景图片"""
    command_name = "get_sign_background"
    command_description = "获得今天的签到背景，可通过@指定用户"
    command_pattern = r"^获得签到背景(?:\s*(?P<target>@<[^:<>]+:[^:<>]+>|@\S+))?$"

    @staticmethod
    def _extract_user_id_from_ref(text: str) -> Optional[str]:
        if not text:
            return None
        match = re.search(r"@<[^:<>]+:(?P<uid>[^:<>]+)>", text)
        if match:
            return str(match.group("uid"))
        return None

    @classmethod
    def _extract_user_id_from_segment(cls, segment) -> Optional[str]:
        if segment is None:
            return None

        seg_type = getattr(segment, "type", "")
        seg_data = getattr(segment, "data", None)

        if seg_type == "seglist" and isinstance(seg_data, list):
            for seg in seg_data:
                uid = cls._extract_user_id_from_segment(seg)
                if uid:
                    return uid
            return None

        if seg_type in {"at", "mention"}:
            if isinstance(seg_data, dict):
                for key in ("user_id", "uid", "qq", "id", "target", "account"):
                    value = seg_data.get(key)
                    if value not in (None, "", "all"):
                        return str(value)
            if isinstance(seg_data, str):
                # 兼容 data 里直接携带 uid 或 @<name:id> 格式
                uid = cls._extract_user_id_from_ref(seg_data)
                if uid:
                    return uid
                if seg_data.isdigit():
                    return seg_data
                bracket_uid = re.search(r"[（(](?P<uid>\d+)[）)]", seg_data)
                if bracket_uid:
                    return str(bracket_uid.group("uid"))
            return None

        if seg_type == "text" and isinstance(seg_data, str):
            return cls._extract_user_id_from_ref(seg_data)

        return None

    def _resolve_target_user_id(self) -> str:
        # 默认取发起者自己
        default_uid = str(self.message.message_info.user_info.user_id)

        # 优先使用正则命中的 target 参数
        target_ref = self.matched_groups.get("target", "")
        if target_ref:
            if uid := self._extract_user_id_from_ref(target_ref):
                return uid

        # 兜底：从整段文本中提取 @<name:id>
        text = self.message.processed_plain_text or ""
        if uid := self._extract_user_id_from_ref(text):
            return uid

        # 进一步兜底：从消息段结构中提取 at 用户
        if uid := self._extract_user_id_from_segment(getattr(self.message, "message_segment", None)):
            return uid

        return default_uid

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        userid = self._resolve_target_user_id()
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        try:
            img_bytes = await get_background(userid, today)
            
            if img_bytes:
                b64_img = base64.b64encode(img_bytes).decode('utf-8')
                await self.send_image(b64_img)
                return True, "获取成功", True
            await self.send_text("未找到该用户今天的签到背景，请先完成今日签到")
            return False, "未找到签到背景", True
        except Exception as e:
            error = f"获取签到背景失败: {e}"
            logger.error(error)
            await self.send_text(error)
            return False, "获取失败", True

class Sign(BaseCommand):
    """签到"""
    command_name = "sign"
    command_description = "签到"
    command_pattern = r"^签到$"

    @staticmethod
    def _apply_sign_streak_bonus(base_coins: int, next_continuous_days: int) -> int:
        if next_continuous_days >= 7:
            bonus_rate = 0.15
        elif next_continuous_days >= 3:
            bonus_rate = 0.10
        else:
            bonus_rate = 0.0

        if bonus_rate <= 0:
            return base_coins

        bonus = int(base_coins * bonus_rate)
        return base_coins + bonus

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        
        await init_draw()

        userid = self.message.message_info.user_info.user_id
        nickname = self.message.message_info.user_info.user_nickname

        add_coins = random.randint(1, 50)
        add_impression = round(random.uniform(0, 1), 2)
        
        wallet_name = self.get_config("components.wallet_name", "麦币")

        self.datahandle = DataHandle(
            userid=userid, 
            add_coins=add_coins, 
            add_impression=add_impression,
            next_score=self.get_config("components.next_score",25))
        
        userdata = await self.datahandle.load_data()
        try:
            if self.datahandle._is_today():
                await self.send_text("你今天已经签过到啦！")
                self.image = ImageGen(userdata=userdata)
                img_bytes = await self.image._image_cache()
                if img_bytes:
                    b64_img = base64.b64encode(img_bytes).decode('utf-8')
                    await self.send_image(b64_img)
                return True, "签到成功", True

            # 若已断签则自动尝试消耗已有补签卡补签（静默，不做提示）
            await auto_resign_with_owned_card(str(userid))
            userdata = await self.datahandle.load_data()

            next_continuous_days = 1
            if self.datahandle._is_continuous():
                next_continuous_days = int((userdata or {}).get("continuous_days", 0)) + 1
            add_coins = self._apply_sign_streak_bonus(add_coins, next_continuous_days)
            self.datahandle.add_coins = add_coins

            await self.datahandle._update_data()
        except Exception as e:
                await self.datahandle.close()
                logger.error(f"签到失败: {e}")
                return False,"签到失败", True

        userdata = await self.datahandle.load_data()
        await self.datahandle.close()
        self.image = ImageGen(
            userdata = userdata, 
            nickname = nickname, 
            wallet_name = wallet_name, 
            add_coins = add_coins, 
            add_impression = add_impression, 
            next_score= self.get_config("components.next_score", 25),
            level_word = self.get_config("components.level_word", DEFAULT_LEVEL),
            use_local_bg= self.get_config("components.use_local_bg", False))
        try:
            # 生成签到图片
            try:
                await self.send_image(base64.b64encode(await self.image._draw()).decode('utf-8'))
                return True, "签到成功", True
            except Exception as e:
                await self.send_text("签到成功但图片生成失败，请检查日志")
                logger.error(f"签到图片生成失败: {e}")
                logger.error(traceback.format_exc())
            return True, "签到成功", True
        except Exception as e:
            logger.error(f"签到失败: {e}")
            logger.error(traceback.format_exc())
            return False, f"签到失败: {e}", True

@register_plugin # 注册插件
class SignPlugin(BasePlugin):
    """签到插件"""

    plugin_name = "sign_plugin"
    enable_plugin = True  # 启用插件
    dependencies = []  # 插件依赖列表
    python_dependencies = ["aiosqlite"]  # Python依赖列表
    config_file_name = "config.toml"  # 配置文件名
    config_schema = {
        "plugin": {
            "version": ConfigField(type=str, default="0.0.1", description="插件版本号"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件")
        },
        "components": {
            "wallet_name": ConfigField(type=str, default="麦币", description="货币名称"),
            "enable_impression_replyer": ConfigField(type=bool, default=False, description="启用好感度影响回复 (启用后bot的回复将受到签到好感的的影响)"),
            "level_word": ConfigField(type=dict,default=DEFAULT_LEVEL, description="好感等级 (总共8级)"),
            "next_score": ConfigField(type=float, default=25, description="每升一个好感等级需要的好感度"),
            "use_local_bg":ConfigField(type=bool, default=False, description="使用本地图库作为签到背景 (请将图片放在插件目录下的resources/custombg目录)"),
            "resign_card_primary_price": ConfigField(type=int, default=100, description="初级补签卡价格"),
            "resign_card_intermediate_price": ConfigField(type=int, default=300, description="中级补签卡价格"),
            "resign_card_advanced_price": ConfigField(type=int, default=1000, description="高级补签卡价格"),
        }
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:

        components = []

        if self.get_config("plugin.enabled", True):
            register_resign_cards_to_shop(
                primary_price=self.get_config("components.resign_card_primary_price", 100),
                intermediate_price=self.get_config("components.resign_card_intermediate_price", 300),
                advanced_price=self.get_config("components.resign_card_advanced_price", 1000),
            )
            components.append((Sign.get_command_info(),Sign))
            components.append((get_sign_background.get_command_info(),get_sign_background))

        if self.get_config("components.enable_impression_replyer", True):
            components.append((ImpressionInjectHandle.get_handler_info(),ImpressionInjectHandle))

        return components
