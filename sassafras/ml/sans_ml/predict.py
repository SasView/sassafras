"""example_data > trained model > suggested sasmodels model.

    python -m sans_ml.predict runs/m1/model.pt mydata.xml

    from sans_ml.predict import Suggester
    Suggester('runs/m1/model.pt').suggest(q, I, dI)
"""
from __future__ import annotations

import argparse
from itertools import combinations

import numpy as np
import torch

from .encode import encode
from .net import SuggestNet


class Suggester:
    def __init__(self, path, n_forms_considered=8):
        ck = torch.load(path, map_location='cpu', weights_only=False)
        v = ck['vocab']
        self.forms, self.structure = v['forms'], v['structure']
        self.net = SuggestNet(len(self.forms), len(self.structure), width=ck['width'])
        self.net.load_state_dict(ck['state'])
        self.net.eval()
        self.T = ck['temperature']
        self.no_sq = set(ck['no_structure_factor'])
        self.pool = set(ck['mixture_pool'])
        self.k = n_forms_considered

    @torch.no_grad()
    def probabilities(self, X):
        f, s, m = self.net(torch.as_tensor(X))
        return (torch.softmax(f / self.T, 1).numpy(), torch.softmax(s, 1).numpy(),
                torch.sigmoid(m).numpy())

    def rank(self, pf, ps, pm, top=5):
        """Combine the three heads into ranked sasmodels model strings."""
        idx = np.argsort(pf)[::-1][: self.k]
        cands = []
        for i in idx:
            f = self.forms[i]
            cands.append((f, (1 - pm) * pf[i] * ps[0], (f,), None))
            if f not in self.no_sq:
                for j, s in enumerate(self.structure[1:], 1):
                    cands.append((f'{f}@{s}', (1 - pm) * pf[i] * ps[j], (f,), s))
        for a, b in combinations(idx, 2):     # idx is sorted, so a is the likelier form
            fa, fb = self.forms[a], self.forms[b]
            if fa not in self.pool or fb not in self.pool:
                continue
            p = pm * 4 * pf[a] * pf[b]
            cands.append((f'{fa}+{fb}', p * ps[0], (fa, fb), None))
            if fa not in self.no_sq:
                for j, s in enumerate(self.structure[1:], 1):
                    cands.append((f'{fa}@{s}+{fb}', p * ps[j], (fa, fb), s))
        total = sum(c[1] for c in cands)
        cands.sort(key=lambda c: -c[1])
        return [dict(model=c[0], probability=float(c[1] / total), forms=c[2], structure=c[3])
                for c in cands[:top]]

    def suggest(self, q, I, dI=None, top=5):
        x = encode(q, I, dI)[None]
        pf, ps, pm = self.probabilities(x)
        return self.rank(pf[0], ps[0], float(pm[0]), top)

    def suggest_data(self, data, top=5):
        """Accepts an IQuana/sasdata Data1D."""
        return self.suggest(data.x, data.y, getattr(data, 'dy', None), top)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument('model')
    ap.add_argument('data', help='anything iquana can load (csv, xml, h5), or an example name')
    ap.add_argument('--top', type=int, default=5)
    a = ap.parse_args()
    from iquana import examples
    data = examples.load(a.data) if a.data in examples.list_examples() else None
    if data is None:
        from iquana.examples import load_sans_data
        data = load_sans_data(a.data)
    for r in Suggester(a.model).suggest_data(data, a.top):
        print(f"{r['probability']:6.1%}  {r['model']}")


if __name__ == '__main__':
    main()
