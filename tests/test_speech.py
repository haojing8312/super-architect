from __future__ import annotations

import io
import json
import os
import sys
import wave
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from architecture_workbench.speech import asr_status, register_speech, tts_status
from architecture_workbench.store import Store


def speech_app(tmp_path):
    app = FastAPI()
    app.state.store = Store(tmp_path / "data")
    register_speech(app)
    return app


def recording() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 1600)
    return output.getvalue()


def test_missing_local_asr_returns_diagnostic_and_never_claims_text(tmp_path):
    app = speech_app(tmp_path)
    app.state.store.save_settings({"asr_python": str(tmp_path / "not-installed.exe")})
    with TestClient(app) as client:
        status = client.get("/api/speech/status").json()
        assert status["local"]
        assert not status["asr"]["ready"]
        response = client.post("/api/speech/transcribe", files={"audio": ("test.wav", recording(), "audio/wav")})
        assert response.status_code == 503
        assert "Whisper" in response.json()["detail"]
        assert "text" not in response.json()


def test_custom_asr_command_is_validated_before_running(tmp_path):
    invalid = asr_status({"asr_command": "python -m whisper"})
    assert not invalid["ready"] and "JSON 数组" in invalid["error"]
    missing = asr_status({"asr_command": [str(tmp_path / "missing.exe"), "{input}"]})
    assert not missing["ready"]
    no_audio = asr_status({"asr_command": [sys.executable, "--version"]})
    assert not no_audio["ready"] and "{input}" in no_audio["error"]


def test_custom_engine_process_contract_audio_path_and_offline_environment(tmp_path):
    """Exercise a real local subprocess, not an ASR model accuracy claim."""
    engine = tmp_path / "local_engine.py"
    engine.write_text("""
import json, os, sys, wave
assert os.environ['HF_HUB_OFFLINE'] == '1'
assert os.environ['TRANSFORMERS_OFFLINE'] == '1'
with wave.open(sys.argv[1], 'rb') as audio:
    assert audio.getframerate() == 16000
with open(sys.argv[2], 'w', encoding='utf-8') as output:
    json.dump({'text': '本地进程协议验证', 'language':'zh', 'segments':[]}, output, ensure_ascii=False)
""", encoding="utf-8")
    app = speech_app(tmp_path)
    app.state.store.save_settings({"asr_command": [sys.executable, str(engine), "{input}", "{output}"]})
    with TestClient(app) as client:
        response = client.post("/api/speech/transcribe", files={"audio": ("../录音.wav", recording(), "audio/wav")})
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "本地进程协议验证"
        assert response.json()["engine"] == "command" and response.json()["local"]
        assert list((app.state.store.data_dir / "speech-jobs").iterdir()) == []


def test_failed_local_engine_does_not_return_transcription(tmp_path):
    app = speech_app(tmp_path)
    app.state.store.save_settings({"asr_command": [sys.executable, "-c", "import sys; sys.exit(3)", "{input}"]})
    with TestClient(app) as client:
        response = client.post("/api/speech/transcribe", files={"audio": ("test.wav", recording(), "audio/wav")})
        assert response.status_code == 422 and "失败" in response.json()["detail"]
        assert list((app.state.store.data_dir / "speech-jobs").iterdir()) == []


def test_real_windows_chinese_voice_produces_playable_pcm_wav(tmp_path):
    status = tts_status()
    if os.name != "nt" or not status["ready"]:
        pytest.skip("A Windows native Chinese voice is not installed")
    app = speech_app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/speech/synthesize", json={"text": "源智建筑工作台，中文语音测试。"})
        assert response.status_code == 201, response.text
        result = response.json()
        assert result["local"]
        assert result["voice"] in [voice["name"] for voice in status["chinese_voices"]]
        with wave.open(result["path"], "rb") as wav:
            assert wav.getnframes() > 0
            assert wav.getnchannels() >= 1
            assert wav.getframerate() >= 16000
            assert wav.getsampwidth() >= 2
        invalid = client.post("/api/speech/synthesize", json={"text": "测试", "voice": "uninstalled-voice"})
        assert invalid.status_code == 422
