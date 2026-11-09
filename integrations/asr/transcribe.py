"""CPU local Whisper command. Existing model files only; no runtime downloads."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True); parser.add_argument("--output", required=True)
    parser.add_argument("--model", required=True); parser.add_argument("--language", default="zh")
    arguments = parser.parse_args()
    model, source, output = Path(arguments.model).resolve(), Path(arguments.input).resolve(), Path(arguments.output).resolve()
    if not model.is_file() or not source.is_file(): raise SystemExit("Existing local model and audio files are required")
    os.environ["HF_HUB_OFFLINE"] = "1"; os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    import whisper
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    started = time.monotonic()
    engine = whisper.load_model(str(model), device="cpu")
    result = engine.transcribe(str(source), language=arguments.language, fp16=False,
                              temperature=0, condition_on_previous_text=False, verbose=False)
    from opencc import OpenCC
    converter = OpenCC("t2s")
    raw_text = result["text"].strip()
    segments = [{**segment, "raw_text": segment.get("text", ""), "text": converter.convert(segment.get("text", ""))} for segment in result.get("segments", [])]
    payload = {"text": converter.convert(raw_text), "raw_text": raw_text, "normalization": "OpenCC t2s", "segments": segments, "language": result.get("language", arguments.language),
               "engine": "openai-whisper", "model": model.name, "local": True, "elapsed_seconds": round(time.monotonic() - started, 3)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    print(json.dumps({key:value for key,value in payload.items() if key != "segments"}, ensure_ascii=False))

if __name__ == "__main__": main()
