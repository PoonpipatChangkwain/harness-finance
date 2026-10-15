"""v0.3 deep-learning versions of experiment E3 (same 20 features + label-uniqueness weights).

E6  MLP   — feed-forward net on E3's tabular features.
E7  SEQ   — 1D-CNN + GRU over the last ``SEQ_LEN`` bars (open/high/low/close relative to the
            decision bar's close, in ATR) joined with the same tabular features.

Both have two sigmoid heads (long wins / short wins), are trained per walk-forward fold on
the fold's TRAIN rows only (median-impute + standardize fitted there), early-stop on the
purged time-ordered tail of those rows, and average ``N_SEEDS`` seeds. Volume is left out
of the sequence: broker tick volume and futures volume (the harness feed) don't compare.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import torch
from torch import nn

from xau_ml.config import Horizon
from xau_ml.cv import Fold, uniqueness

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
SIDES = ("bull", "bear")
SEQ_LEN = 64
N_SEEDS = 3


@dataclass(frozen=True)
class DLConfig:
    hidden: int = 128
    dropout: float = 0.2
    lr: float = 1e-3
    weight_decay: float = 1e-3
    batch: int = 512
    max_epochs: int = 40
    patience: int = 5
    inner_val_frac: float = 0.2
    conv: int = 32
    gru: int = 48


@dataclass(frozen=True)
class DLExperiment:
    id: str
    name: str
    kind: str  # "mlp" | "seq"
    cfg: DLConfig = field(default_factory=DLConfig)


DL_EXPERIMENTS = [
    DLExperiment("E6", "MLP on E3 features + weights", "mlp"),
    DLExperiment("E7", "1D-CNN + GRU (64 bars) + E3 features + weights", "seq"),
]


# --- inputs -------------------------------------------------------------------------------


def sequences(fr: pd.DataFrame, length: int = SEQ_LEN) -> np.ndarray:
    """(N, length, 4): bars t-length+1..t, each OHLC as (price − close_t) / ATR14_t.
    Only bars up to the decision bar ``t`` — causal by construction. NaN where incomplete."""
    o, h, lo, c = (fr[k].to_numpy("float64") for k in ("open", "high", "low", "close"))
    atr = fr["atr14"].to_numpy("float64")
    n = len(fr)
    out = np.full((n, length, 4), np.nan, dtype="float32")
    idx = np.arange(n)[:, None] - np.arange(length - 1, -1, -1)[None, :]  # oldest → newest
    valid = idx >= 0
    idx = np.clip(idx, 0, None)
    ref, scale = c[:, None], atr[:, None]
    for k, arr in enumerate((o, h, lo, c)):
        v = (arr[idx] - ref) / scale
        v[~valid] = np.nan
        out[:, :, k] = v
    return out


@dataclass
class Scaler:
    median: np.ndarray
    mean: np.ndarray
    std: np.ndarray

    @classmethod
    def fit(cls, X: np.ndarray) -> Scaler:
        med = np.nanmedian(X, axis=0)
        Xf = np.where(np.isnan(X), med, X)
        return cls(med, Xf.mean(axis=0), np.where(Xf.std(axis=0) > 0, Xf.std(axis=0), 1.0))

    def __call__(self, X: np.ndarray) -> np.ndarray:
        Xf = np.where(np.isnan(X), self.median, X)
        return np.clip((Xf - self.mean) / self.std, -5, 5).astype("float32")

    def to_json(self) -> dict:
        return {"median": self.median.tolist(), "mean": self.mean.tolist(), "std": self.std.tolist()}


# --- networks -----------------------------------------------------------------------------


class MLP(nn.Module):
    def __init__(self, n_in: int, cfg: DLConfig):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_in, cfg.hidden), nn.LayerNorm(cfg.hidden), nn.GELU(), nn.Dropout(cfg.dropout),
            nn.Linear(cfg.hidden, cfg.hidden // 2), nn.GELU(), nn.Dropout(cfg.dropout),
            nn.Linear(cfg.hidden // 2, 2),
        )

    def forward(self, x: torch.Tensor, seq: torch.Tensor | None = None) -> torch.Tensor:
        return self.net(x)


class SeqNet(nn.Module):
    def __init__(self, n_in: int, cfg: DLConfig):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(4, cfg.conv, 5, padding=2), nn.GELU(),
            nn.Conv1d(cfg.conv, cfg.conv, 5, padding=2), nn.GELU(),
        )
        self.gru = nn.GRU(cfg.conv, cfg.gru, batch_first=True)
        self.tab = nn.Sequential(nn.Linear(n_in, 64), nn.LayerNorm(64), nn.GELU(), nn.Dropout(cfg.dropout))
        self.head = nn.Sequential(nn.Linear(cfg.gru + 64, 64), nn.GELU(), nn.Dropout(cfg.dropout), nn.Linear(64, 2))

    def forward(self, x: torch.Tensor, seq: torch.Tensor | None = None) -> torch.Tensor:
        z = self.conv(seq.transpose(1, 2)).transpose(1, 2)  # (B, L, C)
        _, hN = self.gru(z)
        return self.head(torch.cat([hN[-1], self.tab(x)], dim=1))


# --- training -----------------------------------------------------------------------------


@dataclass
class FittedDL:
    exp: DLExperiment
    cols: list[str]
    scaler: Scaler
    nets: list[nn.Module]
    epochs: list[int]

    @torch.no_grad()
    def predict(self, fr: pd.DataFrame, rows: np.ndarray, seq_all: np.ndarray | None) -> dict[str, np.ndarray]:
        x = torch.from_numpy(self.scaler(fr[self.cols].to_numpy("float64")[rows])).to(DEVICE)
        s = None
        if self.exp.kind == "seq":
            s = torch.from_numpy(np.nan_to_num(np.clip(seq_all[rows], -20, 20) / 5.0)).to(DEVICE)
        probs = []
        for net in self.nets:
            net.eval()
            out = []
            for i in range(0, len(rows), 4096):
                out.append(torch.sigmoid(net(x[i : i + 4096], None if s is None else s[i : i + 4096])).cpu().numpy())
            probs.append(np.concatenate(out))
        p = np.mean(probs, axis=0).astype("float64")
        return {"bull": p[:, 0], "bear": p[:, 1]}


def _seed(seed: int) -> None:
    torch.manual_seed(seed)
    np.random.seed(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def fit_dl(exp: DLExperiment, fr: pd.DataFrame, rows: np.ndarray, cols: list[str], hz: Horizon,
           seq_all: np.ndarray | None, seeds: int = N_SEEDS) -> FittedDL:
    cfg = exp.cfg
    scaler = Scaler.fit(fr[cols].to_numpy("float64")[rows])
    X = scaler(fr[cols].to_numpy("float64")[rows])
    Y = np.stack([fr[f"y_{s}"].to_numpy()[rows] for s in SIDES], axis=1).astype("float32")
    W = np.stack([uniqueness(fr, rows, f"exit_{s}") for s in SIDES], axis=1).astype("float32")
    SQ = np.nan_to_num(np.clip(seq_all[rows], -20, 20) / 5.0).astype("float32") if exp.kind == "seq" else None
    n = len(rows)
    cut = int(n * (1 - cfg.inner_val_frac))
    tr_idx, va_idx = np.arange(0, max(cut - hz.time_stop - 1, 1)), np.arange(cut, n)  # purged inner split
    t = {k: torch.from_numpy(v).to(DEVICE) for k, v in (("x", X), ("y", Y), ("w", W))}
    if SQ is not None:
        t["s"] = torch.from_numpy(SQ).to(DEVICE)
    nets, epochs = [], []
    for seed in range(seeds):
        _seed(seed)
        net = (MLP if exp.kind == "mlp" else SeqNet)(len(cols), cfg).to(DEVICE)
        opt = torch.optim.AdamW(net.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
        loss_fn = nn.BCEWithLogitsLoss(reduction="none")
        best, best_state, best_ep, wait = np.inf, None, 0, 0
        g = torch.Generator(device="cpu").manual_seed(seed)
        for ep in range(cfg.max_epochs):
            net.train()
            perm = torch.from_numpy(tr_idx)[torch.randperm(len(tr_idx), generator=g)].to(DEVICE)
            for i in range(0, len(perm), cfg.batch):
                b = perm[i : i + cfg.batch]
                logits = net(t["x"][b], t["s"][b] if SQ is not None else None)
                loss = (loss_fn(logits, t["y"][b]) * t["w"][b]).mean()
                opt.zero_grad()
                loss.backward()
                opt.step()
            net.eval()
            with torch.no_grad():
                vb = torch.from_numpy(va_idx).to(DEVICE)
                vl = float((loss_fn(net(t["x"][vb], t["s"][vb] if SQ is not None else None), t["y"][vb]) * t["w"][vb]).mean())
            if vl < best - 1e-5:
                best, best_state, best_ep, wait = vl, {k: v.detach().clone() for k, v in net.state_dict().items()}, ep + 1, 0
            else:
                wait += 1
                if wait >= cfg.patience:
                    break
        net.load_state_dict(best_state)
        nets.append(net)
        epochs.append(best_ep)
    return FittedDL(exp, cols, scaler, nets, epochs)


def run_oof_dl(exp: DLExperiment, fr: pd.DataFrame, folds: list[Fold], cols: list[str], hz: Horizon,
               seq_all: np.ndarray | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Same output shape as ``v2.run_oof`` so nested trading / baselines are reused as-is."""
    from sklearn.metrics import log_loss, roc_auc_score

    parts, rows = [], []
    for f in folds:
        m = fit_dl(exp, fr, f.train, cols, hz, seq_all)
        p = m.predict(fr, f.test, seq_all)
        parts.append(pd.DataFrame({"pos": f.test, "fold": f.name, "p_bull_raw": p["bull"], "p_bear_raw": p["bear"]}))
        rec = {"fold": f.name, "train_rows": len(f.train), "test_rows": len(f.test), "epochs": m.epochs}
        for s in SIDES:
            y = fr[f"y_{s}"].to_numpy()[f.test]
            rec[f"auc_{s}"] = roc_auc_score(y, p[s]) if 0 < y.mean() < 1 else np.nan
            rec[f"logloss_{s}"] = log_loss(y, np.clip(p[s], 1e-6, 1 - 1e-6), labels=[0, 1])
            rec[f"logloss_clim_{s}"] = log_loss(y, np.full(len(y), fr[f"y_{s}"].to_numpy()[f.train].mean()), labels=[0, 1])
        rows.append(rec)
    return pd.concat(parts, ignore_index=True), pd.DataFrame(rows)


def save(model_dir, stem: str, fitted: FittedDL, meta: dict) -> None:
    """state_dicts (load with ``torch.load(weights_only=True)``) + scaler/arch in JSON."""
    import json

    model_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for i, net in enumerate(fitted.nets):
        p = model_dir / f"{stem}.seed{i}.pt"
        torch.save(net.state_dict(), p)
        files.append(p.name)
    meta = {**meta, "weights_files": files, "scaler": fitted.scaler.to_json(), "arch": {"kind": fitted.exp.kind, **fitted.exp.cfg.__dict__,
            "seq_len": SEQ_LEN, "seq_channels": ["open", "high", "low", "close"], "seq_norm": "(price - close_t) / ATR14_t, clip ±20, /5"}}
    (model_dir / f"{stem}.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
