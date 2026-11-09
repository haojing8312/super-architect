"""Offline ASR orchestration and Windows native speech synthesis.

No engine downloads weights and no application code sends audio to a service.
Whisper requires an existing local .pt model; custom ASR commands are explicit
local executable/argument arrays, never shell command strings.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .documents import safe_name

MAX_AUDIO_BYTES = 40 * 1024 * 1024
AUDIO_EXTENSIONS = {".wav", ".mp3", ".webm", ".m4a", ".ogg", ".flac", ".mp4", ".aac"}


def _run(command, *, timeout=15, env=None, cwd=None):
    return subprocess.run(command, env=env, cwd=cwd, timeout=timeout, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def executable(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    return shutil.which(value)


def custom_command(settings: dict) -> list[str] | None:
    configured = settings.get("asr_command", [])
    if not configured:
        return None
    if not isinstance(configured, list) or not configured or not all(isinstance(part, str) and part for part in configured):
        raise ValueError("asr_command 必须为本地可执行文件与参数的 JSON 数组")
    if not executable(configured[0]):
        raise ValueError("配置的本地 ASR 可执行文件不存在")
    if not any("{input}" in part for part in configured):
        raise ValueError("asr_command 参数必须包含 {input} 音频路径占位符")
    return [executable(configured[0]), *configured[1:]]


def asr_status(settings: dict) -> dict:
    try:
        custom = custom_command(settings)
    except ValueError as exception:
        return {"engine": "command", "installed": False, "ready": False, "error": str(exception)}
    if custom:
        return {"engine": "command", "installed": True, "ready": True, "command": custom,
                "model": "由本地命令管理", "error": "", "verified": False}
    configured_python = settings.get("asr_python", "")
    python = executable(configured_python) if isinstance(configured_python, str) and configured_python else (None if getattr(sys, "frozen", False) else sys.executable)
    installed = False
    if python:
        if Path(python).resolve() == Path(sys.executable).resolve():
            installed = importlib.util.find_spec("whisper") is not None
        else:
            try:
                probe = _run([python, "-c", "import importlib.util; print('yes' if importlib.util.find_spec('whisper') else 'no')"], timeout=10)
                installed = probe.returncode == 0 and probe.stdout.strip() == b"yes"
            except (OSError, subprocess.TimeoutExpired):
                pass
    whisper_cli = shutil.which("whisper") if not installed and not configured_python else None
    if whisper_cli:
        installed = True
    model_setting = settings.get("asr_model", "")
    model = Path(model_setting).expanduser() if isinstance(model_setting, str) and model_setting else None
    has_model = bool(model and model.is_file())
    ffmpeg = shutil.which("ffmpeg")
    if not installed:
        error = "未检测到本机 Whisper；可配置 asr_command，或 asr_python 指向已安装 Whisper 的 Python"
    elif not has_model:
        error = "请设置 asr_model 为已有的本地 Whisper .pt 模型文件；工作台不会自动下载权重"
    elif not ffmpeg:
        error = "Whisper 需要本机 ffmpeg 读取音频；当前 PATH 中未找到 ffmpeg"
    else:
        error = ""
    return {"engine": "whisper-cli" if whisper_cli else "whisper", "installed": installed, "ready": installed and has_model and bool(ffmpeg),
            "python": python or "", "model": str(model.resolve()) if has_model else "", "ffmpeg": ffmpeg or "",
            "command": [whisper_cli] if whisper_cli else [],
            "error": error, "verified": False}


VOICE_SCRIPT = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $voices = @($synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object {
    @{name=$_.VoiceInfo.Name; language=$_.VoiceInfo.Culture.Name; gender=$_.VoiceInfo.Gender.ToString()}
  })
  ConvertTo-Json -InputObject $voices -Compress -Depth 4
} finally { $synth.Dispose() }
"""


def powershell_command(script: str):
    powershell = shutil.which("powershell.exe") if os.name == "nt" else None
    if not powershell:
        raise ValueError("Windows PowerShell / System.Speech 不可用")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def tts_status() -> dict:
    try:
        result = _run(powershell_command(VOICE_SCRIPT), timeout=15)
        if result.returncode:
            raise ValueError("无法加载 Windows System.Speech 原生语音")
        voices = json.loads(result.stdout.decode("utf-8-sig", errors="replace"))
        if isinstance(voices, dict):
            voices = [voices]
        voices = voices or []
        chinese = [voice for voice in voices if voice.get("language", "").lower().startswith("zh")]
        return {"engine": "windows-system-speech", "installed": bool(voices), "ready": bool(chinese),
                "voices": voices, "chinese_voices": chinese,
                "error": "" if chinese else "Windows 未安装可用的中文原生语音；客户端浏览器朗读可单独检测"}
    except (ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {"engine": "windows-system-speech", "installed": False, "ready": False, "voices": [], "chinese_voices": [],
                "error": "本机 Windows 中文语音暂不可用；客户端使用 speechSynthesis 时由浏览器独立提供声音"}


def transcribe(path: Path, output_dir: Path, settings: dict) -> dict:
    status = asr_status(settings)
    if not status["ready"]:
        raise ValueError(status["error"])
    output = output_dir / "transcript.json"
    custom = custom_command(settings)
    if custom:
        command = [part.replace("{input}", str(path)).replace("{output}", str(output)).replace("{output_dir}", str(output_dir)) for part in custom]
    else:
        command = (status["command"] or [status["python"], "-m", "whisper"]) + [str(path), "--model", status["model"], "--device", "cpu",
                   "--language", "zh", "--task", "transcribe", "--fp16", "False", "--output_format", "json",
                   "--output_dir", str(output_dir)]
        output = output_dir / (path.stem + ".json")
    environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
    try:
        process = _run(command, timeout=180, env=environment, cwd=str(output_dir))
    except subprocess.TimeoutExpired as exception:
        raise ValueError("本机语音识别超过 180 秒，进程已停止；请缩短录音") from exception
    if process.returncode:
        raise ValueError("本机语音识别失败，请检查引擎、模型、ffmpeg 及音频格式")
    if output.is_file():
        if output.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("语音识别输出超过本地读取上限")
        payload = json.loads(output.read_text(encoding="utf-8-sig"))
    else:
        try:
            payload = json.loads(process.stdout.decode("utf-8-sig"))
        except (json.JSONDecodeError, UnicodeError):
            payload = {"text": process.stdout.decode("utf-8", errors="replace").strip()}
    if isinstance(payload, str):
        payload = {"text": payload}
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str) or not payload["text"].strip():
        raise ValueError("本机识别引擎没有返回非空文本")
    return {"text": payload["text"].strip(), "segments": payload.get("segments", []), "language": payload.get("language", "zh"),
            "engine": status["engine"], "local": True}


SYNTHESIS_SCRIPT = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$request = [System.IO.File]::ReadAllText($env:ARCHITECTURE_SPEECH_REQUEST, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
  $synth.SelectVoice($request.voice)
  $synth.Rate = [int]$request.rate
  $synth.SetOutputToWaveFile($env:ARCHITECTURE_SPEECH_WAV)
  $synth.Speak([string]$request.text)
} finally { $synth.Dispose() }
"""


class SynthesisInput(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    voice: str = ""
    rate: int = Field(default=0, ge=-10, le=10)


def register_speech(app):
    router = APIRouter(prefix="/api/speech")
    store = app.state.store
    jobs = store.data_dir / "speech-jobs"
    jobs.mkdir(parents=True, exist_ok=True)

    @router.get("/status")
    def status():
        return {"asr": asr_status(store.get_settings()), "tts": tts_status(), "local": True}

    @router.post("/transcribe")
    def audio_to_text(audio: UploadFile = File(...)):
        status = asr_status(store.get_settings())
        if not status["ready"]:
            audio.file.close()
            raise HTTPException(503, status["error"])
        try:
            name = safe_name(audio.filename or "recording.webm")
            if Path(name).suffix.lower() not in AUDIO_EXTENSIONS:
                raise ValueError("音频支持 WAV、MP3、WebM、M4A、OGG、FLAC、AAC")
            with tempfile.TemporaryDirectory(prefix="asr-", dir=jobs) as directory:
                folder = Path(directory)
                source = folder / name
                size = 0
                with source.open("wb") as stream:
                    while chunk := audio.file.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_AUDIO_BYTES:
                            raise ValueError("录音超过 40 MB 上限")
                        stream.write(chunk)
                if not size:
                    raise ValueError("录音文件为空")
                return transcribe(source, folder, store.get_settings())
        except (ValueError, OSError) as exception:
            raise HTTPException(422, str(exception)) from exception
        finally:
            audio.file.close()

    @router.post("/synthesize", status_code=201)
    def text_to_audio(body: SynthesisInput):
        status = tts_status()
        if not status["ready"]:
            raise HTTPException(503, status["error"])
        names = [voice["name"] for voice in status["chinese_voices"]]
        if body.voice and body.voice not in names:
            raise HTTPException(422, "请选择本机已安装的中文语音")
        with tempfile.TemporaryDirectory(prefix="tts-", dir=jobs) as directory:
            request = Path(directory) / "request.json"
            output = Path(directory) / "speech.wav"
            request.write_text(json.dumps({"text": body.text, "voice": body.voice or names[0], "rate": body.rate}, ensure_ascii=False), encoding="utf-8")
            environment = {**os.environ, "ARCHITECTURE_SPEECH_REQUEST": str(request), "ARCHITECTURE_SPEECH_WAV": str(output)}
            try:
                result = _run(powershell_command(SYNTHESIS_SCRIPT), timeout=60, env=environment)
                if result.returncode or not output.is_file() or output.stat().st_size < 44:
                    raise ValueError("Windows 本机语音合成失败，未生成有效 WAV")
                with output.open("rb") as stream:
                    attachment = store.save_attachment("中文朗读.wav", stream)
                return {**attachment, "voice": body.voice or names[0], "local": True}
            except (ValueError, OSError, subprocess.TimeoutExpired) as exception:
                raise HTTPException(422, str(exception)) from exception

    app.include_router(router)
