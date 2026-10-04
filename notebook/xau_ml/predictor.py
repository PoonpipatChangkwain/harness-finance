"""Save / load a bull+bear model pair as a deterministic predictor (F1: code computes,
the LLM only reads the result).

Artifacts per (horizon, label set) in ``models/`` (v0.1) or ``models/v2/``:
  boosters as LightGBM text files (no pickle) — ``<stem>.bull.txt``/``.bear.txt`` for a
  pair, ``<stem>.multi.txt`` for the 3-class model; a logistic model lives in the JSON;
  ``<stem>.json`` — features (ordered), timeframe, label params, splits, τ/m, calibrators
  as numbers, metrics, version, file hashes, ``model.kind`` and ``extras``.
A backend loader needs ``lightgbm`` + ``numpy`` + the feature code in ``features.py``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from xau_ml import features as F
from xau_ml.model import Calibrator
from xau_ml.sim import signals


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _write_booster(model_dir: Path, name: str, booster: lgb.Booster) -> dict:
    p = model_dir / name
    booster.save_model(str(p))
    return {"file": p.name, "sha256_16": _sha(p)}


def save(model_dir: Path, stem: str, boosters: dict[str, lgb.Booster], cals: dict[str, Calibrator], meta: dict,
         kind: str = "lgbm_pair", logit: dict | None = None) -> Path:
    """``kind``: ``lgbm_pair`` (boosters bull/bear), ``lgbm3`` (boosters {"multi": b}),
    ``logit_pair`` (``logit`` = per-side params, no files)."""
    model_dir.mkdir(parents=True, exist_ok=True)
    files = {k: _write_booster(model_dir, f"{stem}.{k}.txt", b) for k, b in boosters.items()}
    meta = {**meta, "files": files, "calibrators": {s: c.to_json() for s, c in cals.items()}}
    meta.setdefault("model", {})["kind"] = kind
    if logit is not None:
        meta["model"]["logit"] = logit
    out = model_dir / f"{stem}.json"
    out.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return out


@dataclass
class Predictor:
    meta: dict
    boosters: dict[str, lgb.Booster]
    cals: dict[str, Calibrator]
    kind: str = "lgbm_pair"
    logit: dict = field(default_factory=dict)

    @classmethod
    def load(cls, meta_path: Path) -> Predictor:
        meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        boosters = {}
        for k, f in meta["files"].items():
            path = Path(meta_path).parent / f["file"]
            assert _sha(path) == f["sha256_16"], f"{path.name} changed since it was saved"
            boosters[k] = lgb.Booster(model_file=str(path))
        cals = {s: Calibrator(**c) for s, c in meta["calibrators"].items()}
        model = meta.get("model", {})
        return cls(meta, boosters, cals, model.get("kind", "lgbm_pair"), model.get("logit", {}))

    @property
    def features(self) -> list[str]:
        return self.meta["features"]

    def raw(self, X: pd.DataFrame) -> dict[str, np.ndarray]:
        X = X[self.features]
        if self.kind == "lgbm_pair":
            return {s: self.boosters[s].predict(X) for s in ("bull", "bear")}
        if self.kind == "lgbm3":
            p = self.boosters["multi"].predict(X)
            return {"bull": p[:, 1], "bear": p[:, 2]}
        if self.kind == "logit_pair":
            from xau_ml.v2 import LogitPair

            return {s: LogitPair.proba(self.logit[s], X) for s in ("bull", "bear")}
        raise ValueError(self.kind)

    def proba(self, X: pd.DataFrame) -> pd.DataFrame:
        r = self.raw(X)
        return pd.DataFrame({f"p_{s}": self.cals[s](r[s]) for s in ("bull", "bear")}, index=X.index)

    def decide(self, X: pd.DataFrame) -> pd.DataFrame:
        """Probabilities + the τ/m rule → side per row (+1 long, −1 short, 0 flat)."""
        p = self.proba(X)
        rule = self.meta["signal_rule"]
        p["signal"] = signals(p["p_bull"].to_numpy(), p["p_bear"].to_numpy(), rule["tau"], rule["m"])
        return p

    def from_bars(self, main: pd.DataFrame, htfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Raw bars (``date`` = open time, ``t_close`` = close time) → decision per bar."""
        frame = F.build(main, self.meta["timeframe"], htfs)
        if self.meta.get("extras"):
            frame = F.add_extras(frame, htfs["1d"])
        return pd.concat([frame[["date"]], self.decide(frame)], axis=1)


def assert_roundtrip(pred: Predictor, X: pd.DataFrame, expected: dict[str, np.ndarray]) -> None:
    got = pred.proba(X)
    for s in ("bull", "bear"):
        np.testing.assert_allclose(got[f"p_{s}"].to_numpy(), expected[s], rtol=0, atol=1e-12)
