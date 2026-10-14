from __future__ import annotations

"""Small, dependency-free HTTPS client for the FDS Windows Agent."""

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
    # A few MiB keeps the number of HTTPS requests manageable without making
    # each retry expensive when a target connection is unstable.
    CHUNK_SIZE = 4 * 1024 * 1024
    UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024

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

    def _headers(self, content_length: int | None = None,
                 content_type: str | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json", "Connection": "close"}
        if self.endpoint.token:
            headers["X-FDS-Agent-Token"] = self.endpoint.token
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        if content_type:
            headers["Content-Type"] = content_type
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
            headers = self._headers(
                len(body) if body is not None else None,
                "application/json; charset=utf-8" if body is not None else None,
            )
            connection.request(method, path, body=body, headers=headers)
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

    def _raw_request(self, method: str, path: str, body: bytes,
                     accepted: tuple[int, ...] = (200,)) -> dict:
        """Send one bounded binary request and always close that connection."""
        connection = self._connect()
        try:
            connection.request(
                method,
                path,
                body=body,
                headers=self._headers(len(body), "application/octet-stream"),
            )
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

    def _raw_request_on_connection(self, connection: http.client.HTTPSConnection,
                                   method: str, path: str, body: bytes,
                                   accepted: tuple[int, ...] = (200,)) -> dict:
        headers = self._headers(len(body), "application/octet-stream")
        headers["Connection"] = "keep-alive"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = self._decode_response(response)
        if response.status not in accepted:
            raise AgentError(self._error_message(response.status, payload))
        return payload

    def health(self) -> dict:
        return self._request("GET", "/api/v1/health")

    def status(self) -> dict:
        return self._request("GET", "/api/v1/status")

    def inspect_file(self, path: str, include_sha256: bool = True) -> dict:
        body = json.dumps({"path": path, "includeSha256": bool(include_sha256)}, ensure_ascii=False).encode("utf-8")
        return self._request("POST", "/api/v1/files/inspect", body)

    def list_directory_entries(self, path: str = "") -> list[dict]:
        query = urlencode({"path": path or ""})
        payload = self._request("GET", f"/api/v1/files/list?{query}")
        entries = []
        for raw in payload.get("entries") or []:
            item = dict(raw or {})
            item["is_dir"] = bool(item.get("isDir", item.get("is_dir", False)))
            item["modified"] = item.get("lastWriteUtc", item.get("modified", ""))
            # Older Agent builds returned an empty Path.GetFileName for drive
            # roots such as C:\\. Keep the desktop usable while a target is
            # being upgraded by falling back to the returned absolute path.
            if not str(item.get("name") or "").strip() and item.get("path"):
                item["name"] = str(item["path"])
            entries.append(item)
        return entries

    def list_drives(self) -> list[dict]:
        return self.list_directory_entries("")

    def list_known_folders(self) -> list[dict]:
        # The Agent intentionally exposes only its configured AllowedRoots.
        return []

    def path_info(self, path: str) -> dict:
        item = self.inspect_file(path, include_sha256=False)
        item["is_dir"] = bool(item.get("isDir", item.get("is_dir", False)))
        return item

    def ensure_directory(self, path: str) -> dict:
        body = json.dumps({"path": path}, ensure_ascii=False).encode("utf-8")
        return self._request("POST", "/api/v1/files/directory", body)

    def backup_file(self, source_path: str, backup_path: str) -> dict:
        body = json.dumps({"sourcePath": source_path, "backupPath": backup_path}, ensure_ascii=False).encode("utf-8")
        return self._request("POST", "/api/v1/files/backup", body)

    def backup_tree(self, source_path: str, backup_path: str) -> dict:
        body = json.dumps({"sourcePath": source_path, "backupPath": backup_path}, ensure_ascii=False).encode("utf-8")
        return self._request("POST", "/api/v1/files/backup-tree", body)

    def system_info(self) -> dict:
        return self._request("GET", "/api/v1/system/info")

    def free_space(self, path: str) -> int | None:
        drive, _ = os.path.splitdrive(path)
        if not drive:
            return None
        wanted = (drive.rstrip("\\/") + "\\").upper()
        for item in self.system_info().get("drives") or []:
            name = str(item.get("name") or "").replace("/", "\\")
            if name.upper() == wanted:
                return int(item.get("freeBytes", item.get("free_bytes", 0)) or 0)
        return None

    def list_processes(self) -> list[dict]:
        payload = self._request("GET", "/api/v1/operations/process/list")
        result = []
        for raw in payload.get("data") or payload.get("processes") or []:
            item = dict(raw or {})
            result.append({
                "pid": int(item.get("pid", item.get("Pid", 0)) or 0),
                "image_name": str(item.get("imageName", item.get("ImageName", item.get("image_name", ""))) or ""),
                "description": str(item.get("description", item.get("Description", "")) or ""),
                "path": str(item.get("path", item.get("Path", "")) or ""),
                "command_line": str(item.get("commandLine", item.get("CommandLine", "")) or ""),
            })
        return result

    def rename(self, path: str, new_path: str) -> dict:
        body = json.dumps({"path": path, "newPath": new_path}, ensure_ascii=False).encode("utf-8")
        return self._request("POST", "/api/v1/files/rename", body)

    def delete(self, path: str) -> dict:
        query = urlencode({"path": path})
        return self._request("DELETE", f"/api/v1/files?{query}")

    def download_file(self, remote_path: str, local_path: str | Path,
                      progress: Callable[[int, int], None] | None = None) -> Path:
        query = urlencode({"path": remote_path})
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        done = 0
        temporary = target.with_name(target.name + ".fds-download.tmp")
        try:
            connection.request("GET", f"/api/v1/files/download?{query}", headers=self._headers())
            response = connection.getresponse()
            if response.status != 200:
                raise AgentError(self._error_message(response.status, self._decode_response(response)))
            total = int(response.getheader("Content-Length") or 0)
            with temporary.open("wb") as stream:
                while True:
                    chunk = response.read(self.CHUNK_SIZE)
                    if not chunk:
                        break
                    stream.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            temporary.replace(target)
            return target
        except AgentError:
            raise
        except (OSError, ssl.SSLError, TimeoutError) as exc:
            raise AgentError(f"Agent 下载中断（已接收 {done} 字节）：{exc}") from exc
        finally:
            connection.close()
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass

    def upload_file(self, local_path: str | Path, target_path: str,
                    progress: Callable[[int, int], None] | None = None,
                    sha256_hex: str = "") -> dict:
        source = Path(local_path)
        if not source.is_file():
            raise AgentError(f"本地文件不存在：{source}")
        if not target_path or not os.path.isabs(target_path):
            raise AgentError("Agent 目标文件必须使用目标主机上的绝对路径。")
        size = source.stat().st_size
        expected_hash = "".join(ch for ch in (sha256_hex or "").upper() if ch in "0123456789ABCDEF")
        init_body = json.dumps({
            "path": target_path,
            "size": size,
            "sha256": expected_hash,
        }, ensure_ascii=False).encode("utf-8")
        init = self._request("POST", "/api/v1/files/upload/init", init_body)
        upload_id = str(init.get("uploadId") or "")
        sent = int(init.get("offset", 0) or 0)
        if not upload_id or sent < 0 or sent > size:
            raise AgentError("Agent 返回了无效的上传会话。")
        if progress:
            progress(sent, size)

        reconnect_attempts = 0
        connection = None
        try:
            with source.open("rb") as stream:
                while sent < size:
                    stream.seek(sent)
                    chunk = stream.read(min(self.UPLOAD_CHUNK_SIZE, size - sent))
                    if not chunk:
                        raise AgentError(f"本地文件读取提前结束（已读取 {sent} / {size} 字节）。")
                    try:
                        if connection is None:
                            connection = self._connect()
                        query = urlencode({"path": target_path, "uploadId": upload_id, "offset": sent})
                        response = self._raw_request_on_connection(
                            connection, "PUT", f"/api/v1/files/upload/chunk?{query}", chunk)
                        new_offset = int(response.get("offset", sent + len(chunk)) or 0)
                        if new_offset <= sent or new_offset > size:
                            raise AgentError("Agent 返回了无效的上传偏移量。")
                        sent = new_offset
                        if connection.sock is None:
                            connection.close()
                            connection = None
                    except (AgentError, OSError, ssl.SSLError, TimeoutError):
                        if connection is not None:
                            connection.close()
                            connection = None
                        reconnect_attempts += 1
                        if reconnect_attempts > 3:
                            raise
                        # The server commits a chunk before acknowledging it.  A
                        # fresh init therefore discovers the committed offset even
                        # when the acknowledgement itself was lost.
                        init = self._request("POST", "/api/v1/files/upload/init", init_body)
                        upload_id = str(init.get("uploadId") or upload_id)
                        new_sent = int(init.get("offset", sent) or sent)
                        if new_sent < sent:
                            raise
                        sent = new_sent
                        continue
                    reconnect_attempts = 0
                    if progress:
                        progress(sent, size)
        finally:
            if connection is not None:
                connection.close()

        complete_body = json.dumps({
            "path": target_path,
            "uploadId": upload_id,
        }, ensure_ascii=False).encode("utf-8")
        return self._request("POST", "/api/v1/files/upload/complete", complete_body)

    def kill_process(self, image_name: str) -> dict:
        image = (image_name or "").strip()
        if not image:
            raise AgentError("进程名不能为空。")
        body = json.dumps({"imageName": image}, ensure_ascii=False).encode("utf-8")
        return self._request("POST", "/api/v1/operations/process/kill", body, accepted=(200,))

    def restart(self, force: bool = True) -> dict:
        body = json.dumps({"force": bool(force)}).encode("utf-8")
        return self._request("POST", "/api/v1/operations/restart", body, accepted=(200, 202))
