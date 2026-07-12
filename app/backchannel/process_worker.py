from __future__ import annotations

from multiprocessing.connection import Connection
from pathlib import Path


def run_hybrid_classifier_worker(base_dir: str, commands: Connection, results: Connection) -> None:
    try:
        from app.backchannel.hybrid_classifier import HybridBackchannelClassifier

        classifier = HybridBackchannelClassifier.from_model_cache(Path(base_dir), process_isolated=False)
        classifier.preload()
        results.send(("ready", None, None))
        while True:
            message = commands.recv()
            if not isinstance(message, tuple) or len(message) != 2:
                continue
            token, text = message
            if token is None:
                return
            try:
                label = classifier.classify(str(text))
                results.send(("result", int(token), label))
            except Exception as exc:  # noqa: BLE001
                results.send(("error", int(token), str(exc)))
    except EOFError:
        return
    except Exception as exc:  # noqa: BLE001
        try:
            results.send(("startup_error", None, str(exc)))
        except Exception:
            pass
    finally:
        commands.close()
        results.close()
