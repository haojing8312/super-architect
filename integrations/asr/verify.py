"""Actually transcribe the Windows-native Chinese WAV with installed Whisper."""
from __future__ import annotations
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT = Path(__file__).resolve().parents[2]

def main():
    installed = json.loads((PROJECT / "integrations" / "asr" / "installed.json").read_text(encoding="utf-8"))
    source = PROJECT / ".local" / "native-fixtures" / "中文原生朗读.wav"
    output = PROJECT / ".local" / "asr" / "transcript.json"; output.parent.mkdir(parents=True, exist_ok=True)
    settings = installed["settings"]
    command = [part.replace("{input}", str(source)).replace("{output}", str(output)) for part in settings["asr_command"]]
    environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, env=environment, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode: raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    actual = json.loads(output.read_text(encoding="utf-8"))
    if "建筑工作台" not in actual["text"]: raise RuntimeError(f"Expected Chinese phrase not transcribed: {actual['text']}")
    package_check = subprocess.run([settings["asr_python"], "-c", "import whisper,torch,json; print(json.dumps({'whisper':whisper.__version__,'torch':torch.__version__,'cuda':torch.cuda.is_available()}))"], check=True, capture_output=True)
    evidence = {"verified_at": datetime.now(timezone.utc).isoformat(), "official_source": "https://github.com/openai/whisper/tree/v20250625",
        "license": "MIT", "versions": json.loads(package_check.stdout), "model": installed["model"], "model_sha256": installed["model_sha256"],
        "audio": str(source), "audio_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "expected_phrase": "建筑工作台",
        "transcript": actual["text"], "raw_transcript": actual.get("raw_text", actual["text"]), "normalization": actual.get("normalization", "none"),
        "segments": actual["segments"], "elapsed_seconds": round(time.monotonic()-started,3),
        "settings": settings, "real_audio_recognition_verified": True, "audio_uploaded": False, "output": str(output)}
    path = PROJECT / "docs" / "evidence" / "asr-host.json"; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key:value for key,value in evidence.items() if key != "segments"}, ensure_ascii=False, indent=2))

if __name__ == "__main__": main()
