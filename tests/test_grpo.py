import torch
from transformers import Qwen3Config, Qwen3ForCausalLM

from rd.grpo import advantages, old_logprobs, step_loss


def tiny():
    torch.manual_seed(0)
    return Qwen3ForCausalLM(Qwen3Config(vocab_size=50, hidden_size=32, intermediate_size=64, num_hidden_layers=2,
                                        num_attention_heads=4, num_key_value_heads=2, head_dim=8))


def test_advantages():
    assert advantages([1.0, 1.0, 1.0]) == [0.0, 0.0, 0.0]
    assert advantages([1.0, 0.0, 0.0, 0.0]) == [0.75, -0.25, -0.25, -0.25]


def test_step_loss():
    model, cpu = tiny(), torch.device("cpu")
    ids, start = torch.randint(0, 50, (12,)).tolist(), 5
    old = old_logprobs(model, [(ids, start, None)], cpu)[0]

    # positive advantage, on-policy (ratio = 1): one SGD step raises the sample's log-prob
    clip, n = step_loss(model, [(ids, start, 1.0, old)], cpu)
    assert clip == 0 and n == len(ids) - start
    with torch.no_grad():
        for p in model.parameters():
            p -= 0.5 * p.grad
    model.zero_grad()
    assert sum(old_logprobs(model, [(ids, start, None)], cpu)[0]) > sum(old)

    # ratio already far above 1 + eps_high with a positive advantage: clipped, so no gradient at all
    clip, _ = step_loss(model, [(ids, start, 1.0, [x - 5 for x in old])], cpu)
    assert clip == 1.0
    assert all(p.grad is None or p.grad.abs().max() == 0 for p in model.parameters())
