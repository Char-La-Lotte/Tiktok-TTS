import threading
import queue
import time
import os
import sys
import uuid
import re
import hashlib
import warnings
import subprocess
import collections
import asyncio

from gtts import gTTS
import pygame


# =========================================================
# 📁 RESOURCE PATH
# =========================================================
def resource_path(relative_path):
    try:
        # PyInstaller temp
        base_path = sys._MEIPASS
    except Exception:
        # Python thường
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# =========================================================
# 🔇 REDIRECT CONSOLE - NGĂN CMD HIỆN KHI BUILD .EXE
# =========================================================
if getattr(sys, 'frozen', False):
    try:
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
        sys.stderr = open(os.devnull, 'w', encoding='utf-8')
    except:
        pass


# =========================================================
# 🔥 FIX FFMPEG
# =========================================================
import urllib.request
import zipfile
import io

def download_ffmpeg_if_missing(ffmpeg_path):
    if os.path.exists(ffmpeg_path):
        return
    print("⏳ Đang tải FFmpeg (Lần đầu chạy, vui lòng chờ)...")
    url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    try:
        ffmpeg_dir = os.path.dirname(os.path.dirname(ffmpeg_path)) # data/ffmpeg
        os.makedirs(os.path.join(ffmpeg_dir, "bin"), exist_ok=True)
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as z:
                for file_info in z.infolist():
                    if file_info.filename.endswith("ffmpeg.exe") or file_info.filename.endswith("ffprobe.exe"):
                        file_info.filename = os.path.basename(file_info.filename)
                        z.extract(file_info, os.path.join(ffmpeg_dir, "bin"))
        print("✅ Tải và giải nén FFmpeg thành công!")
    except Exception as e:
        print(f"❌ Lỗi tải FFmpeg: {e}")

FFMPEG_PATH = resource_path("data/ffmpeg/bin/ffmpeg.exe")
FFPROBE_PATH = resource_path("data/ffmpeg/bin/ffprobe.exe")

download_ffmpeg_if_missing(FFMPEG_PATH)

# ENV
os.environ["FFMPEG_BINARY"] = FFMPEG_PATH
os.environ["FFPROBE_BINARY"] = FFPROBE_PATH
os.environ["PATH"] += os.pathsep + os.path.dirname(FFMPEG_PATH)

# Tắt warning pydub
warnings.filterwarnings("ignore", category=RuntimeWarning)


# =========================================================
# 🔇 HIDE CMD WINDOW
# =========================================================
if os.name == "nt":
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    CREATE_NO_WINDOW = 0x08000000
else:
    startupinfo = None
    CREATE_NO_WINDOW = 0

class HiddenPopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = (
            kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
        )
        super().__init__(*args, **kwargs)

subprocess.Popen = HiddenPopen

# =========================================================
# 🎵 PYDUB
# =========================================================
from pydub import AudioSegment
from pydub.audio_segment import AudioSegment as AS

AudioSegment.converter = FFMPEG_PATH
AudioSegment.ffmpeg = FFMPEG_PATH
AudioSegment.ffprobe = FFPROBE_PATH


# =========================================================
# 🔥 PATCH PYDUB
# =========================================================
_original_export = AS.export
_original_from_file = AS.from_file


def silent_from_file(*args, **kwargs):
    kwargs["parameters"] = kwargs.get("parameters", [])
    return _original_from_file(*args, **kwargs)


def silent_export(
    self,
    out_f=None,
    format="mp3",
    codec=None,
    bitrate=None,
    parameters=None,
    tags=None,
    id3v2_version="4",
    cover=None
):
    return _original_export(
        self,
        out_f=out_f,
        format=format,
        codec=codec,
        bitrate=bitrate,
        parameters=parameters,
        tags=tags,
        id3v2_version=id3v2_version,
        cover=cover
    )


AS.export = silent_export
AS.from_file = silent_from_file


# =========================================================
# 📦 TIKTOK LIVE
# =========================================================
from TikTokLive import TikTokLiveClient
from TikTokLive.events import (
    CommentEvent,
    GiftEvent,
    JoinEvent,
    LikeEvent,
    ConnectEvent,
    DisconnectEvent
)

import data_manager as dm


# =========================================================
# 🚀 TIKTOK MANAGER
# =========================================================
class TikTokManager:

    def __init__(self, log_func):
        self.client = None
        self.log_func = log_func

        self.tts_queue = queue.Queue(maxsize=20)

        self.is_paused = False
        self.is_stop = False
        self.can_read = False
        self.is_running = False
        self.intentionally_disconnected = False
        self.reconnect_attempts = 0
        self.current_speed = 1.0          # ← Thêm để quản lý tốc độ
        self.volume = 1.0
        self.connected_username = None

        # =====================================================
        # 🔊 INIT PYGAME
        # =====================================================
        try:
            pygame.mixer.init()
            pygame.mixer.music.set_volume(self.volume)
            self.log_func("")
        except Exception as e:
            self.log_func(f"❌ Không thể khởi tạo pygame mixer: {e}")

        # =====================================================
        # 📂 LOAD DATA
        # =====================================================
        self.abbreviations = dm.load_json(dm.ABBR_FILE)
        self.gift_map = dm.load_json(dm.GIFT_FILE)
        self.emoji_map = dm.load_json(dm.EMOJI_FILE)
        self.blacklist = dm.load_json(dm.BLACKLIST_FILE)

        # =====================================================
        # 🧠 STATE
        # =====================================================
        self.seen_msg_ids = collections.OrderedDict()
        self.recent_comments = collections.OrderedDict()
        self.processed_gifts = {}
        self.gift_buffer = {}
        self.like_buffer = {}
        self.like_last = {}
        self.connection_time = 0.0
        self.is_reconnecting = False
        self.reconnect_timer = None
        self._can_read_timer = None

        # =====================================================
        # 📁 AUDIO DIR
        # =====================================================
        os.makedirs(dm.AUDIO_DIR, exist_ok=True)
        os.makedirs(dm.CACHE_DIR, exist_ok=True)
        self.cache_dir = dm.CACHE_DIR

        # =====================================================
        # 🚀 START TTS THREAD
        # =====================================================
        threading.Thread(target=self._tts_worker, daemon=True).start()

    def _get_user_info(self, e):
        """Safely extract (unique_id, nickname) from event object without triggering
        proto conversion errors. Returns ('', '') on failure."""
        try:
            # Try direct user attribute first (may raise on proto mismatch)
            user = getattr(e, "user", None)
            if user is not None:
                try:
                    uid = getattr(user, "unique_id", None) or getattr(user, "uniqueId", None) or getattr(user, "uid", None)
                    nick = getattr(user, "nickname", None) or getattr(user, "nick_name", None) or getattr(user, "nickName", None) or getattr(user, "display_name", None)
                    if uid or nick:
                        return (uid or "", nick or "")
                except Exception:
                    # fall through to user_info
                    pass

            ui = getattr(e, "user_info", None)
            if ui is not None:
                try:
                    if hasattr(ui, "to_pydict"):
                        d = ui.to_pydict()
                    elif isinstance(ui, dict):
                        d = ui
                    else:
                        # best-effort dict
                        d = {k: getattr(ui, k) for k in dir(ui) if not k.startswith("_")}

                    uid = d.get("uniqueId") or d.get("unique_id") or d.get("uid") or d.get("user_id")
                    nick = d.get("nickName") or d.get("nickname") or d.get("nick_name") or d.get("displayName") or d.get("display_name")
                    return (uid or "", nick or "")
                except Exception:
                    pass

        except Exception:
            pass
        return ("", "")

    # =========================================================
    # 🔊 TTS WORKER (ĐÃ TỐI ƯU)
    # =========================================================
    def _tts_worker(self):
        while True:
            item = self.tts_queue.get()
            if item is None:
                break

            text, speed = item

            if self.is_stop:
                self.tts_queue.task_done()
                continue

            # Bỏ qua nếu text rỗng hoặc không có chữ cái/số nào để đọc (tránh lỗi gTTS No text to speak)
            if not text or not str(text).strip() or not re.search(r"[\w\dÀ-ỹ]", str(text)):
                self.tts_queue.task_done()
                continue

            path_original = None
            path_final = None

            try:
                cache_key = f"{text}|{speed}"
                uid = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
                path_original = os.path.join(self.cache_dir, f"tts_{uid}_raw.mp3")
                path_final = os.path.join(self.cache_dir, f"tts_{uid}_final.mp3")

                if not os.path.exists(path_final):
                    if not os.path.exists(path_original):
                        # Không hiển thị log tạo TTS để tránh spam nhật ký
                        gTTS(text=text, lang="vi").save(path_original)

                    if abs(speed - 1.0) > 0.05:
                        audio = AudioSegment.from_file(path_original, format="mp3")
                        changed_audio = audio._spawn(
                            audio.raw_data,
                            overrides={"frame_rate": int(audio.frame_rate * speed)}
                        ).set_frame_rate(audio.frame_rate)
                        changed_audio.export(path_final, format="mp3", bitrate="192k")
                    else:
                        path_final = path_original
                else:
                    self.log_func("⚡ Tải từ cache TTS")

                pygame.mixer.music.set_volume(self.volume)
                pygame.mixer.music.load(path_final)
                pygame.mixer.music.play()

                while pygame.mixer.music.get_busy():
                    if self.is_stop:
                        pygame.mixer.music.stop()
                        break

                    if self.is_paused:
                        pygame.mixer.music.pause()
                        while self.is_paused and not self.is_stop:
                            time.sleep(0.1)
                        if not self.is_stop:
                            pygame.mixer.music.unpause()

                    time.sleep(0.05)

            except Exception as e:
                self.log_func(f"❌ TTS lỗi: {e}")

            finally:
                try:
                    pygame.mixer.music.stop()
                    if hasattr(pygame.mixer.music, "unload"):
                        pygame.mixer.music.unload()
                except Exception:
                    pass

                # Xóa file cache sau khi phát xong (không giữ cache lâu dài)
                for file_path in [path_original, path_final]:
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                        except Exception:
                            pass

                self.tts_queue.task_done()

    def enqueue_tts(self, text, speed):
        try:
            self.tts_queue.put_nowait((text, speed))
        except queue.Full:
            try:
                self.tts_queue.get_nowait()
                self.tts_queue.task_done()
                self.log_func("⚠️ Hàng đợi TTS quá tải, đã loại bỏ phần cũ nhất.")
            except Exception:
                pass
            try:
                self.tts_queue.put_nowait((text, speed))
            except queue.Full:
                self.log_func("⚠️ Hàng đợi TTS vẫn đầy, bỏ âm thanh mới.")

    def _safe_stop_client(self, client_instance):
        if not client_instance:
            return
        try:
            loop = getattr(client_instance, "_asyncio_loop", None)
            ws = getattr(getattr(client_instance, "_ws", None), "ws", None)
            if ws and loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(ws.close(), loop)
            elif loop and loop.is_running():
                loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass

    def _enable_reading(self):
        self.can_read = True
        self.log_func("🎙️ Bắt đầu đọc tương tác mới...")

    # =========================================================
    # 🔌 CONNECT
    # =========================================================
    def connect(self, username, app_ref):
        self.app_ref = app_ref
        if not username:
            self.log_func("❌ Username không hợp lệ!")
            return False

        username = username.replace("@", "").strip()
        if not username:
            self.log_func("❌ Username không hợp lệ!")
            return False

        self.connected_username = username
        self.log_func(f"👤 Username: {username}")

        # Dừng triệt để client cũ nếu còn hoạt động để tránh chạy song song nhiều luồng
        if self.client:
            self._safe_stop_client(self.client)
            self.client = None

        try:
            self.client = TikTokLiveClient(unique_id=username)
            self.log_func("📡 Đã tạo TikTokLiveClient")
        except Exception as e:
            self.log_func(f"❌ Lỗi tạo client: {e}")
            return False

        self.log_func(f"🔴 Đang kết nối tới: {username}")

        self.is_stop = False
        self.can_read = False
        self.is_running = True
        self.intentionally_disconnected = False

        # =====================================================
        # ✅ CONNECT
        # =====================================================
        @self.client.on(ConnectEvent)
        async def on_connect(e):
            self.reconnect_attempts = 0
            self.is_reconnecting = False
            self.connection_time = time.time()
            self.log_func("✅ CONNECT THÀNH CÔNG")
            if hasattr(app_ref, "on_live_connected"):
                app_ref.on_live_connected(username)

            # Chờ 3.0s sau khi ConnectEvent kích hoạt để TikTok xả hết toàn bộ batch comment cũ của phòng live
            if hasattr(self, "_can_read_timer") and self._can_read_timer:
                try:
                    self._can_read_timer.cancel()
                except Exception:
                    pass
            self._can_read_timer = threading.Timer(3.0, self._enable_reading)
            self._can_read_timer.daemon = True
            self._can_read_timer.start()

        # =====================================================
        # ❌ DISCONNECT
        # =====================================================
        @self.client.on(DisconnectEvent)
        async def on_disconnect(e):
            self.log_func("❌ DISCONNECT")
            if self.is_running and not self.intentionally_disconnected:
                self._attempt_reconnect()

        # =====================================================
        # 💬 COMMENT
        # =====================================================
        @self.client.on(CommentEvent)
        async def on_comment(e):
            if not getattr(app_ref, "sw_comment_var", None):
                return
            if not app_ref.sw_comment_var.get():
                return
            if not self.can_read:
                return

            raw_msg = getattr(e, 'comment', '') or getattr(e, 'content', '') or ''
            if not raw_msg or not str(raw_msg).strip():
                return

            # Kiểm tra thời gian tin nhắn: nếu là tin nhắn cũ sinh ra trước thời điểm kết nối live -> bỏ qua
            common_data = getattr(e, 'common', None)
            create_time = getattr(common_data, 'create_time', 0) if common_data else 0
            if create_time:
                msg_ts = create_time / 1000.0 if create_time > 1e11 else float(create_time)
                if self.connection_time > 0 and msg_ts > 0 and msg_ts < (self.connection_time - 1.0):
                    return

            # Kiểm tra ID tin nhắn để chống lặp tuyệt đối khi mất mạng / nối lại
            msg_id = (getattr(common_data, 'msg_id', None) if common_data else None) or getattr(e, 'id', None)
            if msg_id:
                if msg_id in self.seen_msg_ids:
                    return
                self.seen_msg_ids[msg_id] = time.time()
                if len(self.seen_msg_ids) > 3000:
                    self.seen_msg_ids.popitem(last=False)

            uid, nickname = self._get_user_info(e)
            if not nickname:
                nickname = uid or "Ai đó"

            # Blacklist check
            if any(word.lower() in raw_msg.lower() for word in self.blacklist):
                self.log_func(f"🛡️ Chặn cmt từ {nickname}")
                return

            # Xử lý viết tắt & emoji
            msg = raw_msg
            for k, v in self.abbreviations.items():
                msg = re.sub(rf"\b{k}\b", v, msg, flags=re.I)
            for k, v in self.emoji_map.items():
                msg = msg.replace(k, v)

            clean_msg = msg.strip()
            # Bỏ qua nếu tin nhắn sau khi lọc không có chữ hoặc số nào để phát âm (tránh gTTS No text to speak)
            if not re.search(r"[\w\dÀ-ỹ]", clean_msg):
                self.log_func(f"💬 {nickname}: {raw_msg}")
                return

            # Chống lặp nội dung giống nhau từ cùng 1 người trong vòng 20s
            dup_key = f"{uid or nickname}:{clean_msg}"
            now = time.time()
            if now - self.recent_comments.get(dup_key, 0) < 20:
                return
            self.recent_comments[dup_key] = now
            if len(self.recent_comments) > 3000:
                self.recent_comments.popitem(last=False)

            read_mode = getattr(app_ref, "read_mode_var", None)
            if read_mode and read_mode.get() == "Đọc cả Tên + Nội dung":
                template_var = getattr(app_ref, "tpl_comment_var", None)
                template = template_var.get() if template_var else "{name} nói: {comment}"
                text = template.replace("{name}", nickname).replace("{comment}", clean_msg)
            else:
                text = clean_msg

            self.log_func(f"💬 {nickname}: {clean_msg}")
            self.enqueue_tts(text, self.current_speed)
            if hasattr(app_ref, "register_live_event"):
                app_ref.register_live_event("comment", {"nickname": nickname, "text": clean_msg})

        # =====================================================
        # 🎁 GIFT
        # =====================================================
        @self.client.on(GiftEvent)
        async def on_gift(e):
            if not getattr(app_ref, "sw_gift_var", None):
                return
            if not app_ref.sw_gift_var.get():
                return
            if not self.can_read:
                return

            gift_obj = getattr(e, "gift", None)
            diamond_count = getattr(gift_obj, "diamond_count", 0) if gift_obj else 0
            if diamond_count <= 0:
                return

            # Kiểm tra thời gian quà (bỏ qua quà cũ trong phòng)
            common_data = getattr(e, 'common', None)
            create_time = getattr(common_data, 'create_time', 0) if common_data else 0
            if create_time:
                gift_ts = create_time / 1000.0 if create_time > 1e11 else float(create_time)
                if self.connection_time > 0 and gift_ts > 0 and gift_ts < (self.connection_time - 1.0):
                    return

            uid, nickname = self._get_user_info(e)
            if not nickname:
                nickname = uid or "Ai đó"

            original_gift_name = (getattr(gift_obj, 'name', '') if gift_obj else '') or 'Quà'

            # Auto-add new gifts to map
            if original_gift_name not in self.gift_map and original_gift_name != 'Quà':
                self.gift_map[original_gift_name] = original_gift_name
                dm.save_json(dm.GIFT_FILE, self.gift_map)

            gift_name = self.gift_map.get(original_gift_name, original_gift_name)

            repeat_count = getattr(e, "repeat_count", None) or (getattr(gift_obj, "repeat_count", None) if gift_obj else None) or 1
            repeat_end = getattr(e, "repeat_end", 1)
            is_streaking = getattr(e, "streaking", False)

            gift_key = f"{uid or nickname}:{original_gift_name}"

            # Quà đang trong chuỗi combo và chưa kết thúc (streaking == True hoặc repeat_end == 0)
            if is_streaking or repeat_end == 0:
                if gift_key not in self.gift_buffer:
                    self.gift_buffer[gift_key] = {
                        "count": repeat_count,
                        "nickname": nickname,
                        "gift_name": gift_name,
                        "timer": None
                    }
                else:
                    self.gift_buffer[gift_key]["count"] = max(self.gift_buffer[gift_key]["count"], repeat_count)

                # Reset timer dự phòng nếu TikTok không gửi sự kiện kết thúc chuỗi
                if self.gift_buffer[gift_key]["timer"]:
                    try:
                        self.gift_buffer[gift_key]["timer"].cancel()
                    except Exception:
                        pass

                self.gift_buffer[gift_key]["timer"] = threading.Timer(2.0, self._process_buffered_gift, args=[gift_key, app_ref])
                self.gift_buffer[gift_key]["timer"].daemon = True
                self.gift_buffer[gift_key]["timer"].start()
                return

            # Sự kiện chốt chuỗi combo (repeat_end == 1) hoặc quà đơn không combo
            count = repeat_count
            if gift_key in self.gift_buffer:
                buf = self.gift_buffer.pop(gift_key)
                if buf.get("timer"):
                    try:
                        buf["timer"].cancel()
                    except Exception:
                        pass
                count = max(buf["count"], repeat_count)

            # Kiểm tra chống đọc lặp món quà này với cùng số lượng hoặc nhỏ hơn
            last_processed = self.processed_gifts.get(gift_key, (0, 0))
            if last_processed[0] >= count and (time.time() - last_processed[1] < 10):
                return
            self.processed_gifts[gift_key] = (count, time.time())

            self._dispatch_gift(nickname, gift_name, count, app_ref)

        # =====================================================
        # 👋 JOIN
        # =====================================================
        @self.client.on(JoinEvent)
        async def on_join(e):
            if not getattr(app_ref, "sw_join_var", None):
                return
            if not app_ref.sw_join_var.get():
                return
            if not self.can_read:
                return

            uid, nickname = self._get_user_info(e)
            if not nickname:
                return

            template_var = getattr(app_ref, "tpl_join_var", None)
            template = template_var.get() if template_var else "{name} đã vào phòng"
            text = template.replace("{name}", nickname)
            self.log_func(text)
            self.enqueue_tts(text, self.current_speed)
            if hasattr(app_ref, "register_live_event"):
                app_ref.register_live_event("join", {"nickname": nickname})

        # =====================================================
        # ❤️ LIKE
        # =====================================================
        @self.client.on(LikeEvent)
        async def on_like(e):
            if not getattr(app_ref, "sw_like_var", None):
                return
            if not app_ref.sw_like_var.get():
                return
            if not self.can_read:
                return

            uid, nickname = self._get_user_info(e)
            if not nickname:
                return

            uid_key = uid or getattr(e, 'user_id', None) or getattr(e, 'user_id_str', None) or nickname
            self.like_buffer[uid_key] = self.like_buffer.get(uid_key, 0) + getattr(e, "like_count", 1)

            if time.time() - self.like_last.get(uid_key, 0) >= 5:
                template_var = getattr(app_ref, "tpl_like_var", None)
                template = template_var.get() if template_var else "{name} thả {count} tim"
                text = template.replace("{name}", nickname).replace("{count}", str(self.like_buffer[uid_key]))
                self.log_func(text)
                self.enqueue_tts(text, self.current_speed)
                self.like_last[uid_key] = time.time()
                self.like_buffer[uid_key] = 0
                if hasattr(app_ref, "register_live_event"):
                    app_ref.register_live_event("like", {"nickname": nickname, "count": getattr(e, "like_count", 1)})

        # =====================================================
        # 🚀 START CLIENT
        # =====================================================
        self.log_func("🧵 Đang tạo thread client...")
        self._start_client_thread()
        return True

    def _process_buffered_gift(self, gift_key, app_ref):
        data = self.gift_buffer.pop(gift_key, None)
        if not data:
            return
        count = data["count"]
        nick = data["nickname"]
        gname = data["gift_name"]

        last_processed = self.processed_gifts.get(gift_key, (0, 0))
        if last_processed[0] >= count and (time.time() - last_processed[1] < 10):
            return
        self.processed_gifts[gift_key] = (count, time.time())

        self._dispatch_gift(nick, gname, count, app_ref)

    def _dispatch_gift(self, nick, gname, count, app_ref):
        display_gift = f"{count} {gname}" if count > 1 else gname
        template_var = getattr(app_ref, "tpl_gift_var", None)
        template = template_var.get() if template_var else "{name} tặng {gift}"
        text = template.replace("{name}", nick).replace("{gift}", display_gift)

        self.log_func(text)
        self.enqueue_tts(text, self.current_speed)

        if hasattr(app_ref, "register_live_event"):
            app_ref.register_live_event("gift", {"nickname": nick, "gift": display_gift, "count": count})

    # =========================================================
    # 🚀 START THREAD
    # =========================================================
    def _start_client_thread(self):
        threading.Thread(target=self._run_client, daemon=True).start()

    # =========================================================
    # 🧠 RUN CLIENT
    # =====================================================
    def _run_client(self):
        try:
            self.log_func("🚀 Đang chạy client...")
            # process_connect_events=False: BỎ QUA TOÀN BỘ BÌNH LUẬN VÀ QUÀ LỊCH SỬ KHI VỪA VÀO PHÒNG
            self.client.run(process_connect_events=False)
        except Exception as e:
            self.log_func(f"❌ Lỗi client: {e}")
            if self.is_running and not self.intentionally_disconnected:
                self._attempt_reconnect()

    def _attempt_reconnect(self):
        if not self.is_running or self.intentionally_disconnected:
            return
        if self.is_reconnecting:
            return
        self.is_reconnecting = True

        self.reconnect_attempts += 1
        delay = min(15, 2 + self.reconnect_attempts * 2)
        self.log_func(f"🔄 Mất kết nối. Tự nối lại sau {delay}s... (Lần {self.reconnect_attempts})")

        if self.reconnect_timer:
            try:
                self.reconnect_timer.cancel()
            except Exception:
                pass

        def do_reconnect():
            self.is_reconnecting = False
            if self.is_running and not self.intentionally_disconnected:
                if self.client:
                    self._safe_stop_client(self.client)
                    self.client = None
                self.connect(self.connected_username, getattr(self, "app_ref", None))

        self.reconnect_timer = threading.Timer(delay, do_reconnect)
        self.reconnect_timer.daemon = True
        self.reconnect_timer.start()

    # =========================================================
    # 🔌 DISCONNECT
    # =========================================================
    def disconnect(self):
        self.intentionally_disconnected = True
        self.is_running = False
        self.can_read = False
        self.is_reconnecting = False

        if self.reconnect_timer:
            try:
                self.reconnect_timer.cancel()
            except Exception:
                pass
            self.reconnect_timer = None

        if hasattr(self, "_can_read_timer") and self._can_read_timer:
            try:
                self._can_read_timer.cancel()
            except Exception:
                pass
            self._can_read_timer = None

        # Hủy các gift buffer timer còn sót
        for item in self.gift_buffer.values():
            if item.get("timer"):
                try:
                    item["timer"].cancel()
                except Exception:
                    pass
        self.gift_buffer.clear()

        self.stop_tts_now()
        if self.client:
            self._safe_stop_client(self.client)
            self.client = None
            self.log_func("🔌 Đã ngắt kết nối.")

    # =========================================================
    # ⏸ TOGGLE PAUSE
    # =========================================================
    def toggle_pause(self):
        self.is_paused = not self.is_paused
        return self.is_paused

    # =========================================================
    # 🛑 STOP TTS
    # =========================================================
    def stop_tts_now(self):
        self.is_stop = True
        try:
            pygame.mixer.music.stop()
            while not self.tts_queue.empty():
                try:
                    self.tts_queue.get_nowait()
                    self.tts_queue.task_done()
                except Exception:
                    break
        except Exception:
            pass
        self.is_stop = False
        self.log_func("🛑 Đã dừng toàn bộ TTS.")

    # =========================================================
    # ⚡ SET SPEED (MỚI THÊM)
    # =========================================================
    def set_speed(self, speed: float):
        self.current_speed = round(speed, 1)

    # =========================================================
    # 🔊 SET VOLUME
    # =========================================================
    def set_volume(self, volume: float):
        self.volume = max(0.0, min(1.0, float(volume)))
        try:
            pygame.mixer.music.set_volume(self.volume)
        except Exception:
            pass