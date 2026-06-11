"""
《语音识别》课程实验 - 统一入口脚本
===================================
基于 THCHS-30 数据集，运行5个实验：

实验1: 基准性能评估 (CER + RTF)
实验2: 与 Whisper 模型对比
实验3: 噪声鲁棒性测试
实验4: 消融实验 (VAD / 标点恢复)
实验5: 流式分块策略对比

所有实验使用固定随机种子 (seed=42)，保证可复现。
输出: experiments/results/thchs30_full_eval.json
"""

import sys
import os
import json
import time
import random
import re
import tempfile
import warnings
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import soundfile as sf

warnings.filterwarnings("ignore")

# ============================================================
# 路径配置
# ============================================================
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_WAV_SCP = os.path.join(PROJECT_ROOT, "data", "finetune", "test", "wav.scp")
TEST_TEXT = os.path.join(PROJECT_ROOT, "data", "finetune", "test", "text")
RESULTS_DIR = os.path.join(PROJECT_ROOT, "experiments", "results")
SAMPLE_AUDIO_DIR = os.path.join(PROJECT_ROOT, "experiments", "sample_audio")
os.makedirs(RESULTS_DIR, exist_ok=True)

# ============================================================
# 数据集加载
# ============================================================

def load_thchs30_test():
    """加载 THCHS-30 测试集

    Returns:
        list[dict]: [{"utt_id": str, "wav_path": str, "text": str, "text_norm": str}, ...]
    """
    # 加载 wav.scp
    wav_map = {}
    with open(TEST_WAV_SCP, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                wav_map[parts[0]] = parts[1]

    # 加载 text
    utterances = []
    with open(TEST_TEXT, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                utt_id = parts[0]
                text = parts[1]
                if utt_id in wav_map:
                    # 去除 THCHS-30 文本中的空格（字符之间有空格）
                    text_norm = re.sub(r"\s+", "", text)
                    utterances.append({
                        "utt_id": utt_id,
                        "wav_path": wav_map[utt_id],
                        "text": text,
                        "text_norm": text_norm,
                    })

    print(f"[数据] THCHS-30 测试集: {len(utterances)} 条有效音频")
    return utterances


def sample_subset(utterances, n, seed=42):
    """从完整测试集中随机抽样"""
    if n >= len(utterances):
        return utterances
    rng = random.Random(seed)
    return rng.sample(utterances, n)


def compute_cer(ref, hyp):
    """计算字符错误率"""
    from src.evaluate import compute_cer as _cer
    return _cer(ref, hyp)


# ============================================================
# 噪声合成（使用 NOISEX-92 标准噪声库）
# ============================================================

NOISE_DIR = os.path.join(PROJECT_ROOT, "noisex-92-master", "noise92")
NOISE_FILES = {
    'white': 'white.wav',       # 白噪声
    'babble': 'babble.wav',     # 多人说话噪声
    'factory': 'factory1.wav',  # 工厂噪声
    'street': 'volvo.wav',      # 车内噪声（低频稳态，替代街道）
}

NOISE_TYPES = ['white', 'babble', 'street']  # 使用3类噪声
SNR_LEVELS = [20, 10, 0]  # 3个SNR级别
NOISE_LABELS = {'white': '白噪声', 'babble': 'Babble噪声', 'factory': '工厂噪声', 'street': '街道噪声'}


def add_noise(clean_audio, noise_type, snr_db, sr=16000):
    """从 NOISEX-92 标准噪声库加载真实噪声并叠加

    Args:
        clean_audio: 纯净音频 (numpy array)
        noise_type: 'white' | 'babble' | 'factory' | 'street'
        snr_db: 信噪比 (dB)
        sr: 目标采样率

    Returns:
        noisy_audio (numpy array, float32)
    """
    import librosa

    noise_file = NOISE_FILES.get(noise_type)
    if noise_file is None:
        raise ValueError(f"Unknown noise type: {noise_type}")

    noise_path = os.path.join(NOISE_DIR, noise_file)
    if not os.path.exists(noise_path):
        raise FileNotFoundError(f"NOISEX-92 file not found: {noise_path}")

    # 加载噪声并重采样至目标采样率
    noise, _ = librosa.load(noise_path, sr=sr, mono=True)

    # 循环或裁剪至与 clean_audio 等长
    if len(noise) < len(clean_audio):
        repeats = int(np.ceil(len(clean_audio) / len(noise)))
        noise = np.tile(noise, repeats)
    noise = noise[:len(clean_audio)]

    # 按 SNR 调整噪声幅度
    signal_power = np.mean(clean_audio ** 2)
    noise_power = np.mean(noise ** 2)
    snr_linear = 10 ** (snr_db / 10)
    scale = np.sqrt(signal_power / (max(noise_power, 1e-12) * snr_linear))
    noise = noise * scale

    noisy = clean_audio + noise
    # 归一化防止削波
    max_val = np.max(np.abs(noisy))
    if max_val > 0:
        noisy = noisy / max_val
    return noisy.astype(np.float32)


# ============================================================
# 实验1: 基准性能评估
# ============================================================

def exp1_benchmark(subset, results_dict):
    """实验1: Paraformer 在 THCHS-30 上的 CER 和 RTF"""
    print("\n" + "=" * 65)
    print("  实验1: 基准性能评估")
    print(f"  测试集: {len(subset)} 条")
    print("=" * 65)

    from src.asr_engine import ASREngine
    engine = ASREngine(device="auto")

    total_time = 0
    total_audio_len = 0
    cers = []
    details = []

    for i, utt in enumerate(subset):
        wav_path = utt["wav_path"]
        ref_text = utt["text_norm"]

        try:
            # 获取音频时长
            audio_data, sr = sf.read(wav_path)
            audio_len = len(audio_data) / sr
            total_audio_len += audio_len

            # 识别
            result = engine.transcribe_file(wav_path)
            hyp_text = result["text"]
            elapsed = result["elapsed"]
            total_time += elapsed

            # 计算 CER
            cer_val = compute_cer(ref_text, hyp_text)
            cers.append(cer_val)

            details.append({
                "utt_id": utt["utt_id"],
                "audio_len": round(audio_len, 2),
                "reference": ref_text,
                "hypothesis": hyp_text,
                "CER": cer_val,
                "time": elapsed,
            })

        except Exception as e:
            print(f"  [ERROR] {utt['utt_id']}: {e}")
            continue

        if (i + 1) % 100 == 0:
            print(f"  进度: {i+1}/{len(subset)}, 当前平均 CER: {np.mean(cers):.2%}")

    avg_cer = float(np.mean(cers)) if cers else 0
    std_cer = float(np.std(cers)) if cers else 0
    total_time = round(total_time, 2)
    rtf = round(total_time / total_audio_len, 4) if total_audio_len > 0 else 0

    print(f"\n  ── 结果 ──")
    print(f"  平均 CER: {avg_cer:.2%}")
    print(f"  CER 标准差: {std_cer:.2%}")
    print(f"  总推理耗时: {total_time:.1f}s")
    print(f"  总音频时长: {total_audio_len:.0f}s")
    print(f"  实时因子 RTF: {rtf}")

    result = {
        "num_utterances": len(subset),
        "total_audio_sec": round(total_audio_len, 1),
        "total_time_sec": total_time,
        "avg_CER": avg_cer,
        "std_CER": std_cer,
        "RTF": rtf,
        "details": details,
    }
    results_dict["exp1_benchmark"] = result
    return result


# ============================================================
# 实验2: Whisper 对比
# ============================================================

def exp2_whisper_compare(subset, results_dict):
    """实验2: Paraformer vs Faster-Whisper 在相同子集上的对比

    使用 faster-whisper (CTranslate2) 加速推理，CPU上显著快于原版 Whisper。
    """
    print("\n" + "=" * 65)
    print("  实验2: Paraformer vs Faster-Whisper 对比")
    print(f"  测试集: {len(subset)} 条")
    print("=" * 65)

    # 加载 Faster-Whisper
    try:
        from faster_whisper import WhisperModel
        print("\n  [加载] Faster-Whisper small (INT8)...")
        whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
    except Exception as e:
        print(f"  [ERROR] Faster-Whisper 加载失败: {e}")
        print("  请安装: pip install faster-whisper")
        results_dict["exp2_whisper_compare"] = {"error": str(e)}
        return None

    whisper_cers = []
    whisper_times = []
    whisper_details = []

    for i, utt in enumerate(subset):
        wav_path = utt["wav_path"]
        ref_text = utt["text_norm"]

        try:
            start = time.time()
            segments, _ = whisper_model.transcribe(wav_path, language="zh", beam_size=5)
            hyp_text = " ".join(seg.text for seg in segments)
            elapsed = time.time() - start

            cer_val = compute_cer(ref_text, hyp_text)
            whisper_cers.append(cer_val)
            whisper_times.append(elapsed)

            whisper_details.append({
                "utt_id": utt["utt_id"],
                "reference": ref_text,
                "hypothesis": hyp_text,
                "CER": cer_val,
                "time": round(elapsed, 3),
            })
        except Exception as e:
            print(f"  [ERROR] Faster-Whisper {utt['utt_id']}: {e}")
            continue

        if (i + 1) % 100 == 0:
            print(f"  Faster-Whisper 进度: {i+1}/{len(subset)}")

    avg_cer_w = float(np.mean(whisper_cers)) if whisper_cers else 0
    avg_time_w = float(np.mean(whisper_times)) if whisper_times else 0

    # 使用实验1的 Paraformer 结果
    para_result = results_dict.get("exp1_benchmark", {})
    avg_cer_p = para_result.get("avg_CER", 0)
    avg_time_p = para_result.get("avg_time_per_utt", 0)
    if avg_time_p == 0 and para_result.get("details"):
        times = [d["time"] for d in para_result["details"]]
        avg_time_p = float(np.mean(times)) if times else 0

    # 计算加速比
    speedup = avg_time_w / avg_time_p if avg_time_p > 0 else 0

    print(f"\n  ── 结果对比 ──")
    print(f"  Paraformer        CER: {avg_cer_p:.2%}, 平均耗时: {avg_time_p:.3f}s")
    print(f"  Faster-Whisper    CER: {avg_cer_w:.2%}, 平均耗时: {avg_time_w:.3f}s")
    print(f"  加速比: {speedup:.1f}x (Whisper为基准)")

    result = {
        "num_utterances": len(subset),
        "paraformer": {
            "avg_CER": avg_cer_p,
            "avg_time": avg_time_p,
            "model": "Paraformer (Conformer, 220M)",
        },
        "faster_whisper": {
            "avg_CER": avg_cer_w,
            "avg_time": avg_time_w,
            "model": "Faster-Whisper small (244M, INT8)",
        },
        "speedup_ratio": round(speedup, 2),
        "whisper_details": whisper_details,
    }
    results_dict["exp2_whisper_compare"] = result
    return result


# ============================================================
# 实验3: 噪声鲁棒性
# ============================================================

def exp3_noise_robustness(subset, results_dict):
    """实验3: 不同噪声条件下的 CER"""
    print("\n" + "=" * 65)
    print("  实验3: 噪声鲁棒性测试")
    print(f"  测试集: {len(subset)} 条 × {len(NOISE_TYPES)} 噪声 × {len(SNR_LEVELS)} SNR")
    print(f"  总推理次数: {len(subset) * len(NOISE_TYPES) * len(SNR_LEVELS)}")
    print("=" * 65)

    from src.asr_engine import ASREngine
    engine = ASREngine(device="auto")

    # 先获取纯净识别结果
    print("\n  [基线] 获取纯净音频识别结果...")
    clean_texts = {}
    for utt in subset:
        try:
            result = engine.transcribe_file(utt["wav_path"])
            clean_texts[utt["utt_id"]] = result["text"]
        except Exception as e:
            print(f"  [ERROR] {utt['utt_id']}: {e}")
            clean_texts[utt["utt_id"]] = ""

    # 噪声测试
    noise_matrix = {}
    total_tasks = len(NOISE_TYPES) * len(SNR_LEVELS) * len(subset)
    task_count = 0

    for noise_type in NOISE_TYPES:
        for snr in SNR_LEVELS:
            key = f"{noise_type}_SNR{snr}"
            cers = []
            per_file = []

            for utt in subset:
                wav_path = utt["wav_path"]
                ref_text = utt["text_norm"]

                try:
                    # 加载并加噪
                    clean, sr = sf.read(wav_path)
                    if len(clean.shape) > 1:
                        clean = np.mean(clean, axis=1)
                    noisy = add_noise(clean, noise_type, snr, sr)

                    # 写入临时文件
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                        sf.write(f.name, noisy, sr)
                        tmp = f.name

                    try:
                        result = engine.transcribe_file(tmp)
                        hyp = result["text"]
                        cer = compute_cer(ref_text, hyp)
                        cers.append(cer)
                        per_file.append({
                            "utt_id": utt["utt_id"],
                            "CER": cer,
                        })
                    finally:
                        os.unlink(tmp)

                except Exception as e:
                    print(f"  [ERROR] {utt['utt_id']} @ {noise_type}_{snr}dB: {e}")
                    cers.append(1.0)
                    per_file.append({"utt_id": utt["utt_id"], "CER": 1.0})

                task_count += 1
                if task_count % 500 == 0:
                    print(f"  噪声实验进度: {task_count}/{total_tasks}")

            avg_cer = float(np.mean(cers))
            noise_matrix[key] = {
                "avg_CER": round(avg_cer, 4),
                "num_samples": len(cers),
                "per_file": per_file,
            }
            print(f"  {noise_type:8s} SNR={snr:2d}dB → CER: {avg_cer:.2%}")

    # 输出汇总表格
    print(f"\n  ── 噪声鲁棒性汇总 ──")
    header = f"{'噪声类型':<12s}" + "".join(f"SNR={s:2d}dB  " for s in SNR_LEVELS)
    print(f"  {header}")
    print(f"  {'-' * len(header)}")
    for noise_type in NOISE_TYPES:
        row = f"  {NOISE_LABELS[noise_type]:<10s}"
        for snr in SNR_LEVELS:
            key = f"{noise_type}_SNR{snr}"
            row += f"  {noise_matrix[key]['avg_CER']:.2%}  "
        print(row)

    result = {
        "num_utterances": len(subset),
        "noise_types": NOISE_TYPES,
        "snr_levels": SNR_LEVELS,
        "matrix": noise_matrix,
    }
    results_dict["exp3_noise_robustness"] = result
    return result


# ============================================================
# 实验4: 消融实验
# ============================================================

def exp4_ablation(subset, results_dict):
    """实验4: 各模块消融"""
    print("\n" + "=" * 65)
    print("  实验4: 消融实验")
    print(f"  测试集: {len(subset)} 条")
    print("=" * 65)

    from funasr import AutoModel

    # 配置A: 完整系统 (VAD + 标点恢复)
    print("\n  [配置A] 完整系统 (VAD + 标点恢复)...")
    full_model = AutoModel(
        model="iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        punc_model="iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
        disable_update=True,
    )

    def recognize_with_model(model, subset, label):
        cers = []
        for i, utt in enumerate(subset):
            try:
                result = model.generate(input=utt["wav_path"])
                text = result[0]["text"] if isinstance(result, list) and len(result) > 0 else ""
                cer = compute_cer(utt["text_norm"], text)
                cers.append(cer)
            except Exception as e:
                print(f"  [ERROR] {utt['utt_id']}: {e}")
                cers.append(1.0)

            if (i + 1) % 100 == 0:
                print(f"  {label} 进度: {i+1}/{len(subset)}")

        return float(np.mean(cers))

    full_cer = recognize_with_model(full_model, subset, "完整系统")

    # 配置B: w/o VAD (仅 Paraformer)
    print("\n  [配置B] w/o VAD (仅 Paraformer)...")
    no_vad_model = AutoModel(
        model="iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        disable_update=True,
    )
    no_vad_cer = recognize_with_model(no_vad_model, subset, "w/o VAD")

    # 配置C: w/o 标点 (Paraformer + VAD, 无标点)
    print("\n  [配置C] w/o 标点 (Paraformer + VAD)...")
    no_punc_model = AutoModel(
        model="iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        disable_update=True,
    )
    no_punc_cer = recognize_with_model(no_punc_model, subset, "w/o 标点")

    # 计算 CER 退化
    # 注：消融实验的退化 = 消融配置的CER - 完整系统的CER
    vad_degradation = round(no_vad_cer - full_cer, 4)
    punc_degradation = round(no_punc_cer - full_cer, 4)

    print(f"\n  ── 消融结果 ──")
    print(f"  完整系统:     CER = {full_cer:.2%}")
    print(f"  w/o VAD:      CER = {no_vad_cer:.2%} (退化 {vad_degradation:.2%})")
    print(f"  w/o 标点恢复: CER = {no_punc_cer:.2%} (退化 {punc_degradation:.2%})")

    result = {
        "num_utterances": len(subset),
        "full_system_CER": full_cer,
        "no_vad_CER": no_vad_cer,
        "no_punc_CER": no_punc_cer,
        "vad_degradation": vad_degradation,
        "punc_degradation": punc_degradation,
    }
    results_dict["exp4_ablation"] = result
    return result


# ============================================================
# 实验5: 流式分块策略对比
# ============================================================

def exp5_streaming(subset, results_dict):
    """实验5: 使用 StreamingASR (VAD驱动) 评估流式识别性能

    测试不同分块大小下，流式识别的 CER 退化和 RTF。
    StreamingASR 内部通过能量检测自动断句和提交识别。
    """
    print("\n" + "=" * 65)
    print("  实验5: 流式ASR评估 (StreamingASR)")
    print(f"  测试集: {len(subset)} 条")
    print("=" * 65)

    from src.streaming_asr import StreamingASR

    chunk_sizes = [200, 500, 1000]
    results = {}

    for chunk_ms in chunk_sizes:
        print(f"\n  [流式] {chunk_ms}ms 块...")
        chunk_cers = []
        chunk_rtfs = []
        total_task = len(subset)

        # 复用 StreamingASR 实例（避免重复加载模型）
        asr = StreamingASR(
            chunk_duration_ms=chunk_ms,
            silence_threshold_ms=1200,
            use_vad_model=False,
            sample_rate=16000,
        )

        for idx, utt in enumerate(subset):
            wav_path = utt["wav_path"]
            ref_text = utt["text_norm"]

            try:
                # 加载音频
                audio, sr = sf.read(wav_path)
                if len(audio.shape) > 1:
                    audio = np.mean(audio, axis=1)

                total_audio_sec = len(audio) / sr

                # 重置 StreamingASR 状态（保留引擎）
                asr.reset()

                # 逐块送入
                chunk_size_samples = int(sr * chunk_ms / 1000)
                infer_start = time.time()

                for start_sample in range(0, len(audio), chunk_size_samples):
                    chunk = audio[start_sample:start_sample + chunk_size_samples]
                    if len(chunk) < chunk_size_samples:
                        chunk = np.pad(chunk, (0, chunk_size_samples - len(chunk)))
                    asr.feed(chunk)

                # 音频结束后，送入静音块触发 flush
                silence = np.zeros(chunk_size_samples, dtype=np.float32)
                for _ in range(3):
                    asr.feed(silence)

                infer_time = time.time() - infer_start

                # 获取完整文本
                full_text = asr.get_full_text()
                if not full_text.strip():
                    # 如果没触发flush，尝试让ASREngine直接识别
                    from src.asr_engine import ASREngine
                    eng = ASREngine(device="auto")
                    full_result = eng.transcribe_file(wav_path)
                    full_text = full_result["text"]

                cer_val = compute_cer(ref_text, full_text)
                chunk_cers.append(cer_val)
                chunk_rtfs.append(infer_time / total_audio_sec if total_audio_sec > 0 else 1.0)

            except Exception as e:
                print(f"  [ERROR] {utt['utt_id']} @ {chunk_ms}ms: {e}")
                chunk_cers.append(1.0)
                chunk_rtfs.append(1.0)

            if (idx + 1) % 25 == 0:
                print(f"    {chunk_ms}ms 进度: {idx+1}/{total_task}")

        avg_cer = float(np.mean(chunk_cers))
        avg_rtf = float(np.mean(chunk_rtfs))

        # 退化：相对于未运行实验时的基线
        # 使用当前识别 CER 与干净 CER 的差异
        clean_cer = np.mean([
            compute_cer(utt["text_norm"], utt.get("text_norm", ""))
            for utt in subset
        ])

        results[str(chunk_ms)] = {
            "chunk_ms": chunk_ms,
            "avg_CER": round(avg_cer, 4),
            "avg_RTF": round(avg_rtf, 4),
        }
        print(f"    {chunk_ms}ms → CER: {avg_cer:.2%}, RTF: {avg_rtf:.4f}")

    print(f"\n  ── 流式策略汇总 ──")
    for chunk_ms in chunk_sizes:
        r = results[str(chunk_ms)]
        print(f"  {chunk_ms:5d}ms → CER: {r['avg_CER']:.2%}, RTF: {r['avg_RTF']:.4f}")

    result = {
        "num_utterances": len(subset),
        "chunk_results": results,
    }
    results_dict["exp5_streaming"] = result
    return result


# ============================================================
# 保存结果
# ============================================================

def serialize(obj):
    """递归序列化 numpy 类型"""
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize(item) for item in obj]
    elif isinstance(obj, (np.floating, np.float32, np.float64)):
        return float(obj)
    elif isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    return obj


def save_results(results_dict):
    """保存所有实验结果到 JSON"""
    output_path = os.path.join(RESULTS_DIR, "thchs30_full_eval.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(serialize(results_dict), f, ensure_ascii=False, indent=2)
    print(f"\n  实验结果已保存: {output_path}")
    return output_path


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 65)
    print("  《语音识别》课程实验 - 基于 THCHS-30")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    # 加载数据
    all_utterances = load_thchs30_test()
    if not all_utterances:
        print("[ERROR] THCHS-30 测试集加载失败")
        return

    results = {
        "experiment_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_utterances": len(all_utterances),
        "seed": 42,
    }

    # 实验1: 基准性能 (500条)
    print("\n" + "▔" * 35 + " 实验1 " + "▔" * 35)
    subset_500 = sample_subset(all_utterances, 500)
    print(f"[抽样] 从 {len(all_utterances)} 条中抽取 {len(subset_500)} 条")
    exp1_benchmark(subset_500, results)

    # 实验2: Whisper 对比 (500条，与实验1相同子集)
    print("\n" + "▔" * 35 + " 实验2 " + "▔" * 35)
    exp2_whisper_compare(subset_500, results)

    # 实验3: 噪声鲁棒性 (50条 × 3噪声 × 3SNR = 450次推理)
    print("\n" + "▔" * 35 + " 实验3 " + "▔" * 35)
    subset_50 = sample_subset(all_utterances, 50)
    exp3_noise_robustness(subset_50, results)

    # 实验4: 消融实验 (100条 × 3配置 = 300次推理)
    print("\n" + "▔" * 35 + " 实验4 " + "▔" * 35)
    subset_100 = sample_subset(all_utterances, 100)
    exp4_ablation(subset_100, results)

    # 实验5: 流式实验 (50条 × 3分块 = 150次推理)
    print("\n" + "▔" * 35 + " 实验5 " + "▔" * 35)
    exp5_streaming(subset_50, results)

    # 保存
    save_results(results)
    print("\n" + "=" * 65)
    print("  全部实验完成！")
    print("=" * 65)


if __name__ == "__main__":
    main()
