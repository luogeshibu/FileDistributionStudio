from __future__ import annotations

"""Small, dependency-free HTTPS client for the FDS Windows Agent.

The desktop application deliberately keeps the Agent transport separate from the
legacy WinRM transport.  This makes the migration reversible and prevents a
failed Agent connection from changing existing WinRM behavior.
"""

from dataclasses import dataclass
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import ssl
from typing import Callable
from urllib.parse import urlencode


class AgentError(RuntimeError):
    """A user-facing Agent transport or API error."""


@dataclass(frozen=True)
class AgentEndpoint:
    host: str
    port: int = 8743
    token: str = ""
    ca_file: str = ""
    certificate_sha256: str = ""
    timeout: float = 10.0


class AgentClient:
    CHUNK_SIZE = 128 * 1024

    def __init__(self, endpoint: AgentEndpoint):
        host = (endpoint.host or "").strip()
        if not host:
            raise ValueError("Agent 主机不能为空。")
        if not 1 <= int(endpoint.port) <= 65535:
            raise ValueError("Agent 端口必须在 1 到 65535 之间。")
        self.endpoint = AgentEndpoint(
            host=host,
            port=int(endpoint.port),
            token=endpoint.token or "",
            ca_file=endpoint.ca_file or "",
            certificate_sha256=self._normalize_pin(endpoint.certificate_sha256),
            timeout=max(0.1, float(endpoint.timeout)),
        )

    @staticmethod
    def _normalize_pin(value: str) -> str:
        return "".join(ch for ch in (value or "").upper() if ch in "0123456789ABCDEF")

    def _ssl_context(self) -> ssl.SSLContext:
        pin = self.endpoint.certificate_sha256
        if self.endpoint.ca_file:
            context = ssl.create_default_context(cafile=self.endpoint.ca_file)
        elif pin:
            # A pinned self-signed certificate is still verified below.  The TLS
            # channel is encrypted and the exact peer certificate must match.
            context = ssl._create_unverified_context()
        else:
            context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    def _connect(self) -> http.client.HTTPSConnection:
        connection = http.client.HTTPSConnection(
            self.endpoint.host,
            self.endpoint.port,
            timeout=self.endpoint.timeout,
            context=self._ssl_context(),
        )
        try:
            connection.connect()
            pin = self.endpoint.certificate_sha256
            if pin:
                actual = sha256(connection.sock.getpeercert(binary_form=True)).hexdigest().upper()
                if actual != pin:
                    raise AgentError(
                        f"Agent 证书指纹不匹配：期望 {pin}，实际 {actual}。"
                    )
            return connection
        except AgentError:
            connection.close()
            raise
        except (OSError, ssl.SSLError) as exc:
            connection.close()
            raise AgentError(f"无法连接 Agent {self.endpoint.host}:{self.endpoint.port}：{exc}") from exc

    def _headers(self, content_length: int | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json", "Connection": "close"}
        if self.endpoint.token:
            headers["X-FDS-Agent-Token"] = self.endpoint.token
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        return headers

    @staticmethod
    def _decode_response(response: http.client.HTTPResponse) -> dict:
        raw = response.read()
        if not raw:
            payload = {}
        else:
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                payload = {"message": raw.decode("utf-8", errors="replace")}
        if not isinstance(payload, dict):
            payload = {"data": payload}
        return payload

    @staticmethod
    def _error_message(status: int, payload: dict) -> str:
        text = payload.get("error") or payload.get("detail") or payload.get("message")
        return f"Agent 请求失败（HTTP {status}）" + (f"：{text}" if text else "。")

    def _request(self, method: str, path: str, body: bytes | None = None,
                 accepted: tuple[int, ...] = (200,)) -> dict:
        connection = self._connect()
        try:
            connection.request(method, path, body=body, headers=self._headers(len(body) if body is not None else None))
            response = connection.getresponse()
            payload = self._decode_response(response)
            if response.status not in accepted:
                raise AgentError(self._error_message(response.status, payload))
            return payload
        except AgentError:
            raise
        except (OSError, ssl.SSLError, TimeoutError) as exc:
            raise AgentError(f"Agent 请求中断：{exc}") from exc
        finally:
            connection.close()

    def health(self) -> dict:
        return self._request("GET", "/api/v1/health")

    def status(self) -> dict:
        return self._request("GET", "/api/v1/status")

    def inspect_file(self, path: str, include_sha256: bool = True) -> dict:
        body = json.dumps({"path": path, "includeSha256": bool(include_sha256)}, ensure_ascii=False).encode("utf-8")
        return self._request("POST", "/api/v1/files/inspect", body)

    def backup_file(self, source_path: str, backup_path: str) -> dict:
        body = json.dumps({"sourcePath": source_path, "backupPath": backup_path}, ensure_ascii=False).encode("utf-8")
        return self._request("POST", "/api/v1/files/backup", body)

    def upload_file(self, local_path: str | Path, target_path: str,
                    progress: Callable[[int, int], None] | None = None) -> dict:
        source = Path(local_path)
        if not source.is_file():
            raise AgentError(f"本地文件不存在：{source}")
        if not target_path or not os.path.isabs(target_path):
            raise AgentError("Agent 目标文件必须使用目标主机上的绝对路径。")
        size = source.stat().st_size
        query = urlencode({"path": target_path})
        connection = self._connect()
        sent = 0
        try:
            connection.putrequest("PUT", f"/api/v1/files?{query}")
            for key, value in self._headers(size).items():
                connection.putheader(key, value)
            connection.endheaders()
            with source.open("rb") as stream:
                while True:
                    chunk = stream.read(self.CHUNK_SIZE)
                    if not chunk:
                        break
                    connection.send(chunk)
                    sent += len(chunk)
                    if progress:
                        progress(sent, size)
            response = connection.getresponse()
            payload = self._decode_response(response)
            if response.status != 200:
                raise AgentError(self._error_message(response.status, payload))
            return payload
        except AgentError:
            raise
        except (OSError, ssl.SSLError, TimeoutError) as exc:
            raise AgentError(f"Agent 上传中断（已发送 {sent} / {size} 字节）：{exc}") from exc
        finally:
            connection.close()

    def kill_process(self, image_name: str) -> dict:
        image = (image_name or "").strip()
        if not image:
            raise AgentError("进程名不能为空。")
        body = json.dumps({"imageName": image}, ensure_ascii=False).encode("utf-8")
        return self._request("POST", "/api/v1/operations/process/kill", body, accepted=(200,))

    def restart(self, force: bool = True) -> dict:
        body = json.dumps({"force": bool(force)}).encode("utf-8")
        return self._request("POST", "/api/v1/operations/restart", body, accepted=(200, 202))
