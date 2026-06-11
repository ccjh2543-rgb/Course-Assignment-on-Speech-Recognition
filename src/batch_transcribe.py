import os
import json
import argparse
from src.asr_engine import ASREngine, WhisperEngine
from src.audio_utils import list_audio_files


def batch_transcribe(input_dir, output_path, engine_type="funasr", model_size="large", device="auto"):
    audio_files = list_audio_files(input_dir)
    if not audio_files:
        print(f"在 {input_dir} 中未找到音频文件")
        return

    print(f"找到 {len(audio_files)} 个音频文件")

    if engine_type == "whisper":
        engine = WhisperEngine(model_size=model_size, device=device)
    else:
        engine = ASREngine(device=device)

    results = []
    for audio_path in audio_files:
        filename = os.path.basename(audio_path)
        print(f"转录: {filename}")
        try:
            result = engine.transcribe_file(audio_path)
            results.append({
                "file": filename,
                "path": audio_path,
                "text": result["text"],
                "elapsed": result["elapsed"],
            })
            print(f"  → {result['text'][:80]}... ({result['elapsed']}s)")
        except Exception as e:
            print(f"  → 错误: {e}")
            results.append({
                "file": filename,
                "path": audio_path,
                "text": "",
                "error": str(e),
            })

    output = {
        "engine": engine_type,
        "model": model_size if engine_type == "whisper" else "Paraformer-Conformer",
        "total_files": len(audio_files),
        "results": results,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n批量转录完成，结果已保存: {output_path}")

    total_time = sum(r.get("elapsed", 0) for r in results)
    print(f"总耗时: {total_time:.2f}s | 平均: {total_time/len(results):.2f}s/文件")


def main():
    parser = argparse.ArgumentParser(description="批量音频转录工具")
    parser.add_argument("input_dir", help="输入音频目录")
    parser.add_argument("--output", "-o", default="transcription_results.json", help="输出JSON路径")
    parser.add_argument("--engine", "-e", choices=["funasr", "whisper"], default="funasr", help="ASR引擎")
    parser.add_argument("--model", "-m", default="large", help="Whisper模型尺寸 (tiny/base/small/medium/large)")
    parser.add_argument("--device", "-d", default="auto", help="运行设备 (auto/cuda/cpu)")
    args = parser.parse_args()

    batch_transcribe(args.input_dir, args.output, args.engine, args.model, args.device)


if __name__ == "__main__":
    main()
