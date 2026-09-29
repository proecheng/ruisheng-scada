"""Build an authenticated tools candidate while reusing the deployed images."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SOURCE = Path("C:/ProgramData/Ruisheng/publisher-build/address-correlation-20260907")
BASE = Path("C:/ProgramData/Ruisheng/publisher-output/deploy-20260907.1")
sys.path.insert(0, str(SOURCE))

from tools.release_artifacts import SubprocessRunner, build_candidate  # noqa: E402


def main() -> None:
    baseline = json.loads((BASE / "MANIFEST.json").read_text(encoding="utf-8"))
    if baseline["source_commit"] != "2150b5ee904760ce0af4483c201009b8744669a2":
        raise RuntimeError("Unexpected baseline source")
    images = {entry["component"]: entry["image_id"] for entry in baseline["images"]}
    expected = {
        "postgres": "sha256:50a2abfa8bad354f4bc1567c6edf7426586fd99ee8cd8982bbaee157a460c6b1",
        "redis": "sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf",
        "api": "sha256:33d948f176ddc7d47ec8840d6baff4892bb9b95014444590d3ae7f1dc2989ce3",
        "gw": "sha256:becb5435ecff9449f7fe9d39e655d0d8d33c7cb14629a33247ed0a0b29c420b6",
        "web": "sha256:15499a12b59107bbfa94fd270c44ba4f34e81194e0c7864ed88be8d710bc905b",
    }
    if images != expected:
        raise RuntimeError("Baseline images do not match the approved deployed images")
    result = build_candidate(
        root=SOURCE,
        output_root=BASE.parent,
        candidate_id="deploy-20260907.2",
        target_platform="linux/amd64",
        env_file=SOURCE / ".env.prod.example",
        postgres_source=images["postgres"],
        redis_source=images["redis"],
        runner=SubprocessRunner(),
        signing_identity=Path("C:/ProgramData/Ruisheng/publisher-secrets/ruisheng-release.pub"),
        trust_directory=Path("C:/ProgramData/Ruisheng/publisher-trust"),
        prebuilt_app_sources={component: images[component] for component in ("api", "gw", "web")},
        pull_base_images=False,
    )
    print(json.dumps({"candidate_path": str(result), "application_images_reused": True}))


if __name__ == "__main__":
    main()
