#!/usr/bin/env python3
"""Repository adapter for the existing Flare Stack Blog client.

No credentials are stored here. Use python3 -B to avoid repository bytecode.
Network-enabled exec_command permission must be requested by the calling agent.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sys
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True
ORIGIN = "https://blog.isomeria.net"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / ".agents/skills/flare-stack-blog/scripts"


class OperationError(RuntimeError):
    """Safe error: contains no response body, credentials or transport headers."""


def environment_config():
    key = os.environ.get("FSB_ADMIN_API_KEY")
    if not key or not key.strip():
        raise OperationError("FSB_ADMIN_API_KEY is missing or empty; no fallback")
    return {"url": ORIGIN, "api_key": key}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _deadline(*_):
    raise TimeoutError("request deadline exceeded")


class Client:
    """Default is read-only. Enable writes only for an authorized future task.

    This adapter is synchronous and intended for the Linux main Python thread.
    Publication requires its own explicit authorization, separate from draft work.
    """
    def __init__(self, *, draft_writes=False, publish_authorized=False, publish_post_id=None):
        environment_config()  # Fail before importing or performing any HTTP call.
        self.draft_writes = draft_writes
        self.publish_authorized = publish_authorized
        self.publish_post_id = None if publish_post_id is None else self._post_id(publish_post_id)
        self.events = []
        spec = importlib.util.spec_from_file_location("isomeria_fsb", SCRIPTS / "fsb.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.read_config = environment_config
        self.codec = module.tiptap
        self._opener = urllib.request.build_opener(NoRedirect)
        # Override only this imported client's transport, not urllib globally.
        module.urllib = SimpleNamespace(
            request=SimpleNamespace(Request=urllib.request.Request, urlopen=self._urlopen),
            parse=urllib.parse, error=urllib.error,
        )
        self._client = module

    def _allowed(self, method, path):
        if method == "GET":
            return path in {"/api/spec.json", "/api/admin/posts", "/api/admin/tags",
                            "/api/admin/categories", "/api/admin/posts/slug"} or bool(
                re.fullmatch(r"/api/admin/posts/[1-9][0-9]*(?:/tags)?", path))
        if self.draft_writes:
            if method == "POST" and path == "/api/admin/posts":
                return True
            if method == "PATCH" and re.fullmatch(r"/api/admin/posts/[1-9][0-9]*", path):
                return True
            if method == "PUT" and re.fullmatch(r"/api/admin/posts/[1-9][0-9]*/tags", path):
                return True
        return bool(self.publish_authorized and self.publish_post_id is not None and
                    method == "POST" and
                    path == f"/api/admin/posts/{self.publish_post_id}/publish")

    def _urlopen(self, req):
        parsed = urllib.parse.urlsplit(req.full_url)
        if (parsed.scheme != "https" or parsed.netloc != "blog.isomeria.net" or
                parsed.fragment or not self._allowed(req.get_method(), parsed.path)):
            raise OperationError("Destination or operation refused")
        # Inherit platform proxies and CA trust. Never disable TLS verification.
        return self._opener.open(req, timeout=10)

    def _request(self, method, path, *, query=None, body=None):
        environment_config()
        if not self._allowed(method, path):
            raise OperationError("Operation refused by runtime policy")
        old_handler = signal.signal(signal.SIGALRM, _deadline)
        signal.setitimer(signal.ITIMER_REAL, 20)
        try:
            status, payload = self._client.request(method, path, query=query, json_body=body)
        except Exception as exc:
            # Never expose exception text: it may contain headers or response data.
            self.events.append({"method": method, "path": path, "http": None,
                                "result": "transport-or-json-error"})
            raise OperationError(f"Request failed ({type(exc).__name__}); do not retry writes automatically") from None
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
        self.events.append({"method": method, "path": path, "http": status,
                            "result": "success" if 200 <= status < 300 else "http-error"})
        if not 200 <= status < 300:
            raise OperationError(f"HTTP {status}; response body withheld")
        return status, payload

    def get(self, path, *, query=None):
        return self._request("GET", path, query=query)

    def read_post(self, post_id):
        post_id = self._post_id(post_id)
        _, post = self.get(f"/api/admin/posts/{post_id}")
        if not isinstance(post, dict) or post.get("id") != post_id:
            raise OperationError("Post detail is absent or invalid")
        return post

    @staticmethod
    def _post_id(post_id):
        if type(post_id) is not int or post_id <= 0:
            raise OperationError("A positive integer post ID is required")
        return post_id

    @staticmethod
    def _assert_draft(post):
        if post.get("status") != "draft" or post.get("hasPublicSnapshot") is not False:
            raise OperationError("Target is not an unpublished draft; stopped")

    @staticmethod
    def _verify(post, expected):
        def comparable(value):
            # The server removes code highlighting from the editable draft.
            value = copy.deepcopy(value)
            def walk(node):
                if not isinstance(node, dict):
                    return
                if node.get("type") == "codeBlock" and isinstance(node.get("attrs"), dict):
                    node["attrs"].pop("highlightedHtml", None)
                for child in node.get("content", []) or []:
                    walk(child)
            walk(value)
            return value
        for field, value in expected.items():
            actual = post.get(field)
            if field == "contentJson":
                actual, value = comparable(actual), comparable(value)
            if actual != value:
                raise OperationError("Read-back differs from intended draft content; inspect, do not retry blindly")

    def create_draft(self, *, title, markdown, summary=None):
        if not self.draft_writes:
            raise OperationError("Draft writes are disabled")
        if not isinstance(title, str) or not title.strip():
            raise OperationError("A nonempty title is required")
        data = {"title": title, "summary": summary,
                "contentJson": self.codec.from_markdown(markdown)}
        # Supplying data always creates a new draft; never reuse an empty draft.
        _, created = self._request("POST", "/api/admin/posts", body={"data": data})
        if not isinstance(created, dict):
            raise OperationError("Create response has no post ID; inspect before retrying")
        post_id = self._post_id(created.get("id"))
        post = self.read_post(post_id)
        self._assert_draft(post)
        self._verify(post, data)
        return post

    def save_draft(self, post_id, *, data, expected_updated_at, tag_ids=None):
        if not self.draft_writes:
            raise OperationError("Draft writes are disabled")
        if not isinstance(data, dict) or set(data) - {"title", "summary", "contentJson", "slug", "categoryId"}:
            raise OperationError("Only draft text, slug and category fields are accepted")
        if tag_ids is not None and (not isinstance(tag_ids, list) or
                                   any(type(i) is not int or i <= 0 for i in tag_ids)):
            raise OperationError("Tag IDs must be a list of positive integers")
        post = self.read_post(post_id)
        self._assert_draft(post)
        if not expected_updated_at or post.get("updatedAt") != expected_updated_at:
            raise OperationError("Draft changed since inspection; review before updating")
        if data:
            self._request("PATCH", f"/api/admin/posts/{post_id}", body={"data": data})
        if tag_ids is not None:
            self._request("PUT", f"/api/admin/posts/{post_id}/tags", body={"tagIds": tag_ids})
        saved = self.read_post(post_id)
        self._assert_draft(saved)
        self._verify(saved, data)
        if tag_ids is not None:
            _, tags = self.get(f"/api/admin/posts/{post_id}/tags")
            items = tags.get("items", []) if isinstance(tags, dict) else tags
            if not isinstance(items, list) or sorted(t["id"] for t in items) != sorted(set(tag_ids)):
                raise OperationError("Tag read-back verification failed")
        return saved

    def publish(self, post_id, *, expected_updated_at):
        post_id = self._post_id(post_id)
        if not self.publish_authorized or self.publish_post_id != post_id:
            raise OperationError("Explicit user authorization for this post ID is required")
        post = self.read_post(post_id)
        if not expected_updated_at or post.get("updatedAt") != expected_updated_at:
            raise OperationError("Post changed since publication review")
        self._request("POST", f"/api/admin/posts/{post_id}/publish", body={})
        saved = self.read_post(post_id)
        if saved.get("status") != "published" or saved.get("hasPublicSnapshot") is not True:
            raise OperationError("Publication read-back verification failed")
        return saved


def main():
    parser = argparse.ArgumentParser(description="Isomeria publisher runtime (read-only CLI)")
    parser.add_argument("action", choices=["check", "list", "get"])
    parser.add_argument("--id", type=int)
    args = parser.parse_args()
    client = Client()
    try:
        if args.action == "check":
            print(json.dumps({"credential_present_nonempty": True, "policy": "read-only"}))
            return
        if args.action == "list":
            _, body = client.get("/api/admin/posts", query={"limit": 1, "offset": 0})
            ids = [post["id"] for post in body["items"]]
        else:
            ids = [client.read_post(args.id)["id"]]
        print(json.dumps({"requests": client.events, "post_ids": ids, "result": "read-only success"}))
    except OperationError as exc:
        print(json.dumps({"requests": client.events, "error": str(exc)}))
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except OperationError as exc:
        print(json.dumps({"error": str(exc)}))
        raise SystemExit(1)
