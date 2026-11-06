"""1D residual CNN with one head each for form factor, S(Q) and component count."""
from __future__ import annotations

import torch
from torch import nn


class Block(nn.Module):
    def __init__(self, c_in, c_out, stride=1, dilation=1, k=7):
        super().__init__()
        pad = dilation * (k - 1) // 2
        self.net = nn.Sequential(
            nn.Conv1d(c_in, c_out, k, stride, pad, dilation, bias=False),
            nn.BatchNorm1d(c_out), nn.SiLU(),
            nn.Conv1d(c_out, c_out, k, 1, pad, dilation, bias=False),
            nn.BatchNorm1d(c_out),
        )
        self.skip = nn.Identity() if c_in == c_out and stride == 1 else nn.Sequential(
            nn.Conv1d(c_in, c_out, 1, stride, bias=False), nn.BatchNorm1d(c_out))
        self.act = nn.SiLU()

    def forward(self, x):
        return self.act(self.net(x) + self.skip(x))


class SuggestNet(nn.Module):
    def __init__(self, n_forms, n_structure, in_ch=4, width=32, dropout=0.1):
        super().__init__()
        w = width
        self.body = nn.Sequential(
            nn.Conv1d(in_ch, w, 7, 2, 3), nn.SiLU(),
            Block(w, w), Block(w, w, dilation=2),
            Block(w, 2 * w, stride=2), Block(2 * w, 2 * w, dilation=2),
            Block(2 * w, 3 * w, stride=2), Block(3 * w, 3 * w, dilation=2),
            Block(3 * w, 4 * w, stride=2), Block(4 * w, 4 * w),
        )
        c = 4 * w
        self.attn = nn.Conv1d(c, 1, 1)
        self.shared = nn.Sequential(nn.Linear(2 * c, 2 * c), nn.SiLU(), nn.Dropout(dropout))
        self.form = nn.Linear(2 * c, n_forms)
        self.structure = nn.Linear(2 * c, n_structure)
        self.mixture = nn.Linear(2 * c, 1)

    def forward(self, x):
        h = self.body(x)
        mask = torch.nn.functional.adaptive_max_pool1d(x[:, 2:3], h.shape[-1])
        a = self.attn(h).masked_fill(mask == 0, -1e4)
        pooled = (torch.softmax(a, -1) * h).sum(-1)
        mean = (h * mask).sum(-1) / mask.sum(-1).clamp(min=1)
        z = self.shared(torch.cat([pooled, mean], 1))
        return self.form(z), self.structure(z), self.mixture(z).squeeze(-1)


def losses(out, form_t, st_t, mix_t, weight):
    f, s, m = out
    lf = -(form_t * torch.log_softmax(f, 1)).sum(1)
    ls = torch.nn.functional.cross_entropy(s, st_t, reduction='none')
    lm = torch.nn.functional.binary_cross_entropy_with_logits(m, mix_t, reduction='none')
    return ((lf + 0.5 * ls + 0.5 * lm) * weight).mean()
