import asyncio
import datetime
import io
import json
import os
import random
import re
import traceback
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict

import aiohttp
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from src.common.logger import get_logger
from src.config.config import MMC_VERSION

PLUGIN_DIR = os.path.dirname(__file__)
PLUGIN_VERSION = "0.0.1"

IMAGE_DIR = os.path.join(PLUGIN_DIR, "resources", "images")
LOCAL_BG_DIR = os.path.join(PLUGIN_DIR, "resources", "custombg")
FONT_DIR = os.path.join(PLUGIN_DIR, "resources", "fonts")

FONT_PATH_ZH = os.path.join(FONT_DIR, "zh_font.ttf")
FONT_PATH_EN = os.path.join(FONT_DIR, "en_font.ttf")

# 保障特殊字符和 emoji 的回退字体。
SYSTEM_LATIN_FONT_PATHS = (
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

SYSTEM_CJK_FONT_PATHS = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)

SYSTEM_EMOJI_FONT_PATHS = (
    r"C:\Windows\Fonts\seguiemj.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
)

BG_URL = "https://v2.xxapi.cn/api/random4kPic?type=acg"
FONT_URL_ZH = "https://ghproxy.net/https://github.com/ChisugaMaeka/Chisuga-Shotai/releases/download/v0.2.12/ChisugaShotai_Regular0.2.12.1.ttf"
FONT_URL_EN = "https://cdn.jsdelivr.net/gh/ItMarki/linja-waso@main/fonts/linja-waso-lili.ttf"

logger = get_logger("sign_draw")

_draw_initialized = False


def _sanitize_path_token(value: object, default: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    cleaned = re.sub(r"[^0-9A-Za-z_-]", "_", text)
    cleaned = cleaned.strip("_")
    return cleaned or default


def _join_image_path(filename: str) -> str:
    image_dir_abs = os.path.abspath(IMAGE_DIR)
    path = os.path.abspath(os.path.join(IMAGE_DIR, filename))
    if not path.startswith(image_dir_abs + os.sep):
        raise ValueError(f"非法图片路径: {filename}")
    return path


def _build_background_path(userid: object, date_text: object) -> str:
    safe_uid = _sanitize_path_token(userid)
    safe_date = _sanitize_path_token(date_text)
    return _join_image_path(f"background-{safe_uid}-{safe_date}.png")


def _build_sign_cache_path(userid: object, date_text: object) -> str:
    safe_uid = _sanitize_path_token(userid)
    safe_date = _sanitize_path_token(date_text)
    return _join_image_path(f"{safe_uid}-{safe_date}.png")


async def init_draw():
    global _draw_initialized
    if _draw_initialized:
        return

    os.makedirs(IMAGE_DIR, exist_ok=True)
    os.makedirs(FONT_DIR, exist_ok=True)
    os.makedirs(LOCAL_BG_DIR, exist_ok=True)

    async with aiohttp.ClientSession() as session:
        tasks = []
        if not check_font(FONT_PATH_ZH):
            tasks.append(download_font(session, FONT_URL_ZH, FONT_PATH_ZH))
        if not check_font(FONT_PATH_EN):
            tasks.append(download_font(session, FONT_URL_EN, FONT_PATH_EN))
        if tasks:
            await asyncio.gather(*tasks)

    _draw_initialized = True


def check_font(path):
    try:
        ImageFont.truetype(path, 10)
        return True
    except Exception:
        return False


def save_content(path, content):
    with open(path, "wb") as f:
        f.write(content)


def read_content(path):
    with open(path, "rb") as f:
        return f.read()


async def download_font(session, url, path):
    logger.info(f"正在下载字体: {url} -> {path}")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
        }
        async with session.get(url, headers=headers, timeout=180) as resp:
            if resp.status != 200:
                logger.error(f"字体下载失败, status={resp.status}")
                return

            content = await resp.read()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, save_content, path, content)

            if check_font(path):
                logger.info(f"字体下载并校验成功: {path}")
            else:
                logger.error(f"字体文件校验失败: {path}")
                try:
                    os.remove(path)
                except OSError:
                    pass
    except Exception as e:
        logger.error(f"字体下载出错: {e}")


async def get_background(userid, time):
    path = _build_background_path(userid, time)
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, read_content, path)
    except Exception as e:
        logger.error(f"获取签到背景失败: {e}")
        return None


class ImageGen:
    def __init__(
        self,
        userdata: dict,
        nickname: Optional[str] = "聊天用户",
        wallet_name: Optional[str] = "麦币",
        add_coins: Optional[int] = 0,
        add_impression: Optional[float] = 0,
        next_score: Optional[float] = 25,
        level_word: Optional[dict] = None,
        use_local_bg: Optional[bool] = False,
    ):
        self.userid = userdata.get("user_id")
        raw_nickname = str(nickname or "聊天用户")
        self.nickname = raw_nickname.replace("\r", " ").replace("\n", " ").strip() or "聊天用户"
        self.wallet_name = wallet_name
        self.impression = userdata.get("impression")
        self.coins = userdata.get("coins")
        self.add_impression = add_impression
        self.add_coins = add_coins
        self.last_sign = userdata.get("last_sign")
        self.total_days = userdata.get("total_days", 0) or 0
        self.continuous_days = userdata.get("continuous_days", 0) or 0
        self.level = userdata.get("level")
        self.next_score = next_score
        self.use_local_bg = use_local_bg

        self.level_word = level_word or {}
        self.get_level_word = self._get_level(self.level)
        self.today = datetime.datetime.now().strftime("%Y-%m-%d")

        self.avatar_data: Optional[bytes] = None
        self.bg_data: Optional[bytes] = None
        self._font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
        self._font_paths_cache: Optional[
            tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]
        ] = None

    async def _prepare_resources(self):
        async with aiohttp.ClientSession() as session:
            if self.use_local_bg:
                self.bg_data = await self._get_bg_local()
            else:
                self.bg_data = await self._get_bg(session)
            self.avatar_data = await self._get_avatar(session)

    def _get_font(self, path, size):
        key = (str(path), int(size))
        cached = self._font_cache.get(key)
        if cached is not None:
            return cached
        try:
            font = ImageFont.truetype(path, size)
        except Exception:
            font = ImageFont.load_default()
        self._font_cache[key] = font
        return font

    @staticmethod
    def _font_exists(path: str) -> bool:
        return bool(path and os.path.isfile(path))

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        for char in text:
            code = ord(char)
            if (
                0x4E00 <= code <= 0x9FFF
                or 0x3400 <= code <= 0x4DBF
                or 0xF900 <= code <= 0xFAFF
            ):
                return True
        return False

    @staticmethod
    def _contains_emoji(text: str) -> bool:
        for char in text:
            code = ord(char)
            if (
                0x1F000 <= code <= 0x1FAFF
                or 0x2600 <= code <= 0x27BF
                or 0x2300 <= code <= 0x23FF
            ):
                return True
        return False

    @staticmethod
    def _contains_ascii_alnum(text: str) -> bool:
        for char in text:
            if ("A" <= char <= "Z") or ("a" <= char <= "z") or char.isdigit():
                return True
        return False

    @staticmethod
    def _is_ascii_punct_cluster(text: str) -> bool:
        if not text:
            return False
        has_ascii = False
        for char in text:
            if ord(char) > 0x7F:
                return False
            has_ascii = True
            category = unicodedata.category(char)
            if not (category.startswith("P") or category.startswith("S") or char.isspace()):
                return False
        return has_ascii

    @staticmethod
    def _is_fullwidth_punct_cluster(text: str) -> bool:
        if not text:
            return False
        has_fullwidth = False
        for char in text:
            category = unicodedata.category(char)
            if not (category.startswith("P") or category.startswith("S")):
                return False
            if unicodedata.east_asian_width(char) not in ("W", "F", "A"):
                return False
            has_fullwidth = True
        return has_fullwidth

    def _dedupe_font_paths(self, paths) -> tuple[str, ...]:
        unique_paths = []
        seen = set()
        for path in paths:
            if not self._font_exists(path):
                continue
            normalized = os.path.normcase(os.path.abspath(path))
            if normalized in seen:
                continue
            seen.add(normalized)
            unique_paths.append(path)
        return tuple(unique_paths)

    def _collect_local_font_paths(self) -> tuple[str, ...]:
        preferred = [FONT_PATH_ZH, FONT_PATH_EN]
        extra_fonts = []
        preferred_norm = {os.path.normcase(os.path.abspath(p)) for p in preferred}
        try:
            for name in sorted(os.listdir(FONT_DIR)):
                if not name.lower().endswith((".ttf", ".otf", ".ttc")):
                    continue
                path = os.path.join(FONT_DIR, name)
                normalized = os.path.normcase(os.path.abspath(path))
                if normalized in preferred_norm:
                    continue
                extra_fonts.append(path)
        except OSError:
            pass
        return self._dedupe_font_paths(tuple(preferred + extra_fonts))

    @staticmethod
    def _split_text_clusters(text: str) -> list[str]:
        if not text:
            return []

        clusters: list[str] = []
        current = ""
        for char in text:
            code = ord(char)
            if not current:
                current = char
                continue

            last_code = ord(current[-1])
            if (
                unicodedata.combining(char)
                or code in (0x200D, 0xFE0E, 0xFE0F)
                or last_code == 0x200D
            ):
                current += char
                continue

            clusters.append(current)
            current = char

        if current:
            clusters.append(current)
        return clusters

    def _get_font_paths(
        self
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        if self._font_paths_cache is not None:
            return self._font_paths_cache
        local_font_paths = self._collect_local_font_paths()

        emoji_paths = self._dedupe_font_paths(
            SYSTEM_EMOJI_FONT_PATHS
            + (FONT_PATH_EN,)
            + local_font_paths
            + SYSTEM_LATIN_FONT_PATHS
            + SYSTEM_CJK_FONT_PATHS
        )
        cjk_paths = self._dedupe_font_paths(
            (FONT_PATH_ZH,)
            + local_font_paths
            + SYSTEM_CJK_FONT_PATHS
            + SYSTEM_LATIN_FONT_PATHS
        )
        latin_paths = self._dedupe_font_paths(
            (FONT_PATH_EN,)
            + local_font_paths
            + SYSTEM_LATIN_FONT_PATHS
            + SYSTEM_CJK_FONT_PATHS
        )
        generic_paths = self._dedupe_font_paths(
            local_font_paths
            + SYSTEM_LATIN_FONT_PATHS
            + SYSTEM_CJK_FONT_PATHS
            + SYSTEM_EMOJI_FONT_PATHS
        )
        self._font_paths_cache = (emoji_paths, cjk_paths, latin_paths, generic_paths)
        return self._font_paths_cache

    def _choose_font(self, cluster: str, size: int):
        emoji_paths, cjk_paths, latin_paths, generic_paths = self._get_font_paths()
        if self._contains_emoji(cluster) and emoji_paths:
            return self._get_font(emoji_paths[0], size)
        if self._contains_cjk(cluster) and cjk_paths:
            return self._get_font(cjk_paths[0], size)
        if self._contains_ascii_alnum(cluster) and latin_paths:
            return self._get_font(latin_paths[0], size)
        if self._is_fullwidth_punct_cluster(cluster) and cjk_paths:
            return self._get_font(cjk_paths[0], size)
        if self._is_ascii_punct_cluster(cluster) and latin_paths:
            return self._get_font(latin_paths[0], size)
        if latin_paths:
            return self._get_font(latin_paths[0], size)
        if generic_paths:
            return self._get_font(generic_paths[0], size)
        return ImageFont.load_default()

    def _build_text_chunks(self, text: str, size: int) -> list[dict]:
        chunks = []
        current_font = None
        current_text = ""

        for cluster in self._split_text_clusters(str(text or "")):
            font = self._choose_font(cluster, size)
            if current_font is None or font != current_font:
                if current_text:
                    chunks.append({"text": current_text, "font": current_font})
                current_text = cluster
                current_font = font
            else:
                current_text += cluster

        if current_text:
            chunks.append({"text": current_text, "font": current_font})

        return chunks

    def _measure_text_mixed(self, text: str, size: int) -> tuple[float, float]:
        total_w = 0.0
        max_h = 0.0
        for chunk in self._build_text_chunks(text, size):
            font = chunk["font"]
            content = chunk["text"]
            width = font.getlength(content)
            bbox = font.getbbox(content)
            height = (bbox[3] - bbox[1]) if bbox else size
            total_w += width
            max_h = max(max_h, float(height))
        return total_w, max_h

    def _truncate_text_to_width(self, text: str, size: int, max_width: float, suffix: str = "...") -> str:
        text = str(text or "")
        if not text:
            return text

        full_width, _ = self._measure_text_mixed(text, size)
        if full_width <= max_width:
            return text

        suffix_width, _ = self._measure_text_mixed(suffix, size)
        if suffix_width > max_width:
            return ""

        kept = []
        current_width = 0.0
        for cluster in self._split_text_clusters(text):
            cluster_width, _ = self._measure_text_mixed(cluster, size)
            if current_width + cluster_width + suffix_width > max_width:
                break
            kept.append(cluster)
            current_width += cluster_width

        return ("".join(kept) + suffix) if kept else suffix

    async def _get_bg_local(self):
        try:
            bg_path = _build_background_path(self.userid, self.today)
            files = os.listdir(LOCAL_BG_DIR)
            img_files = [f for f in files if f.lower().endswith((".png", ".jpg", ".jpeg"))]
            if not img_files:
                return None

            chosen_img = random.choice(img_files)
            img_path = os.path.join(LOCAL_BG_DIR, chosen_img)

            loop = asyncio.get_running_loop()
            bg_data = await loop.run_in_executor(None, read_content, img_path)
            await loop.run_in_executor(None, save_content, bg_path, bg_data)
            return bg_data
        except Exception as e:
            logger.error(f"获取本地图库失败: {e}")
            return None

    async def _get_bg(self, session: aiohttp.ClientSession):
        try:
            bg_path = _build_background_path(self.userid, self.today)
            async with session.get(BG_URL, timeout=30) as resp:
                if resp.status != 200:
                    logger.error(f"图库 API 请求错误: {resp.status}")
                    return None

                response = await resp.read()
                response_dict = json.loads(response)
                image_url = response_dict.get("data")
                if not image_url:
                    return None

                async with session.get(image_url, timeout=30) as img_resp:
                    if img_resp.status != 200:
                        logger.error(f"图库 API 成功但取图失败: {img_resp.status}")
                        return None

                    bg_data = await img_resp.read()
                    try:
                        loop = asyncio.get_running_loop()
                        await loop.run_in_executor(None, save_content, bg_path, bg_data)
                    except Exception as e:
                        logger.warning(f"保存背景图失败: {e}")
                    return bg_data
        except Exception as e:
            logger.error(f"无法获取签到背景图: {e}")
            return None

    async def _get_avatar(self, session: aiohttp.ClientSession):
        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={self.userid}&s=640"
        try:
            async with session.get(avatar_url, timeout=30) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception as e:
            logger.error(f"无法获取用户头像: {e}")
        return None

    def _round_corner(self, img, radius):
        if radius == 0:
            return img
        img = img.convert("RGBA")
        w, h = img.size
        aa_scale = 4
        mask = Image.new("L", (w * aa_scale, h * aa_scale), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle(
            (0, 0, w * aa_scale - 1, h * aa_scale - 1),
            radius=radius * aa_scale,
            fill=255,
        )
        mask = mask.resize((w, h), Image.Resampling.LANCZOS)
        img.putalpha(mask)
        mask.close()
        return img

    def _create_rounded_panel(
        self,
        width: int,
        height: int,
        radius: int,
        fill=(255, 255, 255, 80),
        outline=None,
        outline_width: int = 0,
    ):
        aa_scale = 4
        panel = Image.new("RGBA", (width * aa_scale, height * aa_scale), (0, 0, 0, 0))
        panel_draw = ImageDraw.Draw(panel)
        panel_draw.rounded_rectangle(
            (0, 0, width * aa_scale - 1, height * aa_scale - 1),
            radius=radius * aa_scale,
            fill=fill,
            outline=outline,
            width=max(0, outline_width * aa_scale),
        )
        panel = panel.resize((width, height), Image.Resampling.LANCZOS)
        return panel

    def _get_hour_word(self):
        h = datetime.datetime.now().hour
        if 6 <= h < 11:
            return "早上好"
        if 11 <= h < 14:
            return "中午好"
        if 14 <= h < 19:
            return "下午好"
        if 19 <= h < 24:
            return "晚上好"
        return "凌晨好"

    def _get_level(self, level):
        match level:
            case 1:
                return self.level_word.get("lv1") or "未知"
            case 2:
                return self.level_word.get("lv2") or "未知"
            case 3:
                return self.level_word.get("lv3") or "未知"
            case 4:
                return self.level_word.get("lv4") or "未知"
            case 5:
                return self.level_word.get("lv5") or "未知"
            case 6:
                return self.level_word.get("lv6") or "未知"
            case 7:
                return self.level_word.get("lv7") or "未知"
            case 8:
                return self.level_word.get("lv8") or "未知"
        return "未知"

    def _get_streak_bonus_percent(self) -> int:
        streak = int(self.continuous_days or 0)
        if streak >= 7:
            return 15
        if streak >= 3:
            return 10
        return 0

    def _get_average_color(self, img):
        img2 = img.resize((1, 1), Image.Resampling.BOX)
        return img2.getpixel((0, 0))

    def _create_shadow(self, width, height, radius, opacity=100, blur=10, outer_only=False):
        shadow_size = (int(width + blur * 4), int(height + blur * 4))
        alpha = Image.new("L", shadow_size, 0)
        draw = ImageDraw.Draw(alpha)

        offset = blur * 2
        draw.rounded_rectangle(
            (offset, offset, offset + width, offset + height),
            radius,
            fill=opacity,
        )
        alpha = alpha.filter(ImageFilter.GaussianBlur(blur))

        if outer_only:
            cut = Image.new("L", shadow_size, 0)
            cut_draw = ImageDraw.Draw(cut)
            cut_draw.rounded_rectangle(
                (offset, offset, offset + width, offset + height),
                radius,
                fill=255,
            )
            alpha = ImageChops.subtract(alpha, cut)
            cut.close()

        shadow = Image.new("RGBA", shadow_size, (0, 0, 0, 0))
        shadow.putalpha(alpha)
        alpha.close()
        return shadow

    def _draw_text_mixed(
        self,
        draw,
        x,
        y,
        text,
        size,
        fill=(255, 255, 255, 255),
        anchor="lt",
        stroke_width=0,
        stroke_fill=None,
        shadow_color=None,
        shadow_offset=(0, 0),
    ):
        chunk_metrics = []
        total_w = 0.0
        max_ascent = 0.0
        max_descent = 0.0

        for chunk in self._build_text_chunks(str(text or ""), size):
            font = chunk["font"]
            content = chunk["text"]
            width = font.getlength(content)

            try:
                ascent, descent = font.getmetrics()
            except Exception:
                bbox = font.getbbox(content)
                ascent = max(0, -bbox[1]) if bbox else size
                descent = max(0, bbox[3]) if bbox else int(size * 0.25)

            chunk_metrics.append(
                {
                    "width": width,
                    "text": content,
                    "font": font,
                    "ascent": float(ascent),
                    "descent": float(descent),
                }
            )
            total_w += width
            max_ascent = max(max_ascent, float(ascent))
            max_descent = max(max_descent, float(descent))

        line_height = max_ascent + max_descent

        start_x = float(x)
        start_y = float(y)

        ax = anchor[0] if len(anchor) > 0 else "l"
        ay = anchor[1] if len(anchor) > 1 else "t"

        if ax == "m":
            start_x -= total_w / 2
        elif ax == "r":
            start_x -= total_w

        top_y = start_y
        if ay == "m":
            top_y -= line_height / 2
        elif ay == "b":
            top_y -= line_height

        baseline_y = top_y + max_ascent

        if shadow_color and shadow_offset != (0, 0):
            sx = start_x + shadow_offset[0]
            cx = sx
            for cm in chunk_metrics:
                sy = baseline_y - cm["ascent"] + shadow_offset[1]
                draw.text(
                    (cx, sy),
                    cm["text"],
                    font=cm["font"],
                    fill=shadow_color,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_fill,
                )
                cx += cm["width"]

        curr_x = start_x
        for cm in chunk_metrics:
            draw_y = baseline_y - cm["ascent"]
            draw.text(
                (curr_x, draw_y),
                cm["text"],
                font=cm["font"],
                fill=fill,
                stroke_width=stroke_width,
                stroke_fill=stroke_fill,
            )
            curr_x += cm["width"]

        return total_w, line_height

    async def _image_cache(self):
        try:
            image_path = _build_sign_cache_path(self.userid, self.today)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, read_content, image_path)
        except FileNotFoundError:
            logger.error(f"找不到缓存签到图: {self.userid}-{self.today}")
            return None
        except Exception as e:
            logger.error(f"读取缓存签到图失败: {e}")
            return None

    async def _draw(self):
        await self._prepare_resources()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._draw_sync)
        except Exception as e:
            logger.error(f"签到图片生成失败: {e}")
            logger.error(re.sub(r'File ".*?",', "", str(e)))
            logger.error(traceback.format_exc())
            return None

    def _draw_sync(self):
        """同步绘图逻辑，在独立线程中执行"""
        background = None
        main_bg = None
        canvas = None
        final_img = None

        if not self.bg_data:
            raise Exception("未找到背景图片！")

        target_width = 1280

        try:
            background = Image.open(io.BytesIO(self.bg_data)).convert("RGBA")

            bg_w, bg_h = background.size
            if bg_h / bg_w > 2:
                bg_h = int(bg_w * 2)
                background = background.crop((0, 0, bg_w, bg_h))

            scale = target_width / bg_w
            target_height = max(680, int(bg_h * scale))

            main_bg = background.resize((target_width, target_height), Image.Resampling.LANCZOS)
            blur_bg = main_bg.filter(ImageFilter.GaussianBlur(12))
            canvas = blur_bg.copy()
            blur_bg.close()

            overlay = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.rectangle((0, 0, target_width, target_height), fill=(12, 18, 28, 8))
            canvas.alpha_composite(overlay)
            overlay.close()

            draw = ImageDraw.Draw(canvas)

            card_margin = 40
            card_w = int(target_width * 0.58)
            card_h = int(target_height * 0.60)
            card_x = target_width - card_w - card_margin
            card_y = (target_height - card_h) // 2
            card_radius = 34

            shadow = self._create_shadow(card_w, card_h, card_radius, opacity=120, blur=8, outer_only=True)
            canvas.paste(shadow, (card_x - 16, card_y - 16), shadow)
            shadow.close()

            # 卡片
            card_img = main_bg.resize((card_w, card_h), Image.Resampling.LANCZOS)
            card_img = self._round_corner(card_img, card_radius)
            canvas.paste(card_img, (card_x, card_y), card_img)
            card_img.close()

            glass_card = self._create_rounded_panel(
                card_w,
                card_h,
                card_radius,
                fill=(255, 255, 255, 6),
                outline=(255, 255, 255, 168),
                outline_width=2,
            )
            canvas.paste(glass_card, (card_x, card_y), glass_card)
            glass_card.close()

            inner_glass = self._create_rounded_panel(
                card_w - 20,
                card_h - 20,
                max(8, card_radius - 8),
                fill=(255, 255, 255, 4),
            )
            canvas.paste(inner_glass, (card_x + 10, card_y + 10), inner_glass)
            inner_glass.close()

            left_x = 60
            left_panel_w = max(300, card_x - left_x - 30)
            avatar_size = max(96, min(150, int(target_height * 0.17)))
            avatar_anchor_y = max(24, card_y - int(target_height * 0.19))
            avatar_center_y = avatar_anchor_y + avatar_size / 2
            identity_x = left_x + 12

            nickname_font_size = max(24, min(40, int(target_height * 0.048)))
            nickname_padding_x = max(14, int(nickname_font_size * 0.45))
            nickname_padding_y = max(10, int(nickname_font_size * 0.22))

            left_block_size = avatar_size + 18
            left_block_x = identity_x
            left_block_y = int(avatar_center_y - left_block_size / 2)
            left_block_radius = 12

            right_block_x = left_block_x + left_block_size - 4
            nickname_display = str(self.nickname or "")
            min_nickname_font = 16
            max_block_w = max(110, target_width - right_block_x - 24)

            while True:
                nickname_w, nickname_text_h = self._measure_text_mixed(
                    nickname_display,
                    nickname_font_size,
                )
                right_block_w = max(110, int(nickname_w + nickname_padding_x * 2))
                if right_block_w <= max_block_w or nickname_font_size <= min_nickname_font:
                    right_block_w = min(right_block_w, max_block_w)
                    break
                nickname_font_size -= 1
                nickname_padding_x = max(10, int(nickname_font_size * 0.40))
                nickname_padding_y = max(8, int(nickname_font_size * 0.20))

            right_block_h = max(40, min(62, int(nickname_text_h + nickname_padding_y * 2)))
            right_block_y = int(avatar_center_y - right_block_h / 2)
            right_block_radius = 8

            bridge_w = 10
            bridge_h = max(18, right_block_h - 8)
            bridge_x = left_block_x + left_block_size - (bridge_w // 2)
            bridge_y = int(avatar_center_y - bridge_h / 2)
            bridge_radius = 5

            # 纯色半透明连体底座：统一形状一次性上色，避免连接处出现高光拼缝
            shape_min_x = min(left_block_x, right_block_x, bridge_x)
            shape_min_y = min(left_block_y, right_block_y, bridge_y)
            shape_max_x = max(left_block_x + left_block_size, right_block_x + right_block_w, bridge_x + bridge_w)
            shape_max_y = max(left_block_y + left_block_size, right_block_y + right_block_h, bridge_y + bridge_h)
            shape_w = max(2, shape_max_x - shape_min_x)
            shape_h = max(2, shape_max_y - shape_min_y)

            aa_scale = 6
            shape_mask_aa = Image.new("L", (shape_w * aa_scale, shape_h * aa_scale), 0)
            shape_draw = ImageDraw.Draw(shape_mask_aa)

            lx1 = (left_block_x - shape_min_x) * aa_scale
            ly1 = (left_block_y - shape_min_y) * aa_scale
            lx2 = lx1 + left_block_size * aa_scale
            ly2 = ly1 + left_block_size * aa_scale
            rx1 = (right_block_x - shape_min_x) * aa_scale
            ry1 = (right_block_y - shape_min_y) * aa_scale
            rx2 = rx1 + right_block_w * aa_scale
            ry2 = ry1 + right_block_h * aa_scale
            bx1 = (bridge_x - shape_min_x) * aa_scale
            by1 = (bridge_y - shape_min_y) * aa_scale
            bx2 = bx1 + bridge_w * aa_scale
            by2 = by1 + bridge_h * aa_scale

            shape_draw.rounded_rectangle((lx1, ly1, lx2, ly2), radius=left_block_radius * aa_scale, fill=255)
            shape_draw.rounded_rectangle((rx1, ry1, rx2, ry2), radius=right_block_radius * aa_scale, fill=255)
            shape_draw.rounded_rectangle((bx1, by1, bx2, by2), radius=bridge_radius * aa_scale, fill=255)
            shape_mask = shape_mask_aa.resize((shape_w, shape_h), Image.Resampling.LANCZOS)
            shape_mask_aa.close()

            shadow_pad = 12
            shadow_alpha = Image.new("L", (shape_w + shadow_pad * 2, shape_h + shadow_pad * 2), 0)
            shadow_alpha.paste(shape_mask, (shadow_pad, shadow_pad))
            shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(6))
            shadow_alpha = shadow_alpha.point(lambda p: int(p * 0.34))
            shape_shadow = Image.new("RGBA", shadow_alpha.size, (0, 0, 0, 0))
            shape_shadow.putalpha(shadow_alpha)
            canvas.paste(shape_shadow, (shape_min_x - shadow_pad, shape_min_y - shadow_pad), shape_shadow)
            shape_shadow.close()
            shadow_alpha.close()

            base_alpha = shape_mask.point(lambda p: int(p * 0.22))
            base_layer = Image.new("RGBA", (shape_w, shape_h), (246, 248, 252, 0))
            base_layer.putalpha(base_alpha)
            canvas.alpha_composite(base_layer, (shape_min_x, shape_min_y))
            base_layer.close()
            base_alpha.close()

            # 环形描边：先外扩再内缩，确保边框完整连续且连接处不会断边
            stroke_pad = 2
            stroke_source = Image.new("L", (shape_w + stroke_pad * 2, shape_h + stroke_pad * 2), 0)
            stroke_source.paste(shape_mask, (stroke_pad, stroke_pad))
            outer_mask = stroke_source.filter(ImageFilter.MaxFilter(3))
            inner_mask = stroke_source.filter(ImageFilter.MinFilter(3))
            border_alpha = ImageChops.subtract(outer_mask, inner_mask)
            border_alpha = border_alpha.filter(ImageFilter.GaussianBlur(0.35))
            border_alpha = border_alpha.point(lambda p: int(p * 0.42))
            border_layer = Image.new("RGBA", border_alpha.size, (255, 255, 255, 0))
            border_layer.putalpha(border_alpha)
            canvas.alpha_composite(border_layer, (shape_min_x - stroke_pad, shape_min_y - stroke_pad))
            border_layer.close()
            border_alpha.close()
            outer_mask.close()
            stroke_source.close()
            inner_mask.close()
            shape_mask.close()

            avatar_panel_x = left_block_x + (left_block_size - avatar_size) // 2
            avatar_panel_y = int(avatar_center_y - avatar_size / 2)

            av_img = None
            if self.avatar_data:
                try:
                    av_img = Image.open(io.BytesIO(self.avatar_data)).convert("RGBA")
                except Exception as e:
                    logger.warning(f"解析头像失败: {e}")

            if av_img is None:
                av_img = Image.new("RGBA", (avatar_size, avatar_size), (205, 210, 218, 255))

            avatar_inner_size = min(
                avatar_size,
                max(avatar_size - 2, int(round(avatar_size * (0.99 ** 0.5)))),
            )
            avatar_offset = max(0, (avatar_size - avatar_inner_size) // 2)
            av_img = av_img.resize((avatar_inner_size, avatar_inner_size), Image.Resampling.LANCZOS)
            av_img = self._round_corner(av_img, max(8, int(avatar_inner_size * 0.10)))
            canvas.paste(av_img, (avatar_panel_x + avatar_offset, avatar_panel_y + avatar_offset), av_img)
            av_img.close()

            name_x = right_block_x + nickname_padding_x
            name_y = int(right_block_y + (right_block_h - nickname_text_h) / 2)
            self._draw_text_mixed(
                draw,
                name_x,
                name_y,
                nickname_display,
                size=nickname_font_size,
                fill=(255, 255, 255, 245),
                shadow_color=(10, 16, 24, 84),
                shadow_offset=(1, 1),
            )

            hour_word = self._get_hour_word()
            text_x = left_x + 10
            hour_size = max(58, min(82, int(target_height * 0.105)))
            coin_size = 40
            attitude_size = 38

            coin_str = f"{self.wallet_name} + {self.add_coins}"
            attitude_str = f"态度: {self.get_level_word}"
            total_str = f"你有 {self.coins} 枚{self.wallet_name}"

            _, hour_h = self._measure_text_mixed(hour_word, hour_size)
            _, coin_h = self._measure_text_mixed(coin_str, coin_size)
            _, attitude_h = self._measure_text_mixed(attitude_str, attitude_size)
            text_gap_1 = 44
            text_gap_2 = 28
            block_h = hour_h + text_gap_1 + coin_h + text_gap_2 + attitude_h
            block_top_y = int(card_y + card_h / 2 - block_h / 2)

            self._draw_text_mixed(
                draw,
                text_x,
                block_top_y,
                hour_word,
                size=hour_size,
                fill=(248, 251, 255, 248),
                shadow_color=(16, 22, 32, 102),
                shadow_offset=(3, 3),
            )

            coin_y = int(block_top_y + hour_h + text_gap_1)
            self._draw_text_mixed(draw, text_x, coin_y, coin_str, size=coin_size, fill=(242, 247, 255, 240), shadow_color=(12, 18, 28, 90), shadow_offset=(2, 2))

            attitude_y = int(coin_y + coin_h + text_gap_2)
            self._draw_text_mixed(draw, text_x, attitude_y, attitude_str, size=attitude_size, fill=(233, 240, 252, 232), shadow_color=(10, 14, 24, 80), shadow_offset=(2, 2))

            total_y = int(attitude_y + attitude_h + 64)
            self._draw_text_mixed(draw, text_x, total_y, total_str, size=34, fill=(226, 235, 247, 220), shadow_color=(8, 12, 20, 76), shadow_offset=(2, 2))

            impression_value = Decimal(str(self.impression or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            next_level_raw = Decimal(str((self.level or 1) * self.next_score))
            if next_level_raw <= 0:
                next_level_raw = Decimal("0.01")
            next_level_score = next_level_raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            progress_ratio = float(max(Decimal("0"), min(Decimal("1"), impression_value / next_level_score)))

            stat_x = target_width - 40
            total_days_str = f"累计签到 {self.total_days} 天"
            total_days_y = max(20, card_y - 58)
            self._draw_text_mixed(draw, stat_x, total_days_y, total_days_str, size=40, fill=(245, 249, 255, 236), anchor="rm", shadow_color=(10, 14, 22, 86), shadow_offset=(2, 2))

            progress_title_y = card_y + card_h + 8
            self._draw_text_mixed(draw, card_x, progress_title_y, "好感度进度", size=30, fill=(235, 242, 252, 232))
            prog_str = f"{impression_value}/{next_level_score}"
            self._draw_text_mixed(
                draw,
                card_x + card_w,
                progress_title_y,
                prog_str,
                size=24,
                fill=(242, 248, 255, 228),
                anchor="rt",
                shadow_color=(10, 14, 22, 72),
                shadow_offset=(1, 1),
            )

            bar_x = card_x
            bar_y = progress_title_y + 34
            bar_w = card_w
            bar_h = 16
            draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=8, fill=(190, 206, 226, 110))
            draw.rounded_rectangle((bar_x, bar_y, bar_x + max(8, int(bar_w * progress_ratio)), bar_y + bar_h), radius=8, fill=(255, 255, 255, 206))

            info_line_y = bar_y + bar_h + 8
            date_str = str(self.last_sign or self.today)
            date_max_w = max(320, stat_x - card_x - 120)
            date_text = self._truncate_text_to_width(date_str, 22, date_max_w)
            bonus_percent = self._get_streak_bonus_percent()
            bonus_str = f"连续签到奖励: {bonus_percent}%"
            continuous_gap = 18
            continuous_size = 24
            continuous_y = info_line_y + continuous_gap

            _, date_h = self._measure_text_mixed(date_text, 22)
            _, bonus_h = self._measure_text_mixed(bonus_str, 18)
            _, continuous_h = self._measure_text_mixed(f"连续签到 {self.continuous_days} 天", continuous_size)
            info_bottom = max(info_line_y + max(date_h, bonus_h), continuous_y + continuous_h)
            footer_safe_top = target_height - 34
            if info_bottom > footer_safe_top:
                shift_up = int(info_bottom - footer_safe_top)
                min_info_y = bar_y + bar_h + 8
                info_line_y = max(min_info_y, info_line_y - shift_up)
                continuous_y = info_line_y + continuous_gap

            self._draw_text_mixed(
                draw,
                card_x,
                info_line_y,
                date_text,
                size=22,
                fill=(245, 249, 255, 232),
                anchor="lt",
                shadow_color=(10, 14, 22, 80),
                shadow_offset=(2, 2),
            )

            continuous_days_str = f"连续签到 {self.continuous_days} 天"
            self._draw_text_mixed(
                draw,
                stat_x,
                continuous_y,
                continuous_days_str,
                size=continuous_size,
                fill=(245, 249, 255, 236),
                anchor="rt",
                shadow_color=(10, 14, 22, 86),
                shadow_offset=(2, 2),
            )

            self._draw_text_mixed(
                draw,
                stat_x,
                info_line_y,
                bonus_str,
                size=18,
                fill=(228, 238, 250, 228),
                anchor="rt",
                shadow_color=(10, 14, 22, 72),
                shadow_offset=(1, 1),
            )

            footer = f"Created By MaiBot {MMC_VERSION} & Sign Plugin {PLUGIN_VERSION}"
            self._draw_text_mixed(draw, target_width // 2, target_height - 14, footer, size=20, fill=(225, 232, 242, 220), anchor="mm", shadow_color=(0, 0, 0, 72), shadow_offset=(1, 1))

            image_path = _build_sign_cache_path(self.userid, self.today)

            output = io.BytesIO()
            final_img = canvas.convert("RGB")
            final_img.save(output, format="PNG")
            img_data = output.getvalue()

            with open(image_path, "wb") as f:
                f.write(img_data)

            return img_data

        finally:
            if background:
                background.close()
            if main_bg:
                main_bg.close()
            if canvas:
                canvas.close()
            if final_img:
                final_img.close()


@dataclass
class RankingEntry:
    rank: int
    user_id: str
    nickname: str
    attitude: str
    impression_text: str
    progress_text: str
    progress_ratio: float


class ImpressionRankingImageGen:
    def __init__(
        self,
        entries: list[RankingEntry],
        title: str = "好感度排行",
        max_impression: float = 200.0,
        updated_text: str = "",
    ):
        self.entries = list(entries or [])
        self.title = str(title or "好感度排行")
        self.max_impression = float(max(0.0, max_impression))
        self.updated_text = str(updated_text or "")
        self.today = datetime.datetime.now().strftime("%Y-%m-%d")
        self.avatar_map: Dict[str, bytes] = {}

        self._text_helper = ImageGen(
            userdata={
                "user_id": "ranking",
                "impression": 0,
                "coins": 0,
                "last_sign": "",
                "total_days": 0,
                "continuous_days": 0,
                "level": 1,
            },
            nickname="ranking",
        )

    async def draw(self) -> Optional[bytes]:
        try:
            await self._prepare_avatars()
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._draw_sync)
        except Exception as e:
            logger.error(f"排行榜图片生成失败: {e}")
            logger.error(traceback.format_exc())
            return None

    async def _prepare_avatars(self) -> None:
        user_ids = []
        seen = set()
        for item in self.entries:
            uid = str(getattr(item, "user_id", "") or "").strip()
            if not uid or uid in seen:
                continue
            seen.add(uid)
            user_ids.append(uid)

        if not user_ids:
            return

        semaphore = asyncio.Semaphore(8)
        avatar_map: Dict[str, bytes] = {}

        async def fetch_one(session: aiohttp.ClientSession, user_id: str):
            if not user_id.isdigit():
                return
            url = f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=160"
            try:
                async with semaphore:
                    async with session.get(url, timeout=12) as resp:
                        if resp.status != 200:
                            return
                        data = await resp.read()
                        if data:
                            avatar_map[user_id] = data
            except Exception:
                return

        async with aiohttp.ClientSession() as session:
            tasks = [fetch_one(session, user_id) for user_id in user_ids]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        self.avatar_map = avatar_map

    @staticmethod
    def _clamp_ratio(value: float) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0

    def _draw_text(self, draw: ImageDraw.ImageDraw, *args, **kwargs):
        return self._text_helper._draw_text_mixed(draw, *args, **kwargs)

    def _measure_text(self, text: str, size: int) -> tuple[float, float]:
        return self._text_helper._measure_text_mixed(text, size)

    def _truncate(self, text: str, size: int, max_width: float) -> str:
        return self._text_helper._truncate_text_to_width(text, size, max_width)

    def _avatar_text(self, name: str) -> str:
        clusters = self._text_helper._split_text_clusters(str(name or "").strip())
        if not clusters:
            return "#"
        return clusters[0]

    def _get_avatar_image(self, user_id: str, size: int) -> Optional[Image.Image]:
        avatar_data = self.avatar_map.get(str(user_id or "").strip())
        if not avatar_data:
            return None
        try:
            avatar = Image.open(io.BytesIO(avatar_data)).convert("RGBA")
            avatar = avatar.resize((size, size), Image.Resampling.LANCZOS)
            return self._text_helper._round_corner(avatar, size // 2)
        except Exception:
            return None

    def _draw_background(self, canvas: Image.Image) -> None:
        width, height = canvas.size
        draw = ImageDraw.Draw(canvas)
        top_rgb = (246, 247, 250)
        bottom_rgb = (236, 240, 247)
        for y in range(height):
            t = y / max(1, height - 1)
            color = (
                int(top_rgb[0] * (1 - t) + bottom_rgb[0] * t),
                int(top_rgb[1] * (1 - t) + bottom_rgb[1] * t),
                int(top_rgb[2] * (1 - t) + bottom_rgb[2] * t),
                255,
            )
            draw.line((0, y, width, y), fill=color)

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        odraw.ellipse((-180, -120, width // 2 + 160, height // 2), fill=(0, 113, 227, 22))
        odraw.ellipse((width // 3, height // 3, width + 260, height + 260), fill=(52, 199, 89, 16))
        odraw.ellipse((width // 6, height // 2 - 120, width * 4 // 5, height + 120), fill=(245, 166, 35, 13))
        overlay = overlay.filter(ImageFilter.GaussianBlur(36))
        canvas.alpha_composite(overlay)
        overlay.close()

    def _draw_card(
        self,
        draw: ImageDraw.ImageDraw,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int = 18,
        fill=(255, 255, 255, 175),
        outline=(255, 255, 255, 228),
        outline_width: int = 2,
    ) -> None:
        draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=fill, outline=outline, width=outline_width)

    def _draw_sync(self) -> Optional[bytes]:
        if not self.entries:
            return None

        width = 1080
        top_count = min(3, len(self.entries))
        list_entries = self.entries[top_count:]

        header_h = 160
        badge_h = 52
        podium_h = 320 if top_count > 0 else 0
        list_gap_h = 20 if list_entries else 0
        row_h = 96
        row_gap = 12
        rows_h = 0
        if list_entries:
            rows_h = len(list_entries) * row_h + max(0, len(list_entries) - 1) * row_gap
        footer_h = 58
        canvas_h = max(820, 40 + header_h + badge_h + 18 + podium_h + list_gap_h + rows_h + footer_h + 30)

        canvas = Image.new("RGBA", (width, canvas_h), (246, 247, 250, 255))
        self._draw_background(canvas)
        draw = ImageDraw.Draw(canvas)

        y = 40
        self._draw_text(draw, width // 2, y, "SIGN PLUGIN", size=20, fill=(0, 113, 227, 220), anchor="mt")
        y += 28
        self._draw_text(draw, width // 2, y, self.title, size=62, fill=(29, 29, 31, 245), anchor="mt")
        y += 118
        badge_text = f"更新时间 · {self.updated_text}" if self.updated_text else "更新时间 · 未知"
        badge_w, badge_h_text = self._measure_text(badge_text, 20)
        badge_pad_x = 18
        badge_w_total = int(badge_w + badge_pad_x * 2 + 16)
        badge_x1 = (width - badge_w_total) // 2
        badge_y1 = y
        badge_x2 = badge_x1 + badge_w_total
        badge_y2 = badge_y1 + 36
        self._draw_card(
            draw,
            badge_x1,
            badge_y1,
            badge_x2,
            badge_y2,
            radius=18,
            fill=(255, 255, 255, 165),
            outline=(255, 255, 255, 230),
            outline_width=1,
        )
        dot_cx = badge_x1 + 14
        dot_cy = badge_y1 + 18
        draw.ellipse((dot_cx - 4, dot_cy - 4, dot_cx + 4, dot_cy + 4), fill=(52, 199, 89, 240))
        self._draw_text(draw, dot_cx + 10, badge_y1 + (36 - badge_h_text) // 2, badge_text, size=20, fill=(99, 99, 105, 235))

        y = badge_y2 + 24
        if top_count > 0:
            self._draw_card(
                draw,
                48,
                y,
                width - 48,
                y + podium_h - 10,
                radius=28,
                fill=(255, 255, 255, 145),
                outline=(255, 255, 255, 220),
                outline_width=2,
            )
            # 基准线下移，给头像和文案留出顶部安全边距，避免超出上边框。
            podium_center_y = y + podium_h - 12
            top_entries = self.entries[:top_count]
            if top_count == 1:
                layout = [(top_entries[0], width // 2)]
            elif top_count == 2:
                layout = [(top_entries[0], width // 2 - 150), (top_entries[1], width // 2 + 150)]
            else:
                layout = [(top_entries[1], width // 2 - 220), (top_entries[0], width // 2), (top_entries[2], width // 2 + 220)]

            for item, cx in layout:
                rank = int(item.rank or 0)
                block_h = {1: 122, 2: 94, 3: 72}.get(rank, 62)
                block_w = 164
                block_x1 = int(cx - block_w // 2)
                block_x2 = int(cx + block_w // 2)
                block_y1 = int(podium_center_y - block_h)
                block_y2 = int(podium_center_y)
                block_gradient = {
                    1: ((255, 196, 92, 220), (255, 226, 154, 220)),
                    2: ((202, 208, 221, 215), (232, 236, 244, 215)),
                    3: ((212, 152, 103, 215), (238, 195, 152, 215)),
                }.get(rank, ((224, 230, 242, 210), (241, 245, 251, 210)))
                block_w_px = max(1, block_x2 - block_x1)
                block_h_px = max(1, block_y2 - block_y1)
                grad_layer = Image.new("RGBA", (block_w_px, block_h_px), (0, 0, 0, 0))
                grad_draw = ImageDraw.Draw(grad_layer)
                left_c, right_c = block_gradient
                for gx in range(block_w_px):
                    t = gx / max(1, block_w_px - 1)
                    color = (
                        int(left_c[0] * (1 - t) + right_c[0] * t),
                        int(left_c[1] * (1 - t) + right_c[1] * t),
                        int(left_c[2] * (1 - t) + right_c[2] * t),
                        int(left_c[3] * (1 - t) + right_c[3] * t),
                    )
                    grad_draw.line((gx, 0, gx, block_h_px), fill=color)

                block_mask = Image.new("L", (block_w_px, block_h_px), 0)
                mask_draw = ImageDraw.Draw(block_mask)
                mask_draw.rounded_rectangle(
                    (0, 0, block_w_px - 1, block_h_px - 1),
                    radius=16,
                    fill=255,
                )
                canvas.paste(grad_layer, (block_x1, block_y1), block_mask)
                grad_layer.close()
                block_mask.close()

                draw.rounded_rectangle(
                    (block_x1, block_y1, block_x2, block_y2),
                    radius=16,
                    outline=(255, 255, 255, 230),
                    width=2,
                )
                self._draw_text(draw, cx, block_y1 + (block_h // 2) - 8, str(rank), size=38, fill=(58, 58, 60, 235), anchor="mm")

                avatar_size = 82 if rank == 1 else 70
                av_x1 = int(cx - avatar_size // 2)
                av_x2 = int(cx + avatar_size // 2)
                # 为头像下方三行文案预留空间，避免与领奖台重叠。
                av_y2 = block_y1 - 92
                av_y1 = av_y2 - avatar_size
                draw.ellipse((av_x1, av_y1, av_x2, av_y2), fill=(242, 245, 252, 245), outline=(255, 255, 255, 245), width=2)
                avatar_img = self._get_avatar_image(item.user_id, avatar_size - 4)
                if avatar_img:
                    paste_x = av_x1 + (avatar_size - avatar_img.width) // 2
                    paste_y = av_y1 + (avatar_size - avatar_img.height) // 2
                    canvas.paste(avatar_img, (paste_x, paste_y), avatar_img)
                    avatar_img.close()
                else:
                    self._draw_text(draw, cx, av_y1 + avatar_size // 2 - 2, self._avatar_text(item.nickname), size=34, fill=(66, 66, 72, 240), anchor="mm")

                text_top = av_y2 + 8
                name = self._truncate(item.nickname, 20, 176)
                self._draw_text(draw, cx, text_top, name, size=20, fill=(38, 38, 40, 235), anchor="mt")
                attitude = self._truncate(f"态度: {item.attitude}", 16, 188)
                self._draw_text(draw, cx, text_top + 24, attitude, size=16, fill=(112, 112, 118, 226), anchor="mt")
                self._draw_text(draw, cx, text_top + 46, f"{item.impression_text}", size=18, fill=(66, 66, 72, 230), anchor="mt")

            y += podium_h

        if list_entries:
            y += 10
            margin_x = 54
            card_w = width - margin_x * 2
            for i, item in enumerate(list_entries):
                row_y1 = y + i * (row_h + row_gap)
                row_y2 = row_y1 + row_h
                row_x1 = margin_x
                row_x2 = row_x1 + card_w

                self._draw_card(
                    draw,
                    row_x1,
                    row_y1,
                    row_x2,
                    row_y2,
                    radius=18,
                    fill=(255, 255, 255, 180),
                    outline=(255, 255, 255, 232),
                    outline_width=2,
                )

                self._draw_text(draw, row_x1 + 30, row_y1 + 31, str(item.rank), size=26, fill=(102, 102, 108, 230), anchor="mm")

                avatar_cx = row_x1 + 86
                avatar_cy = row_y1 + 48
                draw.ellipse((avatar_cx - 23, avatar_cy - 23, avatar_cx + 23, avatar_cy + 23), fill=(240, 243, 249, 245), outline=(255, 255, 255, 245), width=2)
                avatar_img = self._get_avatar_image(item.user_id, 42)
                if avatar_img:
                    canvas.paste(avatar_img, (avatar_cx - avatar_img.width // 2, avatar_cy - avatar_img.height // 2), avatar_img)
                    avatar_img.close()
                else:
                    self._draw_text(draw, avatar_cx, avatar_cy - 1, self._avatar_text(item.nickname), size=22, fill=(70, 70, 74, 235), anchor="mm")

                name_x = row_x1 + 124
                name = self._truncate(item.nickname, 24, 350)
                self._draw_text(draw, name_x, row_y1 + 16, name, size=24, fill=(34, 34, 36, 238), anchor="lt")
                self._draw_text(draw, name_x, row_y1 + 45, f"态度: {item.attitude}", size=18, fill=(110, 110, 116, 228), anchor="lt")

                bar_x1 = row_x1 + 124
                bar_x2 = row_x2 - 24
                bar_y1 = row_y1 + 71
                bar_y2 = bar_y1 + 10
                draw.rounded_rectangle((bar_x1, bar_y1, bar_x2, bar_y2), radius=5, fill=(214, 222, 236, 168))
                fill_ratio = self._clamp_ratio(item.progress_ratio)
                fill_w = max(8, int((bar_x2 - bar_x1) * fill_ratio))
                draw.rounded_rectangle((bar_x1, bar_y1, bar_x1 + fill_w, bar_y2), radius=5, fill=(0, 113, 227, 212))
                self._draw_text(draw, bar_x2, row_y1 + 57, item.progress_text, size=16, fill=(105, 105, 112, 228), anchor="rb")

        footer = f"Created By MaiBot {MMC_VERSION} · Sign Plugin {PLUGIN_VERSION}"
        self._draw_text(draw, width // 2, canvas_h - 20, footer, size=18, fill=(124, 124, 132, 220), anchor="mm")

        output = io.BytesIO()
        final_img = canvas.convert("RGB")
        final_img.save(output, format="PNG")
        img_data = output.getvalue()
        final_img.close()
        canvas.close()
        return img_data
