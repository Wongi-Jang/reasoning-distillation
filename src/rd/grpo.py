"""GRPO with a correctness reward (Shao et al. 2024), with DAPO's clip-higher, token-level loss and no KL term
(Yu et al. 2025) and Dr. GRPO's unscaled advantages (Liu et al. 2025).
Reward = 1 if the sampled answer is correct AND the sample finished within the token cap, else 0, so a student
that circles until the cap gets nothing for it. Round driver: rd.grpo_run.
"""
import math
import random

import torch

from rd.data import PROMPT


def encode(tok, problem, response, finished):
    """Prompt ids + response ids (+ <|im_end|> if the sample stopped by itself). Returns (ids, start of response)."""
    prompt = tok.apply_chat_template([{"role": "user", "content": PROMPT.format(problem=problem)}],
                                     tokenize=False, add_generation_prompt=True, enable_thinking=True)
    p = tok(prompt, add_special_tokens=False)["input_ids"]
    r = tok(response + ("<|im_end|>" if finished else ""), add_special_tokens=False)["input_ids"]
    return p + r, len(p)


def advantages(rewards):
    """Group-relative advantage, one group = the samples of one problem. No division by the group std (Dr. GRPO):
    it would up-weight groups that are nearly all right or all wrong. Uniform groups get 0 everywhere."""
    m = sum(rewards) / len(rewards)
    return [r - m for r in rewards]


def response_logprobs(model, ids, start, device):
    """log p(token) for every response token; position i predicts token i+1."""
    x = torch.tensor([ids], device=device)
    with torch.autocast(device.type, dtype=torch.bfloat16):
        logits = model(input_ids=x).logits
    logp = torch.log_softmax(logits[0, start - 1:-1].float(), dim=-1)
    return logp.gather(1, x[0, start:, None])[:, 0]


@torch.no_grad()
def old_logprobs(model, data, device):
    """Log-probs under the policy that sampled the rollouts (= the weights before this round's updates)."""
    return [response_logprobs(model, ids, start, device).tolist() for ids, start, _ in data]


def step_loss(model, batch, device, eps_low=0.2, eps_high=0.28):
    """batch: list of (ids, start, advantage, old_logprobs). PPO clipped surrogate, summed over tokens and divided
    by the batch's total response tokens (DAPO token-level loss: long sequences count per token, not per sample).
    eps_high > eps_low (clip-higher) leaves room to raise rare tokens, against entropy collapse."""
    n_tok = sum(len(ids) - start for ids, start, _, _ in batch)
    clipped = 0
    for ids, start, adv, old in batch:
        lp = response_logprobs(model, ids, start, device)
        ratio = torch.exp(lp - torch.tensor(old, device=device))
        surr = torch.minimum(ratio * adv, ratio.clamp(1 - eps_low, 1 + eps_high) * adv)
        (-surr.sum() / n_tok).backward()
        clipped += ((ratio < 1 - eps_low) | (ratio > 1 + eps_high)).sum().item()
    return clipped / n_tok, n_tok


def train_round(model, opt, data, lr, seqs_per_step, device):
    """Several PPO steps over one round's rollouts. data: list of (ids, start, advantage), zero-advantage dropped.
    Returns (per-step logs, mean log-prob of the sampled tokens before the update: falling = student less certain)."""
    old = old_logprobs(model, data, device)
    mean_lp = sum(map(sum, old)) / sum(map(len, old))
    data = [(*d, o) for d, o in zip(data, old)]
    random.shuffle(data)
    for g in opt.param_groups:
        g["lr"] = lr
    logs = []
    for i in range(0, len(data), seqs_per_step):
        clip_frac, n_tok = step_loss(model, data[i:i + seqs_per_step], device)
        gnorm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0).item()
        if not math.isfinite(gnorm):
            raise RuntimeError(f"non-finite grad norm {gnorm}")
        opt.step()
        opt.zero_grad()
        logs.append(dict(clip_frac=clip_frac, gnorm=gnorm, n_tok=n_tok))
    return logs, mean_lp
