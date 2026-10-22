"""Plan exact token sequences before sending a completion to llama.cpp."""
from dataclasses import dataclass


class TokenBudgetError(RuntimeError):
    pass


class SplitRequired(Exception):
    def __init__(self, cut):
        self.cut = cut


@dataclass(frozen=True)
class PromptPlan:
    context: tuple[str, ...]
    tokens: tuple[int, ...]
    retry_tokens: tuple[int, ...]
    output_tokens: int


class TokenBudget:
    def __init__(self, llm, output_tokens=256, margin=8):
        self.llm = llm
        self.output_tokens = output_tokens
        self.margin = margin

    @property
    def capacity(self):
        return self.llm.n_ctx()

    def encode(self, prompt):
        # Encode special markers and the model's special prefix exactly once.
        # Passing these IDs to create_completion prevents any re-tokenization.
        return tuple(self.llm.tokenize(prompt.encode("utf-8"), add_bos=True, special=True))

    def fits(self, tokens):
        return len(tokens) + self.output_tokens + self.margin <= self.capacity

    def validate(self, tokens, output_tokens):
        if output_tokens != self.output_tokens or not self.fits(tokens):
            raise TokenBudgetError("翻译上下文不足，无法保留完整输出空间")

    def _fit(self, text, context, build, retry):
        retry_tokens = self.encode(retry(text))
        if not self.fits(retry_tokens):
            return None

        def candidate(kept):
            tokens = self.encode(build(text, kept))
            if self.fits(tokens):
                return PromptPlan(kept, tokens, retry_tokens, self.output_tokens)
            return None

        kept = tuple(context)
        plan = candidate(kept)
        if plan is not None:
            return plan
        # Remove from the oldest end, preserving all newer references first.
        while kept:
            oldest, newer = kept[0], kept[1:]
            low, high, best = 1, len(oldest) - 1, None
            while low <= high:
                length = (low + high) // 2
                shortened = (oldest[-length:], *newer)
                trial = candidate(shortened)
                if trial is not None:
                    best, low = trial, length + 1
                else:
                    high = length - 1
            if best is not None:
                return best
            kept = newer
            plan = candidate(kept)
            if plan is not None:
                return plan
        return None

    def prepare(self, text, context, build, retry, check_cancel=None):
        if check_cancel:
            check_cancel()
        plan = self._fit(text, context, build, retry)
        if plan is not None:
            return plan
        # Even without reference text, the current source does not fit. Split
        # at Unicode character boundaries, never by slicing UTF-8 token bytes.
        low, high, best = 1, len(text) - 1, 0
        while low <= high:
            if check_cancel:
                check_cancel()
            cut = (low + high) // 2
            if self._fit(text[:cut], (), build, retry) is not None:
                best, low = cut, cut + 1
            else:
                high = cut - 1
        if not best:
            raise TokenBudgetError("可用上下文太小，无法容纳最小翻译片段及输出空间")
        # Prefer a nearby clause/word boundary. All candidates are re-checked,
        # since BPE token counts need not be monotonic in character length.
        boundaries = [i + 1 for i, char in enumerate(text[:best])
                      if char.isspace() or char in ",;，；。！？.!?"]
        for cut in reversed(boundaries):
            if cut < best // 2:
                break
            if self._fit(text[:cut], (), build, retry) is not None:
                best = cut
                break
        raise SplitRequired(best)
