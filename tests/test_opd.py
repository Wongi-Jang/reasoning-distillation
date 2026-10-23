import torch
from transformers import Qwen3Config, Qwen3ForCausalLM

from rd.opd import step_loss


def test_step_loss():
    torch.manual_seed(0)
    model = Qwen3ForCausalLM(Qwen3Config(vocab_size=50, hidden_size=32, intermediate_size=64, num_hidden_layers=2,
                                         num_attention_heads=4, num_key_value_heads=2, head_dim=8))
    ids, start = torch.randint(0, 50, (12,)).tolist(), 5
    def own():  # independent per-position reference, same bf16 autocast as step_loss
        with torch.autocast("cpu", dtype=torch.bfloat16):
            logits = model(input_ids=torch.tensor([ids])).logits[0].float()
        return [torch.log_softmax(logits[i - 1], -1)[ids[i]].item() for i in range(start, len(ids))]
    s_lp = own()

    # teacher agrees with the student on every sampled token -> zero KL, zero gradient (checks the logit alignment)
    kl, n = step_loss(model, [(ids, start, s_lp)], torch.device("cpu"))
    assert abs(kl) < 1e-4 and n == len(ids) - start
    assert all(p.grad is None or p.grad.abs().max() < 1e-6 for p in model.parameters())

    # teacher dislikes the first response token -> one SGD step lowers the student's log-prob of it
    model.zero_grad()
    step_loss(model, [(ids, start, [s_lp[0] - 3] + s_lp[1:])], torch.device("cpu"))
    with torch.no_grad():
        for p in model.parameters():
            p -= 0.1 * p.grad
    assert own()[0] < s_lp[0]
