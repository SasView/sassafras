"""Runs the ml-sans network in a child process for the Guess button.

PyTorch is imported only here, never in the sassafras server process: on macOS,
torch and numpy/sasmodels can each bring their own OpenMP runtime, and loading
both aborts the whole process. A crash here costs one Guess, not the server.

Reads a JSON request on stdin, writes a JSON reply on stdout.
"""
import json
import sys


def main():
    req = json.load(sys.stdin)
    sys.path.insert(0, req["folder"])
    try:
        import numpy as np
        from sans_ml.predict import Suggester
    except Exception as exc:
        json.dump({"error": f"Could not import the ML model code: {type(exc).__name__}: {exc}"}, sys.stdout)
        return
    try:
        sug = Suggester(req["model_path"])
    except Exception as exc:
        json.dump({"error": f"Could not load {req['model_path']}: {type(exc).__name__}: {exc}"}, sys.stdout)
        return
    di = None if req.get("di") is None else np.asarray(req["di"], float)
    try:
        ranked = sug.suggest(np.asarray(req["q"], float), np.asarray(req["i"], float), di, top=int(req["top"]))
    except ValueError as exc:
        json.dump({"error": f"The ML model can't use this curve: {exc}"}, sys.stdout)
        return
    out = [{"model": r["model"], "probability": float(r["probability"]),
            "forms": list(r["forms"]), "structure": r["structure"]} for r in ranked]
    json.dump({"ranked": out}, sys.stdout)


if __name__ == "__main__":
    main()
