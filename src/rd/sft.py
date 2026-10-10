import torch
import torch.nn.functional as F
import argparse
import random
import json
import time
import math
from pathlib import Path
from transformers import AutoModelForCausalLM, AutoTokenizer
from rd.data import PROMPT, read_jsonl

def encode(tok, problem, response, max_len):
    prompt=tok.apply_chat_template([{"role":"user","content":PROMPT.format(problem=problem)}],tokenize=False,add_generation_prompt=True,enable_thinking=True)
    p=tok(prompt,add_special_tokens=False)["input_ids"]
    r=tok(response+"<|im_end|>",add_special_tokens=False)["input_ids"]
    if len(p)+len(r)>max_len:
        return None
    
    return p+r, [-100]*len(p)+r    

def step_loss(model, seqs, device):
    n_tok=  sum(sum(l!=-100 for l in labels[1:]) for _, labels in seqs)
    total=0.0
    for ids, labels in seqs:
        ids=torch.tensor([ids],device=device)
        labels=torch.tensor([labels],device=device)
        with torch.autocast(device.type, dtype=torch.bfloat16):
            logits=model(input_ids=ids).logits
        loss=F.cross_entropy(logits[0,:-1].float(),labels[0,1:],ignore_index=-100,reduction="sum")/n_tok
        loss.backward()
        total+=loss.item()
    return total, n_tok

def init_special_tokens(model, tok, names=("<think>", "</think>", "<|im_end|>")):
    """Qwen3-Base ships these as identical, untrained rows (cos = 1.0) and ties input/output embeddings,
    so it cannot tell them apart when predicting. Re-init each as the mean embedding of its plain-text spelling."""
    E = model.get_input_embeddings().weight
    with torch.no_grad():
        for name in names:
            # split off the first char so the added-token matcher can't fire: "<think>" -> "<", "think", ">"
            pieces = tok(name[0], add_special_tokens=False)["input_ids"] + tok(name[1:], add_special_tokens=False)["input_ids"]
            E[tok.convert_tokens_to_ids(name)] = E[pieces].mean(0)

def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    ap=argparse.ArgumentParser()
    ap.add_argument("--data",required=True,help="data/cnditions/<name>.jsonl")
    ap.add_argument("--out",required=True,help="runs/<name>")
    ap.add_argument("--model",default="Qwen/Qwen3-1.7B-Base")
    ap.add_argument("--epochs",type=int,default=3)
    ap.add_argument("--lr",type=float,default=1e-5)
    ap.add_argument("--seqs-per-step",type=int,default=32)
    ap.add_argument("--max-len",type=int,default=16384)
    ap.add_argument("--warmup",type=float,default=0.03,help="fraction of total steps")
    ap.add_argument("--limit",type=int,help="first N examples only")
    ap.add_argument("--seed",type=int,default=0)
    args=ap.parse_args()
    
    random.seed(args.seed)
    
    tok=AutoTokenizer.from_pretrained(args.model)
    rows=read_jsonl(args.data)[:args.limit]
    data=[e for r in rows if (e:=encode(tok,r["problem"],r["response"],args.max_len))]
    model=AutoModelForCausalLM.from_pretrained(args.model,dtype=torch.float32).to(device)
    init_special_tokens(model,tok)
    model.gradient_checkpointing_enable()
    model.config.use_cache=False
    model.train()
    opt=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=0.0)
    
    total_steps=math.ceil(len(data)/args.seqs_per_step)*args.epochs
    warmup=max(1,int(args.warmup*total_steps))
    
    def lr_at(step):
        if step<warmup:
            return args.lr*(step+1)/warmup
        return args.lr*0.5*(1+math.cos(math.pi*(step-warmup)/max(1,total_steps-warmup)))
    
    out=Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    log=open(out/"train_log.jsonl","w")
    step,t0=0,time.time()
    
    for epoch in range(args.epochs):
        random.shuffle(data)
        for i in range(0,len(data),args.seqs_per_step):
            lr=lr_at(step)
            for g in opt.param_groups:
                g["lr"]=lr
            loss,n_tok=step_loss(model,data[i:i+args.seqs_per_step],device)
            if not math.isfinite(loss):
                raise RuntimeError(f"non-finite loss {loss} at step {step}")
            gnorm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.0).item()
            opt.step()
            opt.zero_grad()
            step+=1
            rec=dict(step=step,epoch=epoch,loss=loss,lr=lr,gnorm=gnorm,n_tok=n_tok,sec=round(time.time()-t0))
            log.write(json.dumps(rec)+"\n")
            log.flush()
            if step%10==0 or step==total_steps:
                print(f"step {step}/{total_steps} loss {loss:.4f}",flush=True)
                
    model.generation_config.eos_token_id=[tok.convert_tokens_to_ids("<|im_end|>"),tok.eos_token_id]
    model.to(torch.bfloat16).save_pretrained(out)
    tok.save_pretrained(out)
    
if __name__=="__main__":
    main()
    