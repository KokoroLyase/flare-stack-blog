#!/usr/bin/env python3
"""Read-only runtime readiness check; prints and stores summaries only."""
import json
from datetime import datetime, timezone
from pathlib import Path
from runtime import Client, OperationError

ROOT = Path(__file__).resolve().parents[2] / "tmp/isomeria-publisher"


def main():
    client = Client()  # Cannot issue writes in this mode.
    ROOT.mkdir(parents=True, exist_ok=True)
    try:
        _, spec = client.get("/api/spec.json")
        if spec.get("servers", [{}])[0].get("url") != "https://blog.isomeria.net/api":
            raise OperationError("Unexpected OpenAPI server; stopped")
        required = [
            ("get", "/admin/posts"), ("post", "/admin/posts"),
            ("get", "/admin/posts/{id}"), ("patch", "/admin/posts/{id}"),
            ("get", "/admin/categories"), ("get", "/admin/tags"),
            ("get", "/admin/posts/{postId}/tags"),
            ("put", "/admin/posts/{postId}/tags"),
            ("post", "/admin/posts/{id}/publish"),
        ]
        for method, path in required:
            if method not in spec.get("paths", {}).get(path, {}):
                raise OperationError("Required OpenAPI operation is missing")
        create = spec["paths"]["/admin/posts"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        if not {"title", "summary", "contentJson"} <= set(create["properties"]["data"]["properties"]):
            raise OperationError("Unexpected draft creation schema")
        (ROOT / "openapi.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        _, listing = client.get("/api/admin/posts", query={"limit": 1, "offset": 0, "includeContent": "false"})
        if not isinstance(listing, dict) or not isinstance(listing.get("items"), list) or len(listing["items"]) > 1:
            raise OperationError("Unexpected admin list response")
        post_id = None
        detail_checked = False
        if listing["items"]:
            post_id = listing["items"][0]["id"]
            post = client.read_post(post_id)
            if not {"contentJson", "hasPublicSnapshot", "updatedAt"} <= set(post):
                raise OperationError("Required editable draft fields are absent")
            detail_checked = True
            _, tags = client.get(f"/api/admin/posts/{post_id}/tags")
            if not isinstance(tags, list):
                raise OperationError("Unexpected post tag list")
        for route in ("/api/admin/categories", "/api/admin/tags"):
            _, collection = client.get(route)
            if not (isinstance(collection, list) or isinstance(collection, dict) and isinstance(collection.get("items"), list)):
                raise OperationError("Unexpected taxonomy list response")
        summary = {
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "credential_present_nonempty": True,
            "all_required_operations_defined": True,
            "post_id_read": post_id,
            "detail_read_back_checked": detail_checked,
            "requests": client.events,
            "online_writes": 0,
            "write_operations_verified_by_execution": False,
        }
        (ROOT / "readiness.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary))
    except OperationError as exc:
        print(json.dumps({"error": str(exc), "requests": client.events, "online_writes": 0}))
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except OperationError as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(1)
