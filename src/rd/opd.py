"""On-policy distillation: the student samples, the teacher scores every sampled token, the student
minimizes per-token reverse KL  KL(student || teacher)  on its own rollouts (Agarwal et al. 2024; Qwen3 report §4.5).
Round driver (rollouts, teacher scoring, CPU offload): rd.opd_run.
"""
import math
import random

import torch

from rd.data import PROMPT


def encode(tok, problem, token_ids):
    """Prompt ids + the rollout's response ids (already tokenized by rd.score). Returns (ids, start of response)."""
    prompt = tok.apply_chat_template([{"role": "user", "content": PROMPT.format(problem=problem)}],
                                     tokenize=False, add_generation_prompt=True, enable_thinking=True)
    p = tok(prompt, add_special_tokens=False)["input_ids"]
    return p + token_ids, len(p)


def step_loss(model, batch, device):
    """batch: list of (ids, start, teacher_logprobs). Backprops one sequence at a time (like sft.step_loss).

    Per token t of the student's own sample y:  advantage_t = log p_T(y_t) - log p_S(y_t)   (no grad),
    centered by its mean over the sequence (baseline; a sequence is backpropped alone, so no batch mean)
    loss = -sum_t advantage_t * log p_S(y_t) / n_tok
    Its gradient is exactly the gradient of the reverse KL on on-policy samples: tokens the teacher likes
    less than the student does get pushed down. Each token gets only its own advantage, no future tokens
    (discount 0, as in Thinking Machines' on-policy distillation).
    """
    n_tok = sum(len(ids) - start for ids, start, _ in batch)
    kl = 0.0
    for ids, start, t_lp in batch:
        x = torch.tensor([ids], device=device)
        with torch.autocast(device.type, dtype=torch.bfloat16):
            logits = model(input_ids=x).logits
        logp = torch.log_softmax(logits[0, start - 1:-1].float(), dim=-1)  # position i predicts token i+1
        s_lp = logp.gather(1, x[0, start:, None])[:, 0]                    # log p_S of the sampled tokens
        adv = torch.tensor(t_lp, device=device) - s_lp.detach()
        kl -= adv.sum().item()  # sum of (log p_S - log p_T) = sampled reverse-KL estimate
        # baseline: the raw advantage is ~-0.3 on almost every token, so un-centered updates are mostly noise.
        # Subtracting a constant keeps the gradient unbiased (E_y[grad log p_S(y)] = 0 in every context).
        adv = adv - adv.mean()
        loss = -(adv * s_lp).sum() / n_tok
        loss.backward()
    return kl / n_tok, n_tok


def train_round(model, opt, data, lr, seqs_per_step, device):
    """One pass over this round's rollouts, constant lr. data: list of (ids, start, teacher_logprobs).
    Rollouts come from the policy at the start of the round, so later steps are slightly off-policy.
    ponytail: no importance weights; add a ratio / PPO clip if training goes unstable."""
    random.shuffle(data)
    for g in opt.param_groups:
        g["lr"] = lr
    logs = []
    for i in range(0, len(data), seqs_per_step):
        kl, n_tok = step_loss(model, data[i:i + seqs_per_step], device)
        if not math.isfinite(kl):
            raise RuntimeError(f"non-finite kl {kl}")
        gnorm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0).item()
        opt.step()
        opt.zero_grad()
        logs.append(dict(kl=kl, gnorm=gnorm, n_tok=n_tok))
    return logs
