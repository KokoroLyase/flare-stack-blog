"""Offline security/workflow checks. All credentials and HTTP responses are fake."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import urllib.error
import urllib.request
import urllib.response
from email.message import Message

TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_ROOT))

from runtime import Client, NoRedirect, OperationError, REPO_ROOT


class Checks(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"FSB_ADMIN_API_KEY": "synthetic-offline-value"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_missing_key_has_no_fallback(self):
        for value in ("", "   ", None):
            with patch.dict(os.environ):
                if value is None:
                    os.environ.pop("FSB_ADMIN_API_KEY", None)
                else:
                    os.environ["FSB_ADMIN_API_KEY"] = value
                with self.assertRaises(OperationError):
                    Client()

    def test_reuses_original_transport_and_only_header_credential(self):
        client = Client()
        response = io.BytesIO(b'{"items":[],"total":0,"statusCounts":{}}')
        response.status = 200
        client._opener.open = Mock(return_value=response)
        status, body = client.get("/api/admin/posts", query={"limit": 1})
        self.assertEqual(status, 200)
        req = client._opener.open.call_args.args[0]
        self.assertEqual(req.get_header("X-api-key"), "synthetic-offline-value")
        self.assertNotIn("synthetic-offline-value", req.full_url)
        self.assertEqual(req.get_header("User-agent"), client._client.BROWSER_UA)
        self.assertEqual(client._opener.open.call_args.kwargs, {"timeout": 10})

    def test_foreign_origin_refused_before_transport(self):
        client = Client()
        client._opener.open = Mock()
        for url in ("https://example.com/api/spec.json", "http://blog.isomeria.net/api/spec.json",
                    "https://blog.isomeria.net.evil.example/api/spec.json"):
            with self.assertRaises(OperationError):
                client._urlopen(urllib.request.Request(url))
        client._opener.open.assert_not_called()

    def test_default_read_only_and_destructive_operations_always_denied(self):
        for client in (Client(), Client(draft_writes=True, publish_authorized=True)):
            client._opener.open = Mock()
            with self.assertRaises(OperationError):
                client._request("DELETE", "/api/admin/posts/8")
            with self.assertRaises(OperationError):
                client._request("POST", "/api/admin/posts/8/unpublish", body={})
            client._opener.open.assert_not_called()
        with self.assertRaises(OperationError):
            Client().create_draft(title="offline", markdown="body")
        with self.assertRaises(OperationError):
            Client(draft_writes=True).publish(8, expected_updated_at="old")

    def test_safe_transport_error(self):
        client = Client()
        client._opener.open = Mock(side_effect=RuntimeError("synthetic-offline-value private debug"))
        with self.assertRaises(OperationError) as ctx:
            client.get("/api/spec.json")
        self.assertNotIn("synthetic-offline-value", str(ctx.exception))
        self.assertNotIn("private debug", str(ctx.exception))

    def test_create_never_reuses_empty_draft_and_reads_back(self):
        client = Client(draft_writes=True)
        content = client.codec.from_markdown('## Title\n\n```python\nx = 1\n```\n')
        saved = {"id": 8, "status": "draft", "hasPublicSnapshot": False,
                 "title": "offline", "summary": None, "contentJson": copy.deepcopy(content)}
        for node in saved["contentJson"]["content"]:
            if node["type"] == "codeBlock":
                node["attrs"].pop("highlightedHtml", None)
        client._request = Mock(side_effect=[(200, {"id": 8}), (200, saved)])
        result = client.create_draft(title="offline", markdown='## Title\n\n```python\nx = 1\n```\n')
        first = client._request.call_args_list[0]
        self.assertEqual(first.args, ("POST", "/api/admin/posts"))
        self.assertEqual(first.kwargs["body"]["data"]["title"], "offline")
        self.assertEqual(result["id"], 8)
        self.assertEqual(client._request.call_args_list[1].args, ("GET", "/api/admin/posts/8"))

    def test_stale_or_published_draft_is_not_updated(self):
        for post in ({"id": 8, "status": "draft", "hasPublicSnapshot": False, "updatedAt": "new"},
                     {"id": 8, "status": "published", "hasPublicSnapshot": True, "updatedAt": "old"}):
            client = Client(draft_writes=True)
            client._request = Mock(return_value=(200, post))
            with self.assertRaises(OperationError):
                client.save_draft(8, data={"title": "changed"}, expected_updated_at="old")
            self.assertEqual(client._request.call_count, 1)
            self.assertEqual(client._request.call_args.args, ("GET", "/api/admin/posts/8"))

    def test_save_verifies_fields_and_tag_replacement(self):
        client = Client(draft_writes=True)
        before = {"id": 8, "status": "draft", "hasPublicSnapshot": False, "updatedAt": "old", "title": "before"}
        after = {**before, "title": "after", "updatedAt": "new"}
        client._request = Mock(side_effect=[(200, before), (200, {}), (200, {}), (200, after), (200, [{"id": 2}])])
        client.save_draft(8, data={"title": "after"}, expected_updated_at="old", tag_ids=[2])
        self.assertEqual(client._request.call_args_list[2].kwargs["body"], {"tagIds": [2]})
        with self.assertRaises(OperationError):
            client._verify(after, {"title": "wrong"})

    def test_relocated_checkout_resolves_original_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "other-checkout"
            tools = root / "tools/isomeria-publisher"
            scripts = root / ".agents/skills/flare-stack-blog/scripts"
            tools.mkdir(parents=True)
            scripts.mkdir(parents=True)
            for name in ("runtime.py", "probe.py"):
                shutil.copyfile(TOOL_ROOT / name, tools / name)
            for name in ("fsb.py", "tiptap.py"):
                shutil.copyfile(REPO_ROOT / ".agents/skills/flare-stack-blog/scripts" / name, scripts / name)
            code = (
                "import sys, runpy; from pathlib import Path; "
                "tool = Path(sys.argv[1]); sys.path.insert(0, str(tool)); "
                "from runtime import Client, SCRIPTS, REPO_ROOT; "
                "assert SCRIPTS == tool.parents[1] / '.agents/skills/flare-stack-blog/scripts'; "
                "client = Client(); assert Path(client._client.__file__).parent == SCRIPTS; "
                "assert client.codec.from_markdown('## portable')['type'] == 'doc'; "
                "probe = runpy.run_path(str(tool / 'probe.py')); "
                "assert probe['ROOT'] == REPO_ROOT / 'tmp/isomeria-publisher'; "
                "print('portable dependencies passed')"
            )
            completed = subprocess.run([sys.executable, "-B", "-c", code, str(tools)],
                                       cwd=directory, capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("portable dependencies passed", completed.stdout)

    def test_markdown_fixture_round_trip(self):
        codec = Client().codec
        markdown = Path(__file__).with_name("sample.md").read_text(encoding="utf-8")
        doc = codec.from_markdown(markdown)
        self.assertEqual(codec.from_markdown(codec.to_markdown(doc)), doc)
        self.assertTrue({"heading", "paragraph", "bulletList", "orderedList", "blockquote",
                         "codeBlock", "table", "image", "blockMath"} <=
                        {node["type"] for node in doc["content"]})

    def test_redirect_does_not_forward_authenticated_request(self):
        # Synthetic HTTPS responses exercise urllib's actual redirect processing.
        for status in (301, 302, 303, 307, 308):
            attempted = []
            class RedirectResponse(urllib.request.HTTPSHandler):
                def https_open(self, req):
                    attempted.append(req.full_url)
                    headers = Message()
                    headers["Location"] = "https://other.example/api/spec.json"
                    response = urllib.response.addinfourl(io.BytesIO(b"redirect"), headers, req.full_url, status)
                    response.msg = "Redirect"
                    return response
            client = Client()
            client._opener = urllib.request.build_opener(NoRedirect, RedirectResponse)
            with self.assertRaises(OperationError):
                client.get("/api/spec.json")
            self.assertEqual(attempted, ["https://blog.isomeria.net/api/spec.json"])
            self.assertEqual(client.events[-1]["http"], status)

    def test_publication_authorization_is_bound_to_specific_post(self):
        client = Client(publish_authorized=True, publish_post_id=8)
        client._request = Mock()
        with self.assertRaises(OperationError):
            client.publish(9, expected_updated_at="old")
        client._request.assert_not_called()
        self.assertTrue(client._allowed("POST", "/api/admin/posts/8/publish"))
        self.assertFalse(client._allowed("POST", "/api/admin/posts/9/publish"))
        self.assertFalse(Client(publish_authorized=True)._allowed("POST", "/api/admin/posts/8/publish"))
        before = {"id": 8, "updatedAt": "old", "status": "draft", "hasPublicSnapshot": False}
        after = {**before, "status": "published", "hasPublicSnapshot": True, "publicSlug": "offline"}
        client._request = Mock(side_effect=[(200, before), (200, {}), (200, after)])
        result = client.publish(8, expected_updated_at="old")
        self.assertEqual(result["publicSlug"], "offline")
        self.assertEqual(client._request.call_args_list[1].args, ("POST", "/api/admin/posts/8/publish"))

    def test_http_error_body_with_credentials_is_not_reported(self):
        client = Client()
        error = urllib.error.HTTPError("https://blog.isomeria.net/api/spec.json", 403, "Forbidden",
                                       Message(), io.BytesIO(b'{"private":"synthetic-offline-value"}'))
        client._opener.open = Mock(side_effect=error)
        with self.assertRaises(OperationError) as context:
            client.get("/api/spec.json")
        self.assertNotIn("synthetic-offline-value", str(context.exception))
        self.assertEqual(client.events[-1]["http"], 403)
        self.assertNotIn("synthetic-offline-value", json.dumps(client.events))

    def test_timed_out_creation_is_never_automatically_retried(self):
        client = Client(draft_writes=True)
        client._opener.open = Mock(side_effect=TimeoutError("private transport information"))
        with self.assertRaises(OperationError):
            client.create_draft(title="offline", markdown="local text")
        self.assertEqual(client._opener.open.call_count, 1)
        self.assertEqual(client.events[-1]["method"], "POST")
        self.assertIsNone(client.events[-1]["http"])


if __name__ == "__main__":
    unittest.main()
