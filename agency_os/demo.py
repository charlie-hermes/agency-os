"""Print a compact fictional vertical-slice receipt."""

from __future__ import annotations

import json

from .workflow import run_fictional_article


def main() -> None:
    result = run_fictional_article()
    receipt = result.records["receipt"]
    print(
        json.dumps(
            {
                "brand_id": receipt["brand_id"],
                "state": receipt["state"],
                "destination": receipt["destination_ref"],
                "manifest_checksum": receipt["manifest_checksum"],
                "external_url": receipt["external_url"],
                "external_calls": result.publisher.calls,
                "real_external_writes": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
