"""
可视化演示界面：一键运行所有功能
==================================
用法: python demo.py

功能：
  - 实时录音识别（边说边出字）
  - 上传音频文件识别（支持WAV/MP3/FLAC/M4A）
  - 流式识别演示（模拟实时，逐步输出）
  - 查看实验图表
  - 原始终端菜单

不中断正在运行的程序。
"""

import os
import sys
import subprocess
import tempfile
import time

import gradio as gr
import numpy as np

PROJECT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT)

# 全局单例模型
_engine = None

def _get_engine():
    """懒加载：模型只在首次调用时加载一次"""
    global _engine
    if _engine is None:
        from src.asr_engine import ASREngine
        _engine = ASREngine(device="auto")
    return _engine


# ============================================================
# 核心功能函数
# ============================================================

def recognize_file(audio_path):
    """上传音频文件识别"""
    if audio_path is None:
        return "请先上传音频文件", 0
    try:
        engine = _get_engine()
        start = time.time()
        result = engine.transcribe_file(audio_path)
        elapsed = time.time() - start
        text = result["text"] if result["text"] else "（未识别出文本）"
        return text, round(elapsed, 2)
    except Exception as e:
        return f"识别失败: {e}", 0


def recognize_mic(audio):
    """麦克风录音识别（录完再识别）"""
    if audio is None:
        return "请先录制音频", 0
    try:
        import soundfile as sf
        sr, data = audio  # Gradio 返回 (sample_rate, numpy array)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, data, sr)
            tmp = f.name
        engine = _get_engine()
        start = time.time()
        result = engine.transcribe_file(tmp)
        elapsed = time.time() - start
        os.unlink(tmp)
        text = result["text"] if result["text"] else "（未识别出文本）"
        return text, round(elapsed, 2)
    except Exception as e:
        return f"识别失败: {e}", 0


def recognize_file(audio_path):
    """上传音频文件识别"""
    if audio_path is None:
        return "请先上传音频文件", 0
    try:
        engine = _get_engine()
        start = time.time()
        result = engine.transcribe_file(audio_path)
        elapsed = time.time() - start
        text = result["text"] if result["text"] else "（未识别出文本）"
        return text, round(elapsed, 2)
    except Exception as e:
        return f"识别失败: {e}", 0


def streaming_recognize(audio_path, chunk_ms, progress=gr.Progress()):
    """流式识别演示（可选择分块大小）"""
    if audio_path is None:
        return "请先上传音频文件", ""
    try:
        import librosa
        from src.streaming_asr import StreamingASR

        audio, sr = librosa.load(audio_path, sr=16000, mono=True)
        chunk_ms_int = int(chunk_ms)
        asr = StreamingASR(chunk_duration_ms=chunk_ms_int, silence_threshold_ms=1200, use_vad_model=False)
        chunk_size = int(sr * chunk_ms_int / 1000)

        outputs = []
        flush_count = 0
        start = time.time()

        for i in range(0, len(audio), chunk_size):
            chunk = audio[i:i + chunk_size]
            if len(chunk) < chunk_size:
                chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
            result = asr.feed(chunk)
            if result["is_final"] and result["text"]:
                progress_val = min(i / len(audio), 1.0)
                progress(progress_val, desc="识别中...")
                flush_count += 1
                outputs.append(result["text"])
                yield "\n".join(outputs), asr.get_full_text()

        # 送入足够静音触发 flush（保证 200ms 块也需要 ≥6个）
        silences_needed = max(6, int(1200 / chunk_ms_int) + 1)
        silence = np.zeros(chunk_size, dtype=np.float32)
        for _ in range(silences_needed):
            asr.feed(silence)
            yield "\n".join(outputs), asr.get_full_text()

        progress(1.0, desc="完成")
        full = asr.get_full_text()
        total_time = time.time() - start
        audio_dur = len(audio) / sr
        summary = (
            f"【统计】总处理时间: {total_time:.2f}s | "
            f"音频时长: {audio_dur:.1f}s | "
            f"RTF: {total_time/audio_dur:.4f} | "
            f"Flush次数: {flush_count}"
        )
        outputs.append(f"[{summary}]")
        outputs.append(f"【完整文本】{full}")
        yield "\n".join(outputs), full

    except Exception as e:
        yield f"流式识别失败: {e}", ""


# ============================================================
# Gradio 界面
# ============================================================

def build_interface():
    with gr.Blocks(title="语音识别演示系统", theme=gr.themes.Soft()) as demo:
        gr.Markdown(
            """
            # 🎤 语音识别演示系统
            **基于 Paraformer (Conformer) 的中文语音识别**
            """
        )

        with gr.Tab("🎙 录音后识别"):
            gr.Markdown("点击下方麦克风按钮录音，录完后自动识别")
            audio_input = gr.Audio(sources=["microphone"], type="numpy", label="录音")
            with gr.Row():
                recog_btn = gr.Button("开始识别", variant="primary", scale=1)
                clear_btn = gr.Button("清空", scale=1)
            mic_result = gr.Textbox(label="识别结果", lines=4, placeholder="识别结果将显示在这里...")
            mic_time = gr.Number(label="识别耗时(秒)", value=0)
            recog_btn.click(fn=recognize_mic, inputs=audio_input, outputs=[mic_result, mic_time])

        with gr.Tab("📁 上传音频识别"):
            gr.Markdown("支持 WAV / MP3 / FLAC / M4A 格式")
            file_input = gr.Audio(sources=["upload"], type="filepath", label="选择音频文件")
            file_btn = gr.Button("开始识别", variant="primary")
            file_result = gr.Textbox(label="识别结果", lines=4, placeholder="识别结果将显示在这里...")
            file_time = gr.Number(label="识别耗时(秒)", value=0)
            file_btn.click(fn=recognize_file, inputs=file_input, outputs=[file_result, file_time])

        with gr.Tab("🔄 流式识别演示"):
            gr.Markdown("模拟实时流式识别：可选择不同分块大小")
            stream_file = gr.Audio(sources=["upload"], type="filepath", label="选择音频文件")
            chunk_radio = gr.Radio(
                choices=[("200ms（推荐）", 200), ("500ms", 500), ("1000ms", 1000)],
                value=200,
                label="分块大小",
            )
            stream_btn = gr.Button("开始流式识别", variant="primary")
            stream_progress = gr.Textbox(label="逐步输出", lines=6, placeholder="识别结果会逐步显示在这里...")
            stream_full = gr.Textbox(label="完整文本", lines=3)
            stream_btn.click(
                fn=streaming_recognize,
                inputs=[stream_file, chunk_radio],
                outputs=[stream_progress, stream_full],
            )

    return demo


# ============================================================
# 终端菜单模式（保留原始终端版本）
# ============================================================

def terminal_menu():
    def clear():
        os.system("cls" if sys.platform == "win32" else "clear")

    def run_script(script_path):
        abs_path = os.path.join(PROJECT, script_path)
        print(f"\n> 运行: python {script_path}")
        print("-" * 50)
        subprocess.run([sys.executable, abs_path], cwd=PROJECT)

    clear()
    print("=" * 60)
    print("  语音识别项目 演示菜单（终端模式）")
    print("=" * 60)
    print()
    print("  1. 实时录音识别（边说边出字）← 答辩推荐")
    print("  2. 已有音频文件识别")
    print("  3. 流式识别演示（模拟实时）")
    print("  4. 查看所有实验图表")
    print("  5. 启动 Gradio Web 界面")
    print("  6. 退出")
    print()

    while True:
        choice = input("请选择 (1-6): ").strip()

        if choice == "1":
            print("\n[提示] 实时录音需要麦克风，按 Enter 开始/结束")
            input("准备好后按 Enter 开始...")
            run_script("demo/streaming_demo.py")

        elif choice == "2":
            sample_dir = os.path.join(PROJECT, "experiments", "sample_audio")
            mp3s = []
            if os.path.exists(sample_dir):
                mp3s = [f for f in os.listdir(sample_dir) if f.lower().endswith((".mp3", ".wav", ".m4a"))]
            if mp3s:
                print("\n可用音频文件:")
                for i, f in enumerate(mp3s, 1):
                    print(f"  {i}. {f}")
                print(f"  {len(mp3s)+1}. 自定义路径")
                fc = input("选择: ").strip()
                if fc.isdigit() and 1 <= int(fc) <= len(mp3s):
                    audio_path = os.path.join(sample_dir, mp3s[int(fc) - 1])
                else:
                    audio_path = input("请输入音频文件路径: ").strip()
            else:
                audio_path = input("请输入音频文件路径: ").strip()

            if audio_path and os.path.exists(audio_path):
                print(f"\n识别中: {os.path.basename(audio_path)}")
                from src.asr_engine import ASREngine
                engine = ASREngine(device="auto")
                result = engine.transcribe_file(audio_path)
                print("-" * 50)
                print(f"识别结果: {result['text']}")
                print("-" * 50)
            else:
                print("文件不存在")

        elif choice == "3":
            sample_dir = os.path.join(PROJECT, "experiments", "sample_audio")
            mp3s = []
            if os.path.exists(sample_dir):
                mp3s = [f for f in os.listdir(sample_dir) if f.lower().endswith((".mp3", ".wav", ".m4a"))]
            if mp3s:
                print("\n音频文件:")
                for i, f in enumerate(mp3s, 1):
                    print(f"  {i}. {f}")
                fc = input("选择: ").strip()
                if fc.isdigit() and 1 <= int(fc) <= len(mp3s):
                    audio_path = os.path.join(sample_dir, mp3s[int(fc) - 1])
                else:
                    audio_path = None
            else:
                audio_path = None

            if audio_path:
                import librosa
                from src.streaming_asr import StreamingASR
                print(f"\n流式识别: {os.path.basename(audio_path)}")
                audio, sr = librosa.load(audio_path, sr=16000, mono=True)
                asr = StreamingASR(chunk_duration_ms=1000, silence_threshold_ms=1200, use_vad_model=False)
                chunk_size = int(16000 * 1.0)
                start = time.time()
                for i in range(0, len(audio), chunk_size):
                    chunk = audio[i:i + chunk_size]
                    if len(chunk) < chunk_size:
                        chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
                    result = asr.feed(chunk)
                    if result["is_final"] and result["text"]:
                        progress = min(i / len(audio) * 100, 100)
                        print(f"  [{progress:5.1f}%] {result['text']}")
                silence = np.zeros(chunk_size, dtype=np.float32)
                for _ in range(3):
                    asr.feed(silence)
                elapsed = time.time() - start
                print("-" * 50)
                print(f"完整文本: {asr.get_full_text()}")
                print(f"RTF: {elapsed/(len(audio)/sr):.4f}")

        elif choice == "4":
            results_dir = os.path.join(PROJECT, "experiments", "results")
            pngs = []
            if os.path.exists(results_dir):
                pngs = [f for f in os.listdir(results_dir) if f.endswith(".png")]
            if pngs:
                print("\n实验图表:")
                for i, f in enumerate(pngs, 1):
                    size_kb = os.path.getsize(os.path.join(results_dir, f)) / 1024
                    print(f"  {i:2d}. {f:35s} ({size_kb:.0f} KB)")
                print(f"\n图表位置: {results_dir}")
            else:
                print("\n未找到图表")

        elif choice == "5":
            print("\n启动 Gradio Web 界面...")
            print("浏览器打开: http://127.0.0.1:7860")
            print("按 Ctrl+C 停止")
            run_script("run_demo.py")

        elif choice == "6":
            print("\n退出演示")
            break

        else:
            print("无效选择，请输入 1-6")

        print()


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    if "--terminal" in sys.argv:
        terminal_menu()
    else:
        print("启动可视化界面...")
        print("默认浏览器将自动打开")
        print("如需终端菜单模式，请运行: python demo.py --terminal")
        print()
        demo = build_interface()
        demo.launch(inbrowser=True)
