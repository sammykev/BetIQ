"""
Upload an assembled Space (build-space.sh) to Hugging Face. Creates the
Space on the first run. Needs HF_TOKEN (a write token) and HF_SPACE
("username/space-name").

    python deploy/huggingface/upload.py OUT_DIR
"""

import os
import sys

from huggingface_hub import HfApi

folder = sys.argv[1]
repo = os.environ["HF_SPACE"].strip()
api = HfApi(token=os.environ["HF_TOKEN"])

api.create_repo(repo, repo_type="space", space_sdk="docker", exist_ok=True)
api.upload_folder(
    folder_path=folder,
    repo_id=repo,
    repo_type="space",
    # Files removed from the backend are removed from the Space too
    delete_patterns=["backend/**", "*.csv", "Dockerfile", "README.md"],
    commit_message=f"Deploy {os.getenv('GITHUB_SHA', 'local')[:7]}",
)
owner, name = repo.split("/", 1)
print(f"Uploaded. The Space rebuilds now: https://huggingface.co/spaces/{repo}")
print(f"API address: https://{owner}-{name}.hf.space".lower().replace("_", "-"))
