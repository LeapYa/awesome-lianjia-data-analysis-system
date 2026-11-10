"""
B2：六选一分类（直接预测正确排列）

任务定义
--------
    输入：一组 3 个字（如 决、定、性）
    候选：这 3 个字的全部排列（6 种，含重复字时 3 种）
    输出：哪个排列是正确的中文词

和 A（语言模型）的区别
---------------------
    A:  P(字1) × P(字2|字1) × P(字3|字1,字2)  → 各自打分，事后排序
    B2: softmax(6 个排列的分数)              → 直接学「选哪个」

B2 更贴合任务，因为候选集是封闭的、恰好 6 个。

关键设计
--------
1. **位置无关**：6 个候选的输入顺序随机打乱，标签是正确排列的索引
   否则模型可能学到「候选 0 通常是答案」

2. **共享编码器**：6 个候选共用一个 CharLM backbone，
   每个候选独立编码后取最后位置的隐状态作为表示

3. **分类头**：6 个表示 → 6 个 logits → 交叉熵

4. **数据划分**：用 build_split.py 的固定划分（按词，无泄漏）

用法
----
    python train_b2.py --steps 3000
    python eval_b2.py --model runs_b2/best.pt
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from model import BOS, PAD, CharLM, Config  # noqa: E402


def load_official(path: str, cfg: Config) -> CharLM:
    """从官方 safetensors 加载（INT8 反量化）。"""
    with open(path, "rb") as f:
        n = int.from_bytes(f.read(8), "little")
        hdr = json.loads(f.read(n).decode("utf-8"))
        blob = f.read()
    meta = hdr.pop("__metadata__", {}) or {}
    tensors = {}
    for name, info in hdr.items():
        s, e = info["data_offsets"]
        raw = blob[s:e]
        dt = info["dtype"]
        shape = info["shape"]
        if dt == "I8":
            arr = torch.frombuffer(bytearray(raw), dtype=torch.int8).reshape(shape)
        elif dt == "F32":
            arr = torch.frombuffer(bytearray(raw), dtype=torch.float32).reshape(shape)
        elif dt == "F16":
            arr = torch.frombuffer(bytearray(raw), dtype=torch.float16).reshape(shape).float()
        else:
            continue
        tensors[name] = arr
    w = {}
    for name, arr in tensors.items():
        if name.endswith(".scale"):
            continue
        sc = tensors.get(name + ".scale")
        w[name] = arr.float() * sc[:, None] if arr.dtype == torch.int8 and sc is not None \
            else arr.float()

    model = CharLM(cfg)
    sd = model.state_dict()
    loaded = 0
    with torch.no_grad():
        for k, v in w.items():
            if k in sd and sd[k].shape == v.shape:
                sd[k].copy_(v)
                loaded += 1
    model.load_state_dict(sd)
    print(f"  加载官方权重 {loaded}/{len(sd)}")
    return model


class Ranker(nn.Module):
    """六选一分类器：共享编码器 + 分类头。

    对每个候选编码，取最后有效位置的隐状态，再过线性头得到分数。
    """

    def __init__(self, cfg: Config, backbone: CharLM | None = None):
        super().__init__()
        self.cfg = cfg
        self.backbone = backbone if backbone is not None else CharLM(cfg)
        self.head = nn.Sequential(
            nn.LayerNorm(cfg.n_embd),
            nn.Linear(cfg.n_embd, cfg.n_embd),
            nn.GELU(),
            nn.Linear(cfg.n_embd, 1),
        )

    def forward(self, x: torch.Tensor, lens: torch.Tensor) -> torch.Tensor:
        """x: (B, N, T)  B=batch, N=候选数, T=长度
        lens: (B, N) 每个候选的有效长度

        返回 (B, N) 的分数。
        """
        B, N, T = x.shape
        flat = x.reshape(B * N, T)
        # 共享编码器
        idx = flat
        pos = torch.arange(T, device=flat.device)
        h = self.backbone.drop(self.backbone.tok(idx) + self.backbone.pos(pos))
        for block in self.backbone.blocks:
            h = block(h)
        h = self.backbone.ln_f(h)
        # 取每个候选最后有效位置的隐状态
        flat_lens = lens.reshape(B * N)
        last = (flat_lens - 1).clamp(min=0)
        rep = h[torch.arange(B * N, device=h.device), last]
        scores = self.head(rep).reshape(B, N)
        return scores


def encode_word(s: str, tok2id: dict[str, int], ctx: int) -> list[int]:
    ids = [BOS]
    for ch in s:
        ids.append(tok2id.get(ch, 1))   # UNK=1
    return ids[:ctx]


def build_batch(words: list[str], cands_map: dict, tok2id: dict,
                ctx: int, rng: random.Random):
    """构造一个 batch：每个词 6 个候选，顺序随机打乱。"""
    B = len(words)
    max_n = max(len(cands_map[w]) for w in words)
    max_t = 0
    for w in words:
        for c in cands_map[w]:
            max_t = max(max_t, len(c) + 1)
    max_t = min(max_t, ctx)

    X = torch.full((B, max_n, max_t), PAD, dtype=torch.long)
    L = torch.ones((B, max_n), dtype=torch.long)
    Y = torch.zeros(B, dtype=torch.long)

    orders = []
    for b, w in enumerate(words):
        cands = cands_map[w][:]
        rng.shuffle(cands)
        orders.append(cands)
        for j, c in enumerate(cands):
            ids = encode_word(c, tok2id, ctx)
            X[b, j, :len(ids)] = torch.tensor(ids)
            L[b, j] = len(ids)
            if c == w:
                Y[b] = j
    return X, L, Y, orders


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--official", default=str(
        ROOT / "models" / "ime_lm" / "sentence-model.safetensors"))
    ap.add_argument("--split", default=str(HERE / "split_words.json"))
    ap.add_argument("--out", default=str(HERE / "runs_b2"))
    ap.add_argument("--steps", type=int, default=3000)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--warmup", type=int, default=100)
    ap.add_argument("--eval-every", type=int, default=250)
    ap.add_argument("--freeze-backbone", action="store_true",
                    help="冻结 backbone，只训分类头")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    random.seed(args.seed)
    torch.set_num_threads(6)

    # 词表 + 配置（用官方的）
    with open(args.official, "rb") as f:
        n = int.from_bytes(f.read(8), "little")
        hdr = json.loads(f.read(n).decode("utf-8"))
    meta = hdr["__metadata__"]
    tokens_list = json.loads(meta["vocab"])
    tok2id = {c: i for i, c in enumerate(tokens_list)}
    cfg_json = json.loads(meta["config"])
    cfg = Config(vocab=cfg_json["vocab"], n_layer=cfg_json["n_layer"],
                 n_head=cfg_json["n_head"], n_embd=cfg_json["n_embd"],
                 context=cfg_json["context"], dropout=cfg_json["dropout"])
    print(f"词表 {len(tokens_list)}, 配置 {cfg.n_layer}层 {cfg.n_embd}维")

    # 划分：读 order_dataset（按字符集划分，无泄漏）
    ds_dir = HERE / "order_dataset"
    def load_ds(name):
        p = ds_dir / f"{name}.jsonl"
        recs = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        # 转成 {代表词: 候选列表}；歧义的跳过（训练时只学单解）
        m = {}
        skipped = 0
        for r in recs:
            if r.get("ambiguous"):
                skipped += 1
                continue
            if len(r["answers"]) != 1:
                skipped += 1
                continue
            m[r["answers"][0]] = r["cands"]
        return m, skipped

    tr_map, sk_tr = load_ds("train")
    va_map, sk_va = load_ds("val")
    print(f"数据集: 训练 {len(tr_map)}（跳过歧义 {sk_tr}）/ "
          f"验证 {len(va_map)}（跳过歧义 {sk_va}）")

    # 模型
    print("加载官方权重:")
    backbone = load_official(args.official, cfg)
    model = Ranker(cfg, backbone)
    if args.freeze_backbone:
        for p in model.backbone.parameters():
            p.requires_grad = False
        print("  已冻结 backbone")

    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
                            lr=args.lr, betas=(0.9, 0.95), weight_decay=0.05)
    rng = random.Random(args.seed)
    train_list = sorted(tr_map)
    val_list = sorted(va_map)

    os.makedirs(args.out, exist_ok=True)
    best = 0.0
    t0 = time.monotonic()

    for step in range(args.steps):
        if step < args.warmup:
            lr = args.lr * (step + 1) / args.warmup
        else:
            prog = (step - args.warmup) / max(1, args.steps - args.warmup)
            lr = args.lr * 0.5 * (1 + math.cos(math.pi * prog))
        for g in opt.param_groups:
            g["lr"] = lr

        batch_words = [rng.choice(train_list) for _ in range(args.batch)]
        X, L, Y, _ = build_batch(batch_words, tr_map, tok2id, cfg.context, rng)
        scores = model(X, L)
        loss = F.cross_entropy(scores, Y)

        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(
            [p for p in model.parameters() if p.requires_grad], 1.0)
        opt.step()

        if (step + 1) % args.eval_every == 0 or step + 1 == args.steps:
            model.eval()
            with torch.no_grad():
                # 验证：Top-1 / Top-3 / MRR
                top1 = top3 = 0
                mrr = 0.0
                nb = 0
                for i in range(0, len(val_list), 16):
                    ws = val_list[i:i + 16]
                    Xv, Lv, Yv, _ = build_batch(ws, va_map, tok2id, cfg.context,
                                                random.Random(999))
                    sc = model(Xv, Lv)
                    rank = (sc > sc.gather(1, Yv[:, None])).sum(1) + 1
                    top1 += int((rank == 1).sum())
                    top3 += int((rank <= 3).sum())
                    mrr += float((1.0 / rank.float()).sum())
                    nb += len(ws)
                top1 /= nb
                top3 /= nb
                mrr /= nb
            model.train()
            el = (time.monotonic() - t0) / 60
            line = (f"step {step + 1:>5}/{args.steps}  loss {loss.item():.4f}  "
                    f"top1 {top1 * 100:.1f}%  top3 {top3 * 100:.1f}%  "
                    f"mrr {mrr:.3f}  {el:.1f}min")
            print(line, flush=True)
            with open(os.path.join(args.out, "progress.log"), "a",
                      encoding="utf-8") as lg:
                lg.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
            if top1 > best:
                best = top1
                torch.save({"model": model.state_dict(), "cfg": cfg_json,
                            "tokens": tokens_list, "top1": top1,
                            "freeze_backbone": args.freeze_backbone},
                           os.path.join(args.out, "best.pt"))
                print(f"  -> 保存 best (top1 {top1 * 100:.1f}%)", flush=True)

    print(f"\n最佳 Top-1 {best * 100:.1f}%")
    print(f"权重: {os.path.join(args.out, 'best.pt')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
