"""Local protocol fixture for integration checks; its scores are not research results."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [((digest[index % len(digest)] / 255) - 0.5) for index in range(768)]


class Handler(BaseHTTPRequestHandler):
    bad_query_embeddings = False

    def do_GET(self) -> None:
        if self.path.rstrip("/") != "/v1/models":
            self.send_error(404)
            return
        self._json({"object": "list", "data": [{"id": "fixture-model", "object": "model"}]})

    def do_POST(self) -> None:
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/v1/embeddings":
            values = request.get("input", "")
            values = values if isinstance(values, list) else [values]
            self._json({"object": "list", "model": request.get("model", "fixture-model"),
                        "data": [{"object": "embedding", "index": index,
                                  "embedding": [] if self.bad_query_embeddings and str(value).startswith("查找")
                                  else vector(str(value))} for index, value in enumerate(values)]})
            return
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        messages = request.get("messages", [])
        prompt = str(messages[-1].get("content", "")) if messages else ""
        if "聚类重打标" in prompt:
            ids = re.findall(r"ID:\s*([^\)\s]+)", prompt)
            answer = [{"id": item, "refined_tags": ["coding"],
                       "refined_summary": "正在整理测试活动", "cluster_id": "fixture-cluster"}
                      for item in ids]
        else:
            title = next((line.split("] ", 1)[-1] for line in prompt.splitlines() if "] " in line), "测试活动")
            answer = {"summary": f"正在查看 {title}", "tags": ["coding"],
                      "confidence": 0.8, "primary_app": "fixture-app"}
        self._json({"id": "fixture", "object": "chat.completion", "created": 1,
                    "model": request.get("model", "fixture-model"),
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}}]})

    def _json(self, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


class NoEmbeddingHandler(Handler):
    def do_POST(self) -> None:
        if self.path == "/v1/embeddings":
            self.send_error(404, "Embedding model is unavailable")
        else:
            super().do_POST()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()

