"""
Train the prediction model and publish it for the API server
(model_store.py). Runs nightly on GitHub Actions, where training takes a
minute or two instead of many minutes on the API server's small CPU.

    python train_model.py            # needs UPSTASH_REDIS_URL

Uses exactly the pipeline's data: football-data.co.uk league CSVs (synced
first), the legacy EPL/UCL CSVs and the international results. Then the
European competitions model, when its check adopted one (europe_model.py).
"""

import asyncio
import os
import sys
import time


def main() -> int:
    if not os.getenv("UPSTASH_REDIS_URL"):
        print("UPSTASH_REDIS_URL is not set: nowhere to publish the model. Skipping.")
        return 0

    import main as app
    import model_store
    from predictor import MODEL_CACHE_VERSION

    t0 = time.time()
    asyncio.run(app._sync_football_data(force=True))
    assembled = app._assemble_training_data()
    if assembled is None:
        print("No training data.")
        return 1
    _, combined, _ = assembled

    predictor = app._train_new(combined)
    blob = predictor.to_bytes()
    meta = model_store.publish(blob, MODEL_CACHE_VERSION, {
        "source": "github-actions", "rows": len(combined),
        "commit": os.getenv("GITHUB_SHA", "")[:7],
    })
    print(f"Published model v{MODEL_CACHE_VERSION}: {meta['size'] / 1e6:.1f} MB in "
          f"{meta['chunks']} chunk(s), {len(combined)} matches, {time.time() - t0:.0f}s total.")

    # The European competitions model, when its weekly check adopted one
    # (europe_model.py), so the server always has a fresh copy
    import europe_model
    verdict = europe_model.load_check(model_store._client())
    if verdict.get("adopted"):
        europe_model.train_and_publish(app, verdict["adopted"])
    else:
        print(f"Europe model: none adopted ({verdict.get('reason') or 'no check yet'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
