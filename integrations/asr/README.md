# 本地中文语音识别

安装官方 OpenAI Whisper v20250625、PyTorch CPU 与多语言 base 权重至本工程独立环境：

```powershell
.\.venv\Scripts\python.exe scripts/setup-asr.py --model base
.\.venv\Scripts\python.exe integrations/asr/verify.py
```

安装信息及可直接用于工作台的 `asr_python`、`asr_model`、`asr_command` 位于 `installed.json` 的 `settings` 对象。运行入口为 `transcribe.py`，参数 `--input`、`--output`、`--model` 均为本地文件路径。运行时不下载模型、不上传音频，最多使用 4 个 CPU 线程。

OpenCC 在本机规范化繁体为简体，输出同时保留 `raw_text` 与原始 segment 文本。真实中文 WAV 验收证据位于 `docs/evidence/asr-host.json`；样例中关键句“建筑工作台”通过，专名“源智”存在误识别。
