# CASSANDRA COMPOSITE

**CASSANDRA COMPOSITE** is a powerful, proactive translation and audio capture assistant for Windows. It combines real-time text monitoring, high-quality audio transcription, and advanced AI-driven text refinement into one sleek, dark-themed interface.

![Interface Preview](https://via.placeholder.com/500x700.png?text=Cassandra+Composite+UI) *(Add your own screenshot here)*

## 🚀 Key Features

### 1. Smart Text Capture & Translation
*   **Focus Monitoring**: Automatically detects and captures text highlighted in any application (via UI Automation).
*   **Clipboard Monitoring**: Instantly reacts to copied text.
*   **Multi-language Support**: Supports translation into Russian, English, Spanish, Japanese, Korean, French, and German.
*   **Auto-Translate Mode**: Toggle "AUTO" to have captured text translated instantly without manual interaction.

### 2. Advanced Audio Capture
*   **Microphone Recording**: High-quality audio recording with configurable limits (10 to 60 seconds).
*   **Gemini STT**: Uses **Google Gemini 2.0 Flash** for state-of-the-art speech-to-text accuracy.
*   **Transcribe & Translate**: Option to transcribe your speech and automatically translate it into the target language in one step.

### 3. Professional AI Refinement
*   **Business Style**: One-click refinement that uses Gemini to transform messy voice transcriptions into polished, professional business text. It removes filler words, fixes grammar, and improves flow while preserving your original meaning.

### 4. Natural Voice Playback (TTS)
*   **Edge TTS Integration**: Features natural-sounding voices and high-speed delivery using Microsoft Edge's cloud TTS engine.
*   **Auto-Read**: Automatically speaks the translation or transcription as soon as it's ready.

---

## 🛠 Tech Stack

*   **GUI**: [PySide6](https://pypi.org/project/PySide6/) (Qt for Python).
*   **AI/LLM**: [Google Generative AI](https://pypi.org/project/google-generativeai/) (Gemini).
*   **TTS**: [Edge-TTS](https://pypi.org/project/edge-tts/) with [Pygame](https://pypi.org/project/pygame/) for playback.
*   **Text Capture**: [UIAutomation](https://pypi.org/project/uiautomation/) and [Pyperclip](https://pypi.org/project/pyperclip/).
*   **Audio**: [SoundDevice](https://pypi.org/project/sounddevice/) and [NumPy](https://pypi.org/project/numpy/).

---

## 🔑 Setup & API Key

To use the AI features (transcription and business style refinement), you need a **Gemini API Key**.

1.  **Get your Key**: Visit [Google AI Studio](https://aistudio.google.com/app/apikey) and create a free API key.
2.  **Configuration**: 
    *   On the first run, the app will prompt you to enter the key.
    *   Alternatively, you can set an environment variable `GEMINI_API_KEY`.
    *   The key is securely saved in your system settings (Windows Registry via `QSettings`).

---

## 💻 Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/yourusername/Translator_1.git
    cd Translator_1
    ```

2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
    *Note: On Windows, make sure you have the necessary C++ build tools if `sounddevice` or `numpy` fail to install.*

3.  **Run the application**:
    ```bash
    python translator_recorder.pyw
    ```
    *(The `.pyw` extension ensures the app runs without an annoying console window).*

---

## 🖱 UI Controls

*   **AUTO ON/OFF**: Enable/disable automatic translation of captured text.
*   **VOICE ON/OFF**: Enable/disable text-to-speech feedback.
*   **Language Box**: Change the target language for both translation and voice output.
*   **MIC→**: Toggle whether microphone transcriptions should be translated.
*   **BUSINESS STYLE**: Send current text to Gemini for professional rewriting.
*   **COPY**: Copy the current content to the clipboard.
*   **✕ (Clear)**: Reset the text field and internal buffers.

---

## 📝 License
This project is for personal use and development. Built with ❤️ by Cassandra & Oleg.
