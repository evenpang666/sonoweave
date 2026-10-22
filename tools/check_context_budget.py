"""Verify both local models with real tokenizers and audited offline completions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")

from sonoweave import __version__
from sonoweave.config import translation_model_location
from sonoweave.token_budget import SplitRequired
from sonoweave.translation import Translator, build_prompt, build_retry_prompt


def no_network(*args, **kwargs):
    raise AssertionError("Offline inference attempted a network connection")


def check_model(model):
    report = {"model": model, "windows": []}
    for capacity in (2048, 512):
        translator = Translator(model_path=translation_model_location(model), context_size=capacity)
        requests, sources, injected = [], [], [False]
        original = translator.llm.create_completion

        def audited_completion(**kwargs):
            prompt, output = kwargs["prompt"], kwargs["max_tokens"]
            assert isinstance(prompt, list)
            assert len(prompt) + output + 8 <= translator.llm.n_ctx()
            assert output == 256
            decoded = translator.llm.detokenize(prompt, special=True).decode("utf-8")
            source = decoded.split("<|im_start|>user\n", 1)[1].split("<|im_end|>", 1)[0]
            sources.append(source)
            requests.append({"input_tokens": len(prompt), "output_reserved": output, "margin": 8,
                             "injected_response": injected[0]})
            if injected[0]:
                injected[0] = False
                return iter([{"choices": [{"text": "n-", "finish_reason": "stop"}]}])
            return original(**kwargs)

        translator.llm.create_completion = audited_completion
        row = {"context_size": capacity, "translations": [], "requests": requests}
        try:
            translator.warmup()
            if capacity == 2048:
                rare, context = "𰻞" * 240, ("𰻞" * 160, "𰻞" * 160)
                old_tokens = translator.token_budget.encode(build_prompt(rare, "zh-CN", context))
                plan = translator.token_budget.prepare(rare, context,
                    lambda text, ref: build_prompt(text, "zh-CN", ref),
                    lambda text: build_retry_prompt(text, "zh-CN"))
                assert len(old_tokens) > capacity
                assert len(plan.tokens) + plan.output_tokens + 8 <= capacity
                assert plan.context[-1] == context[-1]
                row["rare_character_boundary"] = {"input_before": len(old_tokens), "input_after": len(plan.tokens),
                    "source_chars": len(rare), "context_chars_before": 320,
                    "context_chars_after": sum(map(len, plan.context)), "output_reserved": plan.output_tokens}
                # Also test escaped retry payloads without asking the model to
                # invent a translation for control bytes or repeated rare glyphs.
                escaped = "\x01" * 240
                escaped_plan = translator.token_budget.prepare(escaped, context,
                    lambda text, ref: build_prompt(text, "zh-CN", ref),
                    lambda text: build_retry_prompt(text, "zh-CN"))
                translator.token_budget.validate(escaped_plan.retry_tokens, escaped_plan.output_tokens)
                row["escaped_retry_budget_checked"] = True
                for text, target, source, context in (
                    ("He stood beside the bank.", "zh-CN", "en", ("They were fishing by the river.",)),
                    ("The temperature is 37.5 degrees, not 35 degrees.", "zh-CN", "en", ()),
                ):
                    translated = translator.translate(text, target, source, context=context)
                    assert translated.strip()
                    row["translations"].append({"source": text, "target": target, "translation": translated})
                injected[0] = True
                translated = translator.translate("这个模型完全在本机运行。", "ja", "zh")
                assert translated and "n-" not in translated
                row["non_chinese_retry_after_injected_artifact"] = translated
            else:
                text = ("这个工具会持续监听电脑默认输出设备中的语音并在本机识别出文字"
                        "识别完成后程序会参考最近的原文来翻译当前句子以保持人物指代和专有名称一致"
                        "较早的完整句子会保存在内存里以便复用而最新部分会随着后续内容到来继续修订"
                        "用户可以选择目标语言并根据电脑性能在两个本地模型之间切换"
                        "模型首次使用时自动下载以后可以断开网络继续使用字幕窗口支持拖动移动和调整宽度")
                requests.clear()
                sources.clear()
                progress = []
                translated = translator.translate(text, "en", "zh", on_progress=progress.append)
                assert translated.strip() and len(requests) > 1
                assert "".join(sources) == text
                assert len(progress) == len(requests)
                row["forced_split"] = {"source_chars": len(text), "pieces": len(requests),
                                       "source_preserved": True, "progress_events": len(progress),
                                       "translation": translated}
            row["passed"] = True
        finally:
            translator.close()
        report["windows"].append(row)
        print(json.dumps({"model": model, "context_size": capacity, "passed": row["passed"],
                          "requests": len(requests)}, ensure_ascii=True), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=("4b", "2b", "both"), default="both")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/context-budget-v052.json")
    args = parser.parse_args()
    report = {"version": __version__, "network_blocked": True, "system_audio_captured": False, "models": []}
    with patch.object(socket.socket, "connect", no_network), patch.object(socket, "create_connection", no_network):
        for model in ("4b", "2b") if args.model == "both" else (args.model,):
            report["models"].append(check_model(model))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REAL MODEL TOKEN BUDGET CHECKS PASSED", flush=True)


if __name__ == "__main__":
    main()
