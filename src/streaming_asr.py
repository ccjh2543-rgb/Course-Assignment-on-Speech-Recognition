"""
流式语音识别缓冲区模块
======================
实现实时语音识别的流式处理：
  1. 维护音频数据队列
  2. 与 VAD 联动检测语音起止
  3. 静音超过阈值时自动提交识别
  4. 增量式输出识别结果

用法:
  from src.streaming_asr import StreamingASR
  asr = StreamingASR()
  asr.feed(audio_chunk)  # 每次送入 0.5-1 秒音频
  print(asr.get_partial())  # 获取当前识别的部分结果
  print(asr.get_final())    # 获取完整结果
"""

import os
import sys
import time
import tempfile
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.asr_engine import ASREngine


class StreamingASR:
    """流式语音识别器

    工作流程:
    1. feed(chunk) 接收音频块
    2. 内部 VAD 检测语音段
    3. 检测到语音端点时自动提交识别
    4. 结果追加到历史记录

    Args:
        chunk_duration_ms: 每个音频块时长（毫秒）
        silence_threshold_ms: 静音超时阈值（毫秒），超过此时长自动断句
        vad_speech_prob_threshold: VAD 语音概率阈值
        sample_rate: 音频采样率
    """

    def __init__(
        self,
        chunk_duration_ms=500,
        silence_threshold_ms=800,
        vad_speech_prob_threshold=0.5,
        sample_rate=16000,
        use_vad_model=False,
    ):
        self.sample_rate = sample_rate
        self.chunk_size = int(sample_rate * chunk_duration_ms / 1000)
        self.silence_threshold_ms = silence_threshold_ms
        self.silence_chunk_count = int(silence_threshold_ms / chunk_duration_ms)
        self.vad_prob_threshold = vad_speech_prob_threshold
        self.use_vad_model = use_vad_model

        # 内部状态
        self.buffer = np.array([], dtype=np.float32)  # 当前语音段缓冲区
        self.silent_chunks = 0  # 连续静音块计数
        self.is_speaking = False  # 是否正在说话
        self.history = []  # 历史识别结果 [(text, timestamp), ...]
        self.partial_result = ""  # 当前部分结果
        self._engine = None
        self._vad_processor = None
        self._init_vad()

        print(f"[StreamingASR] 初始化: chunk={chunk_duration_ms}ms, "
              f"silence={silence_threshold_ms}ms, sr={sample_rate}")

    def _init_vad(self):
        """初始化 VAD 检测器"""
        if self.use_vad_model:
            try:
                from funasr import AutoModel
                self._vad_model = AutoModel(
                    model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
                    disable_update=True,
                )
                self._has_vad = True
                print("[StreamingASR] 使用 FSMN-VAD 模型")
            except Exception as e:
                print(f"[StreamingASR] VAD 加载失败，使用能量检测: {e}")
                self._has_vad = False
        else:
            self._has_vad = False
            print("[StreamingASR] 使用能量检测 (推荐，速度更快)")

    def _get_engine(self):
        """懒加载 ASR 引擎"""
        if self._engine is None:
            self._engine = ASREngine(device="auto")
        return self._engine

    def _detect_speech_energy(self, chunk):
        """基于能量的简单语音检测（VAD 不可用时的后备方案）"""
        energy = np.sqrt(np.mean(chunk ** 2))
        return energy > 0.02  # 经验阈值

    def _detect_speech_vad(self, chunk):
        """使用 FSMN-VAD 模型进行语音检测"""
        try:
            result = self._vad_model.generate(input=chunk)
            if isinstance(result, list) and len(result) > 0:
                if isinstance(result[0], dict):
                    return result[0].get("value", 0) > self.vad_prob_threshold
            return False
        except:
            return self._detect_speech_energy(chunk)

    def _is_speech(self, chunk):
        """判断音频块是否包含语音"""
        if self._has_vad:
            return self._detect_speech_vad(chunk)
        return self._detect_speech_energy(chunk)

    def feed(self, audio_chunk):
        """送入一个音频块

        Args:
            audio_chunk: 音频数据 (numpy array, float32, 范围[-1, 1])

        Returns:
            dict: 识别结果状态
                {"text": str, "is_partial": bool, "is_final": bool}
        """
        chunk = np.asarray(audio_chunk, dtype=np.float32)

        has_speech = self._is_speech(chunk)

        if has_speech:
            self.silent_chunks = 0
            if not self.is_speaking:
                # 语音开始
                self.is_speaking = True
                self.buffer = chunk.copy()
            else:
                # 持续语音，追加到缓冲区
                self.buffer = np.concatenate([self.buffer, chunk])
            return {"text": "", "is_partial": True, "is_final": False}

        else:
            if self.is_speaking:
                # 语音中但当前块是静音
                self.silent_chunks += 1
                self.buffer = np.concatenate([self.buffer, chunk])

                if self.silent_chunks >= self.silence_chunk_count:
                    # 静音超时，提交识别
                    return self._flush()
                else:
                    return {"text": "", "is_partial": True, "is_final": False}
            else:
                # 全程静音
                return {"text": "", "is_partial": False, "is_final": False}

    def _flush(self):
        """提交缓冲区进行识别"""
        if len(self.buffer) < self.sample_rate * 0.3:  # 少于0.3秒，丢弃
            self._reset_state()
            return {"text": "", "is_partial": False, "is_final": False}

        # 保存缓冲区副本
        audio_to_recognize = self.buffer.copy()
        self._reset_state()

        # 执行识别
        try:
            eng = self._get_engine()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                import soundfile as sf
                sf.write(f.name, audio_to_recognize, self.sample_rate)
                tmp_path = f.name

            try:
                result = eng.transcribe_file(tmp_path)
                text = result["text"]
                timestamp = time.strftime("%H:%M:%S")

                if text.strip():
                    self.history.append((text, timestamp))
                    self.partial_result = text

                    return {
                        "text": text,
                        "is_partial": False,
                        "is_final": True,
                        "timestamp": timestamp,
                        "duration_sec": round(len(audio_to_recognize) / self.sample_rate, 2),
                    }
            finally:
                os.unlink(tmp_path)
        except Exception as e:
            print(f"[StreamingASR] 识别错误: {e}")

        return {"text": "", "is_partial": False, "is_final": False}

    def _reset_state(self):
        """重置语音状态"""
        self.buffer = np.array([], dtype=np.float32)
        self.silent_chunks = 0
        self.is_speaking = False

    def get_history(self):
        """获取完整识别历史"""
        return self.history

    def get_full_text(self):
        """获取全部识别文本（用空格连接）"""
        return " ".join(t for t, _ in self.history)

    def reset(self):
        """完全重置状态"""
        self._reset_state()
        self.history = []
        self.partial_result = ""

    def get_stats(self):
        """获取统计信息"""
        return {
            "utterances": len(self.history),
            "history": self.history,
        }
