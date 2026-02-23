import io
import aiohttp
import random
import datetime
import os
import re
import asyncio
import traceback
import json
from functools import lru_cache
from typing import Optional

from PIL import (
    Image, 
    ImageDraw, 
    ImageFont, 
    ImageFilter
    )

PLUGIN_DIR = os.path.dirname(__file__)
PLUGIN_VERSION = "0.0.1"

IMAGE_DIR = os.path.join(
    PLUGIN_DIR, 
    "resources", 
    "images"
    )

LOCAL_BG_DIR = os.path.join(
    PLUGIN_DIR, 
    "resources", 
    "custombg"
    )

FONT_DIR = os.path.join(
    PLUGIN_DIR,
    "resources",
    "fonts"
    )

FONT_PATH_ZH = os.path.join(
    FONT_DIR,
    "zh_font.ttf"
)

FONT_PATH_EN = os.path.join(
    FONT_DIR,
    "en_font.ttf"
)

BG_URL = "https://v2.xxapi.cn/api/random4kPic?type=acg"
FONT_URL_ZH = "https://ghproxy.net/https://github.com/ChisugaMaeka/Chisuga-Shotai/releases/download/v0.2.12/ChisugaShotai_Regular0.2.12.1.ttf"
FONT_URL_EN = "https://cdn.jsdelivr.net/gh/ItMarki/linja-waso@main/fonts/linja-waso-lili.ttf"

from src.config.config import MMC_VERSION
from src.common.logger import get_logger

logger = get_logger("sign_draw")

_draw_initialized = False

async def init_draw():
    global _draw_initialized
    if _draw_initialized:
        return

    if not os.path.exists(IMAGE_DIR):
        os.makedirs(IMAGE_DIR)
    if not os.path.exists(FONT_DIR):
        os.makedirs(FONT_DIR)
    if not os.path.exists(LOCAL_BG_DIR):
        os.makedirs(LOCAL_BG_DIR)
    
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
    """校验字体文件完整性"""
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
    """下载字体文件"""

    logger.info(f"正在下载字体: {url} ---> {path}")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
        }
        async with session.get(url, headers=headers, timeout=180) as resp:
            if resp.status == 200:
                content = await resp.read()

                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, save_content, path, content)

                if check_font(path):
                    logger.info(f"字体下载并校验成功: {path}")
                else:
                    logger.error(f"字体文件校验失败, 请检查网络设置, 或复制上面的地址手动下载字体")
                    logger.error(f"字体初始化失败，生成签到图片时可能出现异常！")
                    os.remove(path)
            else:
                logger.error(f"字体下载失败, status={resp.status}")
                logger.error(f"请检查网络设置, 或复制上面的地址手动下载字体")
                logger.error(f"字体初始化失败，生成签到图片时可能出现异常！")
    except Exception as e:
        logger.error(f"字体下载出错: {e}")
        logger.error(f"字体初始化失败，生成签到图片时可能出现异常！")

async def get_background(userid,time):
    path = os.path.join(IMAGE_DIR, f"background-{userid}-{time}.png")

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, read_content, path)
    except Exception as e:
        logger.error(f"获取签到背景失败: {e}")
        return None

class ImageGen():
    def __init__(self, 
                userdata: dict, 
                nickname: Optional[str] = "聊天用户", 
                wallet_name: Optional[str] = "麦币", 
                add_coins: Optional[int] = 0, 
                add_impression: Optional[float] = 0,
                next_score: Optional[float] = 25, 
                level_word: Optional[dict] = {},
                use_local_bg: Optional[bool] = False):
        self.userid = userdata.get("user_id")
        self.nickname = nickname
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

        self.leve1_word = level_word
        self.get_level_word = self._get_level(self.level)
        self.today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        self.avatar_data:str = None
        self.bg_data:str = None

    async def _prepare_resources(self):
        """准备资源"""
        # 并发下载
        async with aiohttp.ClientSession() as session:
            if self.use_local_bg:
                self.bg_data = await self._get_bg_local()
            else:
                self.bg_data = await self._get_bg(session)
            self.avatar_data = await self._get_avatar(session)

    @lru_cache(128)
    def _get_font(self,path,size):
            try:
                return ImageFont.truetype(path,size)
            except:
                return ImageFont.load_default()

    async def _get_bg_local(self):
        try:
            bg_path = os.path.join(IMAGE_DIR, f"background-{self.userid}-{self.today}.png")

            files = os.listdir(LOCAL_BG_DIR)
            img_files = [f for f in files if f.lower().endswith(('.png', '.jpg', '.jpeg'))]

            chosen_img = random.choice(img_files)
            img_path = os.path.join(LOCAL_BG_DIR, chosen_img)

            loop = asyncio.get_running_loop()
            bg_data = await loop.run_in_executor(None, read_content, img_path)
            await loop.run_in_executor(None, save_content, bg_path, bg_data)

            return bg_data
        except Exception as e:
            logger.error(f"获取本地图库失败: {e}")
            logger.error("本地图库可能不存在！请自行导入图片到插件目录下的resources/custombg文件夹")
            return None

    async def _get_bg(self, session: aiohttp.ClientSession):
        try:
            bg_path = os.path.join(IMAGE_DIR, f"background-{self.userid}-{self.today}.png")
            async with session.get(BG_URL, timeout = 30) as resp:
                if resp.status == 200:
                    # bg_data = await resp.read()
                    # try:
                    #     loop = asyncio.get_running_loop()
                    #     await loop.run_in_executor(None, save_content, bg_path, bg_data)
                    # except Exception as e:
                    #     logger.warning(f"保存背景图片时出现错误: {e}")
                    # return bg_data
                    response = await resp.read()
                    response_dict = json.loads(response)
                    image_url = response_dict.get("data")
                    async with session.get(image_url, timeout = 30) as resp:
                        if resp.status == 200:
                            bg_data = await resp.read()
                            try:
                                loop = asyncio.get_running_loop()
                                await loop.run_in_executor(None, save_content, bg_path, bg_data)
                            except Exception as e:
                                logger.warning(f"保存背景图片时出现错误: {e}")
                            return bg_data
                        else:
                            logger.error(f"图库api请求成功但获取图片时出错: {resp.status}")
                            return None
                else:
                    logger.error(f"图库api请求错误: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"无法获得签到背景图片: {e}")
            return None

    async def _get_avatar(self, session: aiohttp.ClientSession):
        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={self.userid}&s=640"
        try:
            async with session.get(avatar_url,timeout=30) as resp:
                if resp.status == 200:
                    return await resp.read()
        except Exception as e:
            logger.error(f"无法获取用户头像: {e}")
        return None

    def _round_corner(self, img, radius):
        """为图像添加圆角"""
        if radius == 0: return img
        # 确保图像有alpha通道
        img = img.convert("RGBA")
        
        circle = Image.new('L', (radius * 2, radius * 2), 0)
        draw = ImageDraw.Draw(circle)
        draw.ellipse((0, 0, radius * 2, radius * 2), fill=255)
        
        alpha = Image.new('L', img.size, 255)
        w, h = img.size
        
        # 4个角
        alpha.paste(circle.crop((0, 0, radius, radius)), (0, 0))
        alpha.paste(circle.crop((radius, 0, radius * 2, radius)), (w - radius, 0))
        alpha.paste(circle.crop((radius, radius, radius * 2, radius * 2)), (w - radius, h - radius))
        alpha.paste(circle.crop((0, radius, radius, radius * 2)), (0, h - radius))
        
        # 中间区域
        draw_alpha = ImageDraw.Draw(alpha)
        # 顶部矩形
        draw_alpha.rectangle((radius, 0, w-radius, radius), fill=255)
        # 底部矩形
        draw_alpha.rectangle((radius, h-radius, w-radius, h), fill=255)
        # 中间矩形
        draw_alpha.rectangle((0, radius, w, h-radius), fill=255)
        
        img.putalpha(alpha)
        circle.close()
        alpha.close()
        return img

    def _get_hour_word(self):
        h = datetime.datetime.now().hour
        if 6 <= h < 11:
            return "早上好"
        elif 11 <= h < 14:
            return "中午好"
        elif 14 <= h < 19:
            return "下午好"
        elif 19 <= h < 24:
            return "晚上好"
        else:
            return "凌晨好"

    def _get_level(self, level):
        match level:
            case 1: return self.leve1_word.get("lv1")
            case 2: return self.leve1_word.get("lv2")
            case 3: return self.leve1_word.get("lv3")
            case 4: return self.leve1_word.get("lv4")
            case 5: return self.leve1_word.get("lv5")
            case 6: return self.leve1_word.get("lv6")
            case 7: return self.leve1_word.get("lv7")
            case 8: return self.leve1_word.get("lv8")
        return "未知"

    def _get_average_color(self, img):
        # 缩放到1x1获取平均颜色
        img2 = img.resize((1, 1), Image.Resampling.BOX)
        return img2.getpixel((0, 0))

    def _create_shadow(self, width, height, radius, opacity=100, blur=10):
        # 创建阴影
        shadow_size = (int(width + blur * 4), int(height + blur * 4))
        shadow = Image.new("RGBA", shadow_size, (0,0,0,0))
        draw = ImageDraw.Draw(shadow)
        
        offset = blur * 2

        # 在阴影画布中央绘制圆角矩形
        draw.rounded_rectangle(
            (offset, offset, offset + width, offset + height), 
            radius, 
            fill=(0, 0, 0, opacity)
        )

        return shadow.filter(ImageFilter.GaussianBlur(blur))

    def _draw_text_mixed(self, draw, x, y, text, size, fill=(255,255,255,255), anchor="lt", stroke_width=0, stroke_fill=None, shadow_color=None, shadow_offset=(0,0)):
        """
        使用混合字体绘制文本
        将文本分割成块并顺序绘制
        """
        font_zh = self._get_font(FONT_PATH_ZH, size)
        font_en = self._get_font(FONT_PATH_EN, size)
        
        # 分割文本
        parts = []
        last_idx = 0
        
        # 辅助函数判断字符类型
        def is_zh(char):
            return '\u4e00' <= char <= '\u9fff'
            
        current_chunk = ""
        current_is_zh = None
        
        for char in text:
            char_is_zh = is_zh(char)
            if current_is_zh is None:
                current_is_zh = char_is_zh
                current_chunk += char
            elif char_is_zh == current_is_zh:
                current_chunk += char
            else:
                # 类型转换
                font = font_zh if current_is_zh else font_en
                parts.append({'text': current_chunk, 'font': font})
                current_chunk = char
                current_is_zh = char_is_zh
        
        if current_chunk:
            font = font_zh if current_is_zh else font_en
            parts.append({'text': current_chunk, 'font': font})
            
        # 测量总宽度和最大高度
        total_w = 0
        max_h = 0
        chunk_metrics = []
        
        for p in parts:
            f = p['font']
            t = p['text']
            length = f.getlength(t)
            bbox = f.getbbox(t) 
            if bbox:
                h = bbox[3] - bbox[1]
            else:
                h = size
            
            chunk_metrics.append({'width': length, 'height': h, 'text': t, 'font': f})
            total_w += length
            max_h = max(max_h, h)

        # 处理锚点
        # x轴: l=左, m=中, r=右
        # y轴: t=上, m=中, b=下
        start_x = x
        start_y = y
        
        ax = anchor[0] if len(anchor) > 0 else 'l'
        ay = anchor[1] if len(anchor) > 1 else 't'
            
        if ax == 'm': start_x -= total_w / 2
        elif ax == 'r': start_x -= total_w
        
        if ay == 'm': start_y -= max_h / 2
        elif ay == 'b': start_y -= max_h
            
        # 绘制块
        
        # 如果启用则绘制阴影
        if shadow_color and shadow_offset != (0,0):
             sx = start_x + shadow_offset[0]
             sy = start_y + shadow_offset[1]
             cx = sx
             for cm in chunk_metrics:
                draw.text((cx, sy), cm['text'], font=cm['font'], fill=shadow_color, stroke_width=stroke_width, stroke_fill=stroke_fill)
                cx += cm['width']

        # 绘制主文本
        curr_x = start_x
        for cm in chunk_metrics:
            draw.text((curr_x, start_y), cm['text'], font=cm['font'], fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
            curr_x += cm['width']
            
        return total_w, max_h


    async def _image_cache(self):
        try: 
            image_path = os.path.join(IMAGE_DIR,f"{self.userid}-{self.today}.png")
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, read_content, image_path)
        except FileNotFoundError:
            logger.error(f"找不到缓存的签到图片：{image_path}")
            return None
        except Exception as e:
            logger.error(f"出现未知错误: {e}")
            return None

    async def _draw(self):
        # 准备资源 (IO操作保持异步)
        await self._prepare_resources()

        # 将CPU密集的绘图操作放入线程池执行
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._draw_sync)
        except Exception as e:
             logger.error(f"签到图片生成失败: {e}")
             logger.error(re.sub(r'File ".*?",', '', str(e))) # 简化日志
             logger.error(traceback.format_exc())
             return None

    def _draw_sync(self):
        """同步绘图逻辑，在线程池中运行"""
        background = None
        main_bg = None
        canvas = None
        
        if not self.bg_data:
            raise Exception("未找到背景图片！")

        target_width = 1280
        
        try:
            background = Image.open(io.BytesIO(self.bg_data)).convert("RGBA")
        
            bg_w, bg_h = background.size
            # 限制宽高比，避免图像过高
            if bg_h / bg_w > 2:
                bg_h = int(bg_w * 2)
                background = background.crop((0, 0, bg_w, bg_h))
                
            scale = target_width / bg_w
            target_height = int(bg_h * scale)
            if target_height < 600: target_height = 600 
            
            # 模糊背景
            main_bg = background.resize((target_width, target_height), Image.Resampling.LANCZOS)
            blur_bg = main_bg.filter(ImageFilter.GaussianBlur(15))
            
            canvas = Image.new("RGBA", (target_width, target_height), (0,0,0))
            canvas.paste(blur_bg, (0,0))
            blur_bg.close()
            
            # 创建卡片
            card_scale = 0.6
            card_w = int(target_width * card_scale)
            card_h = int(target_height * card_scale)
            
            card_img = main_bg.resize((card_w, card_h), Image.Resampling.LANCZOS)
            card_img = self._round_corner(card_img, 15)
            
            # 卡片y轴居中放置在右侧
            card_x = int(target_width * 0.66) - card_w // 2
            card_y = (target_height - card_h) // 2
            
            # 卡片阴影
            shadow = self._create_shadow(card_w, card_h, 15, opacity=150, blur=20)
            canvas.paste(shadow, (card_x - 40, card_y - 40), shadow)
            shadow.close()
            
            # 粘贴卡片
            canvas.paste(card_img, (card_x, card_y), card_img)
            card_img.close()
            
            # 头像和信息区域
            draw = ImageDraw.Draw(canvas)
            
            avatar_size = int(target_height * 0.18)
            avatar_y = card_y
            avatar_x = 60
            
            # 确定名字宽度
            dummy_img = Image.new("RGBA", (1,1))
            dummy_draw = ImageDraw.Draw(dummy_img)
            name_w, _ = self._draw_text_mixed(dummy_draw, 0, 0, self.nickname, size=35)
            dummy_img.close()
            
            # 气泡
            bubble_h = avatar_size + 20
            bubble_w = avatar_size + name_w + 60 
            
            dom_color = self._get_average_color(background)
            bubble_fill = (dom_color[0], dom_color[1], dom_color[2], 255)
            
            bubble_shadow = self._create_shadow(bubble_w, bubble_h, 20, opacity=120, blur=15)
            canvas.paste(bubble_shadow, (avatar_x - 30, avatar_y - 15), bubble_shadow)
            bubble_shadow.close()
            
            bubble_img = Image.new("RGBA", (int(bubble_w), int(bubble_h)), (0,0,0,0))
            b_draw = ImageDraw.Draw(bubble_img)
            b_draw.rounded_rectangle((0,0, bubble_w, bubble_h), radius=20, fill=bubble_fill)
            canvas.paste(bubble_img, (avatar_x, avatar_y), bubble_img)
            bubble_img.close()
            
            av_img = None
            if self.avatar_data:
                try:
                    av_img = Image.open(io.BytesIO(self.avatar_data)).convert("RGBA")
                except Exception as e:
                    logger.warning(f"解析头像失败: {e}")
            
            if av_img is None:
                av_img = Image.new("RGBA", (avatar_size, avatar_size), (200,200,200))

            av_img = av_img.resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
            av_img = self._round_corner(av_img, 15)
            
            canvas.paste(av_img, (avatar_x + 10, avatar_y + 10), av_img)

            av_img.close()
            
            # 绘制用户昵称
            name_x = avatar_x + avatar_size + 30
            name_y = avatar_y + bubble_h // 2 - 20 
            self._draw_text_mixed(draw, name_x, name_y, self.nickname, size=35, fill=(255,255,255,255), shadow_color=(0,0,0,100), shadow_offset=(2,2))
            
            # 文本信息
            hour_word = self._get_hour_word()
            text_x = avatar_x + 10
            text_y = avatar_y + bubble_h + 50
            
            # 问候语
            self._draw_text_mixed(draw, text_x, text_y, hour_word, size=80, fill=(255,255,255,255), shadow_color=(0,0,0,100), shadow_offset=(4,4))
            
            text_y += 120
            
            # 货币
            coin_str = f"{self.wallet_name} + {self.add_coins}"
            self._draw_text_mixed(draw, text_x, text_y, coin_str, size=40, fill=(255,255,255,255), shadow_color=(0,0,0,100), shadow_offset=(2,2))
            
            text_y += 60
            self._draw_text_mixed(draw, text_x, text_y, f"态度: {self.get_level_word}", size=40, fill=(255,255,255,255), shadow_color=(0,0,0,100), shadow_offset=(2,2))
            
            text_y += 90
            total_str = f"你有 {self.coins} 枚{self.wallet_name}"
            self._draw_text_mixed(draw, text_x, text_y, total_str, size=35, fill=(255,255,255,255), shadow_color=(0,0,0,100), shadow_offset=(2,2))

            # 卡片信息
            date_str = self.last_sign
            date_x = card_x + card_w - 20
            date_y = card_y + 30
            self._draw_text_mixed(draw, date_x, date_y, date_str, size=50, fill=(255,255,255,255), anchor="rm", shadow_color=(0,0,0,100), shadow_offset=(3,3))

            stat_x = target_width - 40
            total_days_str = f"累计签到 {self.total_days} 天"
            total_days_y = max(20, card_y - 60)
            self._draw_text_mixed(
                draw,
                stat_x,
                total_days_y,
                total_days_str,
                size=42,
                fill=(255,255,255,255),
                anchor="rm",
                shadow_color=(0,0,0,100),
                shadow_offset=(3,3)
            )
            
            level_y = card_y + card_h - 60
            
            # self._draw_text_mixed(draw, card_x + 30, level_y, f"关系: {self.level_word}", size=40, fill=(255,255,255,255), shadow_color=(0,0,0,100), shadow_offset=(2,2))
            
            next_score = self.level * self.next_score
            prog_str = f"{self.impression}/{next_score}"
            self._draw_text_mixed(draw, card_x + card_w, level_y, prog_str, size=40, fill=(255,255,255,255), anchor="rm", shadow_color=(0,0,0,100), shadow_offset=(2,2))

            continuous_days_str = f"连续签到 {self.continuous_days} 天"
            continuous_days_y = min(target_height - 70, card_y + card_h + 25)
            self._draw_text_mixed(
                draw,
                stat_x,
                continuous_days_y,
                continuous_days_str,
                size=42,
                fill=(255,255,255,255),
                anchor="rm",
                shadow_color=(0,0,0,100),
                shadow_offset=(3,3)
            )
            
            # 水印
            footer = f"Created By MaiBot {MMC_VERSION} & Sign Plugin {PLUGIN_VERSION}"
            self._draw_text_mixed(draw, target_width//2, target_height - 30, footer, size=24, fill=(200,200,200,255), anchor="mm", shadow_color=(0,0,0,100), shadow_offset=(1,1))

            image_path = os.path.join(IMAGE_DIR, f"{self.userid}-{self.today}.png")

            output = io.BytesIO()
            canvas.save(output, format="PNG")
            img_data = output.getvalue()

            with open(image_path, "wb") as f:
                f.write(img_data)
            
            return img_data

        finally:
            if background: background.close()
            if main_bg: main_bg.close()
            if canvas: canvas.close()
