import warnings
import os
import sys

# Suppress noise warnings before any library imports
warnings.filterwarnings("ignore")
os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.qpa.*=false"
os.environ["PYTHONWARNINGS"] = "ignore"

import time
import threading
import tempfile
import wave
import logging
import urllib.parse
import requests
import re

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("app.log", encoding='utf-8')]
)

# TTS and translation imports
import asyncio
import edge_tts
import pygame
try:
    import sounddevice as sd
    import numpy as np
    VOICE_AVAILABLE = True
except ImportError:
    VOICE_AVAILABLE = False

import pyperclip
import uiautomation as auto

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QProgressBar, QTextEdit, QLabel,
    QMessageBox, QInputDialog, QLineEdit, QSystemTrayIcon, QMenu, QStyle
)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QObject, QTimer, QSettings
from PySide6.QtGui import QFont, QAction, QTextCursor

import google.generativeai as genai

# Edge TTS handles Unicode natively — no text preprocessing needed.

# ---------------------------------------------------------------------------
# Language configuration
# ---------------------------------------------------------------------------
LANGUAGES = {
    "RU": {"name": "Russian",  "code": "ru", "voice": "ru-RU-SvetlanaNeural"},
    "EN": {"name": "English",  "code": "en", "voice": "en-US-JennyNeural"},
    "ES": {"name": "Spanish",  "code": "es", "voice": "es-ES-ElviraNeural"},
    "JA": {"name": "Japanese", "code": "ja", "voice": "ja-JP-NanamiNeural"},
    "KO": {"name": "Korean",   "code": "ko", "voice": "ko-KR-SunHiNeural"},
    "FR": {"name": "French",   "code": "fr", "voice": "fr-FR-DeniseNeural"},
    "DE": {"name": "German",   "code": "de", "voice": "de-DE-KatjaNeural"},
}

# Path to local config file (same directory as the script)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")


class ThemedComboBox(QComboBox):
    """QComboBox with styled dark popup that expands to all items without jumping."""

    _ITEM_STYLE = """
        QAbstractItemView {
            background-color: #444455;
            border: none;
            border-radius: 0;
            outline: none;
        }
        QAbstractItemView::item {
            background-color: #1e1e28;
            color: #b0b0cc;
            padding: 4px 8px;
            border: none;
            border-radius: 0;
        }
        QAbstractItemView::item:hover {
            background-color: #2a2a3a;
            color: #e0e0ff;
        }
    """

    def showPopup(self):
        v = self.view()

        from PySide6.QtWidgets import QFrame
        v.setFrameShape(QFrame.Shape.NoFrame)
        v.setLineWidth(0)

        v.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Apply style before measuring so sizeHintForRow() is accurate
        v.setStyleSheet(self._ITEM_STYLE)

        # 1px border: view's bg (#444455) peeks through the 1px viewport gap
        v.setViewportMargins(1, 1, 1, 1)

        row_h = v.sizeHintForRow(0) or 28
        v.setMinimumHeight(row_h * self.count() + 2)
        self.setMaxVisibleItems(self.count())

        # Force popup wider than the compact widget so items aren't clipped
        v.setMinimumWidth(80)
        super().showPopup()
        v.setMinimumHeight(0)
        v.setMinimumWidth(0)

        # ONLY fix border-radius — viewportMargins already handles the actual border
        QTimer.singleShot(0, self._fix_roundness)

    def _fix_roundness(self):
        """Remove border-radius and trim any extra bottom padding."""
        v = self.view()
        container = v.parentWidget()
        if not container:
            return
        container.setStyleSheet("QFrame { border-radius: 0px; }")
        if container.layout():
            container.layout().setContentsMargins(0, 0, 0, 0)

        # Shrink-only: trim extra bottom pixels without causing repositioning jump
        row_h = v.sizeHintForRow(0) or 28
        needed = row_h * self.count() + 2
        if container.height() > needed:
            container.setFixedHeight(needed)




def google_translate_free(text, target_lang='ru'):
    try:
        url = ("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl="
               + target_lang + "&dt=t&q=" + urllib.parse.quote(text))
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            result = response.json()
            return "".join([sentence[0] for sentence in result[0]])
        return f"Error: {response.status_code}"
    except Exception as e:
        return f"Translation Error: {str(e)}"


def get_api_key():
    settings = QSettings("CassandraCorp", "CassandraComposite")
    key = settings.value("gemini_api_key", "")
    if not key:
        key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        text, ok = QInputDialog.getText(
            None, "Gemini API Key", "Enter your Gemini API Key:", QLineEdit.Normal, ""
        )
        if ok and text.strip():
            key = text.strip()
            settings.setValue("gemini_api_key", key)
        else:
            sys.exit(1)
    return key


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class VoiceWorker(QObject):
    """Text-to-speech via Edge TTS Cloud."""
    model_loaded = Signal()
    finished_speaking = Signal()

    def __init__(self):
        super().__init__()
        self.voice = "ru-RU-SvetlanaNeural"
        self.speed = "+15%"
        self.is_speaking = False
        self._stop_requested = False
        self._current_text = ""
        self._state_lock = threading.Lock()
        self._process_lock = threading.Lock()
        
        # Init pygame mixer for audio playback
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            QTimer.singleShot(500, self.model_loaded.emit)
        except Exception as e:
            logging.error(f"Failed to init pygame.mixer: {e}")

    def stop(self):
        with self._state_lock:
            self._stop_requested = True
        try:
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
        except:
            pass

    def speak(self, text):
        text = text.strip()
        if not text:
            return

        # Guard 1: already speaking the same text — ignore (debounce protection)
        with self._state_lock:
            if self.is_speaking and self._current_text == text:
                return

        # Guard 2: new text — stop the current playback first
        self.stop()

        with self._process_lock:
            with self._state_lock:
                self.is_speaking = True
                self._stop_requested = False
                self._current_text = text

            try:
                # Generate audio into a temp file then play it
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_file:
                    output_path = tmp_file.name
                
                async def _gen():
                    communicate = edge_tts.Communicate(text, self.voice, rate=self.speed)
                    await communicate.save(output_path)
                
                asyncio.run(_gen())
                
                # Check stop flag BEFORE starting playback
                with self._state_lock:
                    should_play = not self._stop_requested and os.path.exists(output_path)
                
                if should_play:
                    pygame.mixer.music.load(output_path)
                    pygame.mixer.music.play()
                    while pygame.mixer.music.get_busy():
                        with self._state_lock:
                            if self._stop_requested:
                                break
                        time.sleep(0.1)
                    
                    pygame.mixer.music.unload()
                    try:
                        os.remove(output_path)
                    except:
                        pass
            except Exception as e:
                logging.error(f"TTS Error: {e}")
            finally:
                with self._state_lock:
                    self.is_speaking = False
                self.finished_speaking.emit()


class AudioRecorderThread(QThread):
    """Records audio from the microphone."""
    finished = Signal(str)
    progress_update = Signal(float)

    def __init__(self, duration_limit=10):
        super().__init__()
        self.duration_limit = duration_limit
        self.is_running = False

    def run(self):
        try:
            self.is_running = True
            audio_data = []
            start_time = time.time()
            with sd.InputStream(samplerate=44100, channels=1, dtype='int16') as stream:
                while self.is_running:
                    elapsed = time.time() - start_time
                    if elapsed >= self.duration_limit:
                        break
                    data, _ = stream.read(1024)
                    audio_data.append(data)
                    self.progress_update.emit(elapsed)
            if not audio_data:
                return
            fd, path = tempfile.mkstemp(suffix='.wav')
            os.close(fd)
            with wave.open(path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(np.concatenate(audio_data, axis=0).tobytes())
            self.finished.emit(path)
        except Exception:
            pass
        finally:
            self.is_running = False

    def stop(self):
        self.is_running = False


class GeminiWorker(QThread):
    """Audio transcription or text formatting via Gemini."""
    result_ready = Signal(str)

    def __init__(self, task_type="transcribe", data=None, target_lang=None):
        super().__init__()
        self.task_type = task_type
        self.data = data
        self.target_lang = target_lang

    def run(self):
        try:
            model = genai.GenerativeModel("gemini-2.0-flash")
            if self.task_type == "transcribe":
                f = genai.upload_file(path=self.data)
                while f.state.name == "PROCESSING":
                    time.sleep(1)
                    f = genai.get_file(f.name)
                if self.target_lang:
                    prompt = (
                        f"Transcribe this audio and translate the result to {self.target_lang}. "
                        "Return ONLY the final translated text. No original text, no explanations."
                    )
                else:
                    prompt = "Transcribe this audio to text as accurately as possible"
                res = model.generate_content([prompt, f])
                text = (res.text or "").strip()
                self.result_ready.emit(text if text else "Error: model returned empty response")
                genai.delete_file(f.name)
                if os.path.exists(self.data):
                    os.remove(self.data)
            else:
                prompt = (
                    "You are a voice transcription editor. "
                    "Receive a text dictated via microphone and return ONLY one final version — no alternatives, no explanations, no comments.\n"
                    "What to do:\n"
                    "- Remove repeated words and phrases\n"
                    "- Remove filler words (uh, um, well, like, you know, etc.)\n"
                    "- Remove non-verbal sounds and slips (hmm, ah, oops, coughing, etc.)\n"
                    "- Fix incomplete or broken sentences\n"
                    "- Preserve the original meaning and order of thoughts\n"
                    "- Make the text readable and coherent\n\n"
                    f"Text: {self.data}"
                )
                res = model.generate_content(prompt)
                text = (res.text or "").strip()
                self.result_ready.emit(text if text else "Error: model returned empty response")
        except Exception as e:
            logging.error(f"GeminiWorker error ({self.task_type}): {e}")
            self.result_ready.emit(f"Error: {str(e)}")


class TranslationWorker(QThread):
    """Captures selected text via UI Automation."""
    text_captured = Signal(str)

    def __init__(self, debounce_ms=500):
        super().__init__()
        self.debounce_ms = debounce_ms / 1000.0
        self.last_text = ""
        self.running = True

    def run(self):
        MY_PID = os.getpid()
        with auto.UIAutomationInitializerInThread():
            while self.running:
                try:
                    control = auto.GetFocusedControl()
                    # Skip our own window to avoid infinite translation loop
                    if control and control.ProcessId != MY_PID:
                        try:
                            text_pattern = control.GetTextPattern()
                            if text_pattern:
                                selections = text_pattern.GetSelection()
                                if selections:
                                    captured = selections[0].GetText().strip()
                                    if len(captured) >= 2 and captured != self.last_text:
                                        time.sleep(self.debounce_ms)
                                        final_control = auto.GetFocusedControl()
                                        if final_control:
                                            final_pattern = final_control.GetTextPattern()
                                            if final_pattern:
                                                final_sel = final_pattern.GetSelection()
                                                if final_sel and final_sel[0].GetText().strip() == captured:
                                                    self.last_text = captured
                                                    self.text_captured.emit(captured)
                        except Exception:
                            pass
                except Exception as e:
                    logging.error(f"Critical error in capture loop: {e}")
                time.sleep(0.5)


class ClipboardWorker(QThread):
    """Monitors the clipboard — reacts to Ctrl+C in any application."""
    text_captured = Signal(str)

    def __init__(self):
        super().__init__()
        self.last_text = ""
        self.running = True

    def run(self):
        while self.running:
            try:
                text = pyperclip.paste().strip()
                if len(text) >= 2 and text != self.last_text:
                    self.last_text = text
                    self.text_captured.emit(text)
            except Exception:
                pass
            time.sleep(0.3)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class CassandraApp(QMainWindow):
    update_translation_signal = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CASSANDRA COMPOSITE")
        self.setWindowFlags(Qt.WindowStaysOnTopHint)
        self.resize(520, 680)
        self.setMinimumSize(520, 680)

        self.is_recording = False
        self.recording_limit = 10
        self.voice_enabled = True
        self.auto_translate = False  # Disabled by default — allows manual editing of the field
        self._user_closed = False
        self._translate_id = 0
        self._last_captured = ""
        self._last_spoken = ""        # last spoken text — prevents repeat TTS for the same content
        self.current_lang = LANGUAGES["RU"]  # active output language (translation target + TTS voice)
        self._transcribe_worker = None  # keep ref so GC doesn't kill a live thread
        self._format_worker = None      # same for formatting

        self.voice_worker = VoiceWorker()
        self.voice_worker.model_loaded.connect(self._on_voice_ready)

        self.setup_ui()
        self.setup_tray()

        # Start text capture workers
        self.translation_worker = TranslationWorker()
        self.translation_worker.text_captured.connect(self.on_text_captured)
        self.clipboard_worker = ClipboardWorker()
        self.clipboard_worker.text_captured.connect(self.on_text_captured)
        self.update_translation_signal.connect(self._update_translation_ui)
        self.translation_worker.start()
        self.clipboard_worker.start()
        self._load_settings()  # restore persisted state

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def setup_ui(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #0f0f12; }
            QWidget#central {
                background-color: rgba(20, 20, 25, 255);
                border: 1px solid rgba(0, 212, 255, 100);
            }
            QLabel {
                color: #00d4ff;
                font-family: 'Segoe UI';
                font-size: 13px;
            }
            QLabel#section_label {
                color: #555;
                font-size: 10px;
                font-family: 'Segoe UI';
                letter-spacing: 1px;
            }
            QPushButton {
                background: rgba(40, 40, 50, 200);
                border: 1px solid #444;
                border-radius: 8px;
                color: #ccc;
                font-size: 12px;
                padding: 8px;
            }
            QPushButton:hover {
                background: rgba(60, 60, 80, 255);
                color: #fff;
                border: 1px solid #00d4ff;
            }
            QPushButton[active="true"] {
                border: 1px solid #00d4ff;
                color: #00d4ff;
                background: rgba(0, 212, 255, 30);
            }
            QPushButton#recordBtn[recording="true"] {
                background: rgba(255, 50, 50, 150);
                border: 1px solid #ff0000;
                color: white;
            }
            QPushButton#iconBtn {
                border-radius: 8px;
                font-family: 'Segoe UI Symbol';
                font-size: 18px;
                padding: 0px;
                color: #888;
            }
            QPushButton#iconBtn:hover {
                color: #ff5555;
                border: 1px solid #ff5555;
            }
            QPushButton#actionBtn {
                border-radius: 8px;
                font-family: 'Segoe UI';
                font-size: 13px;
                padding: 0px 12px;
                color: #aaa;
                letter-spacing: 0.5px;
            }
            QPushButton#actionBtn:hover {
                color: #fff;
                border: 1px solid #00d4ff;
            }
            QComboBox {
                background: #1e1e28;
                border: 1px solid #444;
                border-radius: 5px;
                color: #fff;
                padding: 5px;
            }
            QComboBox::drop-down { width: 0px; border: none; }
            QComboBox#langBox {
                padding: 5px 8px;
            }
            QProgressBar {
                border: 1px solid #444;
                border-radius: 10px;
                text-align: center;
                background: #1e1e28;
                color: transparent;
                height: 10px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #00d4ff, stop:1 #0055ff
                );
                border-radius: 10px;
            }
            QTextEdit {
                background: rgba(30, 30, 40, 180);
                border: 1px solid rgba(255, 255, 255, 15);
                border-radius: 10px;
                color: #eee;
                font-family: 'Segoe UI';
                font-size: 13px;
                padding: 10px;
            }
        """)

        c = QWidget()
        c.setObjectName("central")
        self.setCentralWidget(c)
        root = QVBoxLayout(c)
        root.setContentsMargins(10, 15, 10, 8)
        root.setSpacing(10)

        # ── Header ─────────────────────────────────────────────────────
        hdr = QHBoxLayout()

        # Title + subtitle block
        title_vbox = QVBoxLayout()
        title_vbox.setSpacing(1)
        title_vbox.setContentsMargins(0, 0, 0, 0)
        title_main = QLabel("CASSANDRA")
        title_main.setFont(QFont("Segoe UI Black", 14))
        title_sub = QLabel("AUDIO CAPTURE")
        title_sub.setObjectName("section_label")
        title_vbox.addWidget(title_main)
        title_vbox.addWidget(title_sub)

        self.btn_auto = QPushButton("AUTO OFF")
        self.btn_auto.setCheckable(True)
        self.btn_auto.setChecked(False)
        self.btn_auto.setProperty("active", "false")
        self.btn_auto.setFixedWidth(90)
        self.btn_auto.clicked.connect(self.toggle_auto)

        self.btn_voice = QPushButton("VOICE ON")
        self.btn_voice.setCheckable(True)
        self.btn_voice.setChecked(True)
        self.btn_voice.setProperty("active", "true")
        self.btn_voice.setFixedWidth(90)
        self.btn_voice.clicked.connect(self.toggle_voice)

        # Language selector — same as limit_box, just narrower
        self.lang_box = ThemedComboBox()
        self.lang_box.setObjectName("langBox")
        self.lang_box.addItems(list(LANGUAGES.keys()))
        self.lang_box.setCurrentText("RU")
        self.lang_box.setFixedWidth(46)
        self.lang_box.setToolTip("Output language (translation + TTS voice)")
        self.lang_box.currentTextChanged.connect(self.on_language_changed)

        # MIC → LANG translate toggle
        self.btn_mic_tr = QPushButton("MIC→")
        self.btn_mic_tr.setCheckable(True)
        self.btn_mic_tr.setChecked(False)
        self.btn_mic_tr.setProperty("active", "false")
        self.btn_mic_tr.setFixedWidth(65)
        self.btn_mic_tr.setToolTip("Translate mic transcription to selected language")
        self.btn_mic_tr.clicked.connect(self._on_mic_translate_toggled)

        hdr.addLayout(title_vbox)
        hdr.addStretch()
        hdr.addWidget(self.lang_box)
        hdr.addWidget(self.btn_mic_tr)
        hdr.addWidget(self.btn_auto)
        hdr.addWidget(self.btn_voice)
        root.addLayout(hdr)

        # ── Separator line ───────────────────────────────────────
        from PySide6.QtWidgets import QFrame
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Plain)
        sep.setStyleSheet("QFrame { color: #444; }")
        sep.setFixedHeight(1)
        root.addWidget(sep)

        # ── Audio capture controls ─────────────────────────────

        rec_row = QHBoxLayout()
        lbl_limit = QLabel("Limit:")
        self.limit_box = ThemedComboBox()
        self.limit_box.setFixedWidth(85)
        self.limit_box.addItems(["10 sec", "20 sec", "30 sec", "60 sec"])
        self.limit_box.currentIndexChanged.connect(
            lambda idx: (
                setattr(self, 'recording_limit',
                        int(self.limit_box.currentText().split()[0])),
                self._save_settings()
            )
        )
        self.rec_btn = QPushButton("START RECORDING")
        self.rec_btn.setObjectName("recordBtn")
        self.rec_btn.clicked.connect(self.toggle_rec)

        rec_row.addWidget(lbl_limit)
        rec_row.addWidget(self.limit_box)
        rec_row.addWidget(self.rec_btn, 1)
        root.addLayout(rec_row)

        self.pbar = QProgressBar()
        self.pbar.setValue(0)
        root.addWidget(self.pbar)

        # ── Transcription / translation text field ─────────────────────
        lbl_tr = QLabel("TRANSCRIPTION / TRANSLATION")
        lbl_tr.setObjectName("section_label")
        root.addWidget(lbl_tr)

        self.txt = QTextEdit()
        self.txt.setPlaceholderText("Transcription and translation will appear here...")
        root.addWidget(self.txt)

        # ── Button row ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self.fb = QPushButton("BUSINESS STYLE")
        self.fb.setObjectName("actionBtn")
        self.fb.setFixedHeight(40)
        self.fb.clicked.connect(self.format_txt)

        self.clr_btn = QPushButton("\u2715")
        self.clr_btn.setObjectName("iconBtn")
        self.clr_btn.setFixedSize(36, 36)
        self.clr_btn.setToolTip("Clear field and buffer")
        self.clr_btn.clicked.connect(self.clear_all)

        self.buf_btn = QPushButton("COPY")
        self.buf_btn.setObjectName("actionBtn")
        self.buf_btn.setFixedHeight(40)
        self.buf_btn.clicked.connect(self._copy_to_clipboard)

        btn_row.addWidget(self.fb)
        btn_row.addWidget(self.clr_btn)
        btn_row.addWidget(self.buf_btn)
        root.addLayout(btn_row)

        # ── Status bar ─────────────────────────────────────────────────
        self.status_lbl = QLabel("Loading voice model...")
        self.status_lbl.setStyleSheet("color: #555; font-size: 10px;")
        root.addWidget(self.status_lbl)

    def setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.style().standardIcon(QStyle.SP_DialogHelpButton))
        menu = QMenu()
        menu.addAction("Show").triggered.connect(self._show_window)
        menu.addAction("Hide").triggered.connect(self._hide_window)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_window()

    # ------------------------------------------------------------------
    # UI slots
    # ------------------------------------------------------------------

    @Slot()
    def _on_voice_ready(self):
        self.status_lbl.setText("Voice engine ready")

    def toggle_auto(self):
        self.auto_translate = self.btn_auto.isChecked()
        self.btn_auto.setText("AUTO ON" if self.auto_translate else "AUTO OFF")
        self.btn_auto.setProperty("active", "true" if self.auto_translate else "false")
        self.btn_auto.style().unpolish(self.btn_auto)
        self.btn_auto.style().polish(self.btn_auto)
        # Reset deduplicator on enable so it reacts to the current buffer immediately
        if self.auto_translate:
            self._last_captured = ""
        self._save_settings()

    def toggle_voice(self):
        self.voice_enabled = self.btn_voice.isChecked()
        self.btn_voice.setText("VOICE ON" if self.voice_enabled else "VOICE OFF")
        self.btn_voice.setProperty("active", "true" if self.voice_enabled else "false")
        self.btn_voice.style().unpolish(self.btn_voice)
        self.btn_voice.style().polish(self.btn_voice)
        if not self.voice_enabled:
            self.voice_worker.stop()
        self._save_settings()

    def _on_mic_translate_toggled(self):
        active = self.btn_mic_tr.isChecked()
        lang_name = self.current_lang["name"] if active else ""
        self.btn_mic_tr.setText("MIC→" if not active else f"MIC→{self.lang_box.currentText()}")
        self.btn_mic_tr.setProperty("active", "true" if active else "false")
        self.btn_mic_tr.style().unpolish(self.btn_mic_tr)
        self.btn_mic_tr.style().polish(self.btn_mic_tr)
        self._save_settings()

    def on_language_changed(self, key: str):
        """Switch output language: translation target + TTS voice."""
        if key not in LANGUAGES:
            return
        self.current_lang = LANGUAGES[key]
        self.voice_worker.voice = self.current_lang["voice"]
        self.voice_worker.stop()
        self._last_spoken = ""  # reset so next translation is spoken with the new voice
        self.status_lbl.setText(f"Language: {self.current_lang['name']}")
        # Update MIC→ label if translate mode is active
        if self.btn_mic_tr.isChecked():
            self.btn_mic_tr.setText(f"MIC→{key}")
        self._save_settings()

    def _load_settings(self):
        """Restore all persisted UI state from config.ini."""
        s = QSettings(CONFIG_PATH, QSettings.Format.IniFormat)

        # Language
        lang = s.value("language", "RU")
        if lang in LANGUAGES:
            self.lang_box.setCurrentText(lang)  # fires on_language_changed

        # Auto-translate
        auto = s.value("auto_translate", False, type=bool)
        self.auto_translate = auto
        self.btn_auto.setChecked(auto)
        self.btn_auto.setText("AUTO ON" if auto else "AUTO OFF")
        self.btn_auto.setProperty("active", "true" if auto else "false")
        self.btn_auto.style().unpolish(self.btn_auto)
        self.btn_auto.style().polish(self.btn_auto)
        if auto:
            self._last_captured = ""

        # Voice
        voice = s.value("voice_enabled", True, type=bool)
        self.voice_enabled = voice
        self.btn_voice.setChecked(voice)
        self.btn_voice.setText("VOICE ON" if voice else "VOICE OFF")
        self.btn_voice.setProperty("active", "true" if voice else "false")
        self.btn_voice.style().unpolish(self.btn_voice)
        self.btn_voice.style().polish(self.btn_voice)
        if not voice:
            self.voice_worker.stop()

        # Recording limit
        limit = s.value("recording_limit", 10, type=int)
        self.recording_limit = limit
        _limit_vals = [10, 20, 30, 60]
        idx = _limit_vals.index(limit) if limit in _limit_vals else 0
        self.limit_box.blockSignals(True)
        self.limit_box.setCurrentIndex(idx)
        self.limit_box.blockSignals(False)

        # MIC translate
        mic_tr = s.value("mic_translate", False, type=bool)
        self.btn_mic_tr.setChecked(mic_tr)
        key = self.lang_box.currentText()
        self.btn_mic_tr.setText(f"MIC→{key}" if mic_tr else "MIC→")
        self.btn_mic_tr.setProperty("active", "true" if mic_tr else "false")
        self.btn_mic_tr.style().unpolish(self.btn_mic_tr)
        self.btn_mic_tr.style().polish(self.btn_mic_tr)

    def _save_settings(self):
        """Persist current UI state to config.ini."""
        s = QSettings(CONFIG_PATH, QSettings.Format.IniFormat)
        s.setValue("language",         self.lang_box.currentText())
        s.setValue("auto_translate",   self.auto_translate)
        s.setValue("voice_enabled",    self.voice_enabled)
        s.setValue("recording_limit",  self.recording_limit)
        s.setValue("mic_translate",    self.btn_mic_tr.isChecked())

    def toggle_rec(self):
        if not self.is_recording:
            self.is_recording = True
            self.rec_btn.setText("STOP")
            self.rec_btn.setProperty("recording", "true")
            self.rec_btn.style().unpolish(self.rec_btn)
            self.rec_btn.style().polish(self.rec_btn)
            self.pbar.setValue(0)
            self.recorder = AudioRecorderThread(self.recording_limit)
            self.recorder.progress_update.connect(
                lambda e: self.pbar.setValue(int((e / self.recording_limit) * 100))
            )
            self.recorder.finished.connect(self._on_rec_finished)
            self.recorder.start()
        else:
            self.recorder.stop()
            self.is_recording = False
            self.rec_btn.setText("WAIT...")
            self.rec_btn.setEnabled(False)

    def _on_rec_finished(self, path):
        self.is_recording = False
        self.rec_btn.setProperty("recording", "false")
        self.rec_btn.style().unpolish(self.rec_btn)
        self.rec_btn.style().polish(self.rec_btn)
        self.rec_btn.setText("THINKING...")
        self.rec_btn.setEnabled(False)
        self.pbar.setValue(0)  # reset progress bar immediately after recording ends
        target = self.current_lang["name"] if self.btn_mic_tr.isChecked() else None
        self._transcribe_worker = GeminiWorker("transcribe", path, target_lang=target)
        self._transcribe_worker.result_ready.connect(self._append_transcription)
        self._transcribe_worker.start()

    def _append_transcription(self, new_text):
        self.rec_btn.setText("START RECORDING")
        self.rec_btn.setEnabled(True)
        if "\u041e\u0448\u0438\u0431\u043a\u0430:" in new_text:
            QMessageBox.critical(self, "Gemini Error", new_text)
            return
        current = self.txt.toPlainText().strip()
        if current:
            self.txt.setPlainText(current + "\n\n" + new_text.strip())
        else:
            self.txt.setPlainText(new_text.strip())
        self.txt.moveCursor(QTextCursor.MoveOperation.End)
        self.status_lbl.setText("Transcription done")
        # Transcription is NOT spoken — the user just said it themselves.
        # TTS is only used for translations (AUTO mode).

    def format_txt(self):
        t = self.txt.toPlainText()
        if not t.strip():
            return
        # Guard against double-click while a previous request is still running
        if self._format_worker is not None and self._format_worker.isRunning():
            return
        self.fb.setText("STYLING...")
        self.fb.setEnabled(False)
        worker = GeminiWorker("format", t)
        worker.result_ready.connect(self._on_format_done)
        self._format_worker = worker   # keep ref — otherwise GC may kill a live thread
        worker.start()

    @Slot(str)
    def _on_format_done(self, res):
        self.fb.setText("BUSINESS STYLE")
        self.fb.setEnabled(True)
        if "\u041e\u0448\u0438\u0431\u043a\u0430:" in res:
            QMessageBox.critical(self, "Gemini Error", res)
            return
        self.txt.setPlainText(res)
        self._format_worker = None

    def clear_all(self):
        self.txt.clear()
        pyperclip.copy("")
        self._last_captured = ""
        self._last_spoken = ""  # reset so re-selecting the same text triggers TTS again
        # Reset cache in workers too so they can pick up the same text anew
        self.translation_worker.last_text = ""
        self.clipboard_worker.last_text = ""
        
        self.clr_btn.setText("\u2713")
        QTimer.singleShot(1000, lambda: self.clr_btn.setText("\u2715"))
        self.status_lbl.setText("Cleared")

    def _copy_to_clipboard(self):
        pyperclip.copy(self.txt.toPlainText())
        self.buf_btn.setText("COPIED")
        QTimer.singleShot(1500, lambda: self.buf_btn.setText("COPY"))

    # ------------------------------------------------------------------
    # Text capture and translation
    # ------------------------------------------------------------------

    @Slot(str)
    def on_text_captured(self, text):
        """Called when text is selected or copied in any application."""
        # If AUTO is off — don't interfere with manual editing
        if not self.auto_translate:
            return
        if self._user_closed:
            return
        # Deduplication: TranslationWorker and ClipboardWorker may emit the same text
        if text == self._last_captured:
            return
        self._last_captured = text

        self._translate_id += 1
        current_id = self._translate_id

        self.status_lbl.setText(f"Translating: {text[:50]}...")
        self.txt.setPlainText("Translating...")

        def do_translate():
            result = google_translate_free(text, self.current_lang["code"])
            if current_id == self._translate_id:
                self.update_translation_signal.emit(result)

        threading.Thread(target=do_translate, daemon=True).start()

    @Slot(str)
    def _update_translation_ui(self, translated_text):
        self.txt.setPlainText(translated_text)
        self.status_lbl.setText("Translation done")
        if self.voice_enabled:
            # Repeat guard: don't start TTS if this text was already spoken
            if translated_text == self._last_spoken:
                return
            self._last_spoken = translated_text
            self.voice_worker.stop()
            threading.Thread(target=self.voice_worker.speak,
                             args=(translated_text,), daemon=True).start()

    # ------------------------------------------------------------------
    # Tray and window close
    # ------------------------------------------------------------------

    def _show_window(self):
        self._user_closed = False
        self.show()
        self.activateWindow()

    def _hide_window(self):
        self._user_closed = True
        self._translate_id += 1
        self.hide()
        self.voice_worker.stop()

    def quit_app(self):
        self._save_settings()  # persist before exit
        # 1. Stop TTS
        self.voice_worker.stop()

        # 2. Signal worker threads to exit
        self.translation_worker.running = False
        self.clipboard_worker.running = False

        # 3. Wait for threads to finish (max 1s each to avoid hanging)
        self.translation_worker.wait(1000)
        self.clipboard_worker.wait(1000)

        # 4. Remove tray icon — without this the process may hang on Windows
        self.tray.hide()

        # 5. Quit the application
        QApplication.instance().quit()

    def closeEvent(self, event):
        # Close button — hide to tray instead of quitting
        event.ignore()
        self._hide_window()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import ctypes
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "CassandraCompositeMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:
        # Already running — notify the user instead of silently exiting
        import tkinter, tkinter.messagebox
        r = tkinter.Tk(); r.withdraw()
        tkinter.messagebox.showwarning(
            "CASSANDRA is already running",
            "The application is already running.\nFind the icon in the system tray (bottom-right corner)."
        )
        r.destroy()
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    genai.configure(api_key=get_api_key())

    window = CassandraApp()
    window.show()

    sys.exit(app.exec())
