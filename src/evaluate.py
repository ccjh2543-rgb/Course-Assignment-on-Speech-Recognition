import os
import re
import json
import numpy as np
from jiwer import cer, wer

# 繁简转换（Whisper输出繁体中文，需转为简体计算CER）
try:
    from zhconv import convert
    _use_zhconv = True
except ImportError:
    _use_zhconv = False


def normalize_text(text):
    # 繁简转换
    if _use_zhconv:
        text = convert(text, 'zh-cn')
    # 保留中文字符、字母、数字、下划线
    text = re.sub(r"[^\u4e00-\u9fff\w]", "", text)
    return text.strip()


def compute_cer(reference, hypothesis):
    ref_norm = normalize_text(reference)
    hyp_norm = normalize_text(hypothesis)
    return round(cer(ref_norm, hyp_norm), 4)


def compute_wer(reference, hypothesis):
    ref_norm = normalize_text(reference)
    hyp_norm = normalize_text(hypothesis)
    return round(wer(ref_norm, hyp_norm), 4)


def evaluate_transcriptions(references, hypotheses):
    results = []
    all_cer = []
    all_wer = []

    for ref, hyp in zip(references, hypotheses):
        c = compute_cer(ref, hyp)
        w = compute_wer(ref, hyp)
        all_cer.append(c)
        all_wer.append(w)
        results.append({
            "reference": ref,
            "hypothesis": hyp,
            "CER": c,
            "WER": w,
        })

    return {
        "results": results,
        "avg_CER": round(float(np.mean(all_cer)), 4),
        "avg_WER": round(float(np.mean(all_wer)), 4),
        "std_CER": round(float(np.std(all_cer)), 4),
        "std_WER": round(float(np.std(all_wer)), 4),
    }


def evaluate_from_files(ref_file, hyp_file):
    with open(ref_file, "r", encoding="utf-8") as f:
        references = [line.strip() for line in f if line.strip()]
    with open(hyp_file, "r", encoding="utf-8") as f:
        hypotheses = [line.strip() for line in f if line.strip()]

    return evaluate_transcriptions(references, hypotheses)


def save_evaluation_report(eval_result, output_path):
    report = {
        "summary": {
            "avg_CER": eval_result["avg_CER"],
            "avg_WER": eval_result["avg_WER"],
            "std_CER": eval_result["std_CER"],
            "std_WER": eval_result["std_WER"],
        },
        "details": [
            {
                "utterance": i + 1,
                "reference": r["reference"],
                "hypothesis": r["hypothesis"],
                "CER": r["CER"],
                "WER": r["WER"],
            }
            for i, r in enumerate(eval_result["results"])
        ],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"评估报告已保存: {output_path}")
    return report
