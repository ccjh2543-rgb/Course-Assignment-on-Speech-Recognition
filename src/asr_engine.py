import os
import time
import numpy as np
import soundfile as sf


class ASREngine:
    def __init__(self, model_dir=None, device="auto", use_vad=True):
        self.model_dir = model_dir
        self.device = device
        self.use_vad = use_vad
        self.model = None
        self._load_model()

    def _load_model(self):
        from funasr import AutoModel

        model_id = self.model_dir or "iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
        print(f"[ASR] 加载模型: {model_id}")
        kwargs = {
            "model": model_id,
            "vad_model": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
            "punc_model": "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
            "spk_model": None,
            "disable_update": True,
        }
        if self.device != "auto":
            kwargs["device"] = self.device
        self.model = AutoModel(**kwargs)
        print("[ASR] 模型加载完成")

    def transcribe(self, audio_input):
        start_time = time.time()
        if isinstance(audio_input, str):
            result = self.model.generate(input=audio_input)
        else:
            result = self.model.generate(input=audio_input)
        elapsed = time.time() - start_time

        text = ""
        if isinstance(result, list) and len(result) > 0:
            texts = []
            for item in result:
                if isinstance(item, dict) and "text" in item:
                    texts.append(item["text"])
            text = " ".join(texts)

        return {
            "text": text,
            "raw_result": result,
            "elapsed": round(elapsed, 3),
        }

    def transcribe_file(self, audio_path):
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")
        return self.transcribe(audio_path)

    def transcribe_bytes(self, audio_bytes, sample_rate=16000):
        import soundfile as sf
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, audio_bytes, sample_rate)
            tmp_path = f.name
        try:
            result = self.transcribe(tmp_path)
        finally:
            os.unlink(tmp_path)
        return result

    def get_model_info(self):
        return {
            "model": self.model_dir or "speech_paraformer-large-vad-punc",
            "framework": "FunASR",
            "architecture": "Conformer (Paraformer variant)",
            "vad": self.use_vad,
        }


class WhisperEngine:
    def __init__(self, model_size="medium", device="auto"):
        self.model_size = model_size
        self.device = device
        self.model = None
        self._load_model()

    def _load_model(self):
        import whisper

        print(f"[Whisper] 加载模型: {self.model_size}")
        self.model = whisper.load_model(self.model_size)
        print("[Whisper] 模型加载完成")

    def transcribe_file(self, audio_path):
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")
        start_time = time.time()
        result = self.model.transcribe(audio_path, language="zh")
        elapsed = time.time() - start_time
        return {
            "text": result["text"].strip(),
            "segments": result.get("segments", []),
            "elapsed": round(elapsed, 3),
        }

    def get_model_info(self):
        return {
            "model": f"Whisper {self.model_size}",
            "framework": "OpenAI Whisper",
            "architecture": "Transformer (Encoder-Decoder)",
        }
