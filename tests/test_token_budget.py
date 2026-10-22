import json

import pytest

from sonoweave.token_budget import SplitRequired, TokenBudget
from sonoweave.translation import TranslationCancelled, TranslationError, Translator


class ByteModel:
    """Deterministic byte tokens, including a special prefix, for boundary tests."""
    def __init__(self, capacity=2048, outputs=None):
        self.capacity = capacity
        self.outputs = iter(outputs) if outputs is not None else None
        self.requests = []

    def n_ctx(self):
        return self.capacity

    def tokenize(self, data, add_bos=True, special=True):
        assert add_bos and special
        return [999, *data]

    def create_completion(self, prompt, max_tokens, **kwargs):
        assert isinstance(prompt, list) and prompt[0] == 999
        assert len(prompt) + max_tokens + 8 <= self.capacity
        self.requests.append((bytes(prompt[1:]).decode("utf-8"), max_tokens))
        output = next(self.outputs) if self.outputs is not None else "译文"
        return iter([{"choices": [{"text": output, "finish_reason": "stop"}]}])


def simple_prompt(text, context):
    return "S|" + "|".join(context) + "|U|" + text


def simple_retry(text):
    return "Retry|" + json.dumps(text, ensure_ascii=False)


def test_full_prompt_counts_special_prefix_and_preserves_output_space():
    model = ByteModel(400)
    budget = TokenBudget(model)
    plan = budget.prepare("current", (), simple_prompt, simple_retry)
    assert plan.tokens[0] == 999
    assert plan.output_tokens == 256
    assert len(plan.tokens) + plan.output_tokens + budget.margin <= 400
    assert len(plan.retry_tokens) + plan.output_tokens + budget.margin <= 400


def test_trim_oldest_reference_before_touching_newer_reference_or_source():
    model = ByteModel(400)
    budget = TokenBudget(model)
    old, new, source = "old" * 35, "new" * 15, "source" * 10
    plan = budget.prepare(source, (old, new), simple_prompt, simple_retry)
    assert plan.context[-1] == new
    assert len(plan.context[0]) < len(old) and old.endswith(plan.context[0])
    assert bytes(plan.tokens[1:]).decode().endswith(source)


def test_unicode_source_splits_only_after_reference_can_no_longer_help():
    budget = TokenBudget(ByteModel(600))
    source = "𰻞" * 240
    with pytest.raises(SplitRequired) as caught:
        budget.prepare(source, ("older", "newer"), simple_prompt, simple_retry)
    cut = caught.value.cut
    assert 0 < cut < len(source)
    assert source[:cut] + source[cut:] == source
    head = budget.prepare(source[:cut], (), simple_prompt, simple_retry)
    assert bytes(head.tokens[1:]).decode("utf-8").endswith(source[:cut])


def test_retry_json_escaping_is_budgeted_even_if_first_template_fits():
    model = ByteModel(600)
    budget = TokenBudget(model)
    source = "\x01" * 100
    assert budget.fits(budget.encode(simple_prompt(source, ())))
    assert not budget.fits(budget.encode(simple_retry(source)))
    with pytest.raises(SplitRequired):
        budget.prepare(source, (), simple_prompt, simple_retry)


def test_public_translation_splits_without_dropping_source_and_publishes_progress():
    model = ByteModel(1400)
    translator = Translator(model)
    source = "𰻞" * 240
    progress = []
    translator.translate(source, "zh-CN", "en", on_progress=progress.append)
    assert len(model.requests) > 1 and len(progress) == len(model.requests)
    recovered = "".join(prompt.split("<|im_start|>user\n", 1)[1].split("<|im_end|>", 1)[0] for prompt, _ in model.requests)
    assert recovered == source
    count = len(model.requests)
    translator.translate(source, "zh-CN", "en")
    assert len(model.requests) == count  # Stable sub-sentences remain cached.


def test_trimmed_context_is_used_in_cache_key():
    model = ByteModel(1500)
    translator = Translator(model)
    context = ("𰻞" * 160, "𰻞" * 160)
    translator.translate("The model is ready.", "zh-CN", "en", context=context)
    key = next(iter(translator.cache))
    assert sum(map(len, key[-1])) < 320
    assert key[-1][-1].endswith("𰻞")


def test_retry_retains_budget_and_target_language():
    model = ByteModel(2048, ["n-", "こんにちは。"])
    translator = Translator(model)
    assert translator.translate("Hello there.", "ja", "en") == "こんにちは。"
    assert len(model.requests) == 2
    assert all("Japanese" in prompt for prompt, _ in model.requests)
    assert all(output == 256 for _, output in model.requests)


def test_impossibly_small_window_fails_without_inference_or_caching():
    model = ByteModel(100)
    translator = Translator(model)
    with pytest.raises(TranslationError, match="上下文太小"):
        translator.translate("A sentence.", "zh-CN", "en")
    assert not model.requests and not translator.cache


def test_budget_planning_can_be_cancelled_without_inference():
    model = ByteModel(1400)
    translator = Translator(model)
    calls = []

    def cancelled():
        calls.append(True)
        return len(calls) >= 3

    with pytest.raises(TranslationCancelled):
        translator.translate("A sentence.", "zh-CN", "en", should_cancel=cancelled)
    assert not model.requests and not translator.cache


def test_final_generation_gate_cannot_silently_shrink_reserved_output():
    model = ByteModel(400)
    translator = Translator(model)
    with pytest.raises(TranslationError):
        translator._generate(tuple(range(200)), 256, None)
    assert not model.requests
