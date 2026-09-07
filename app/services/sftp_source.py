from pathlib import Path, PurePosixPath
import stat
import posixpath
import paramiko

class SftpSource:
    def __init__(self, host: str, port: int, username: str, password: str, timeout=10):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout

    def _connect(self):
        transport = paramiko.Transport((self.host, self.port))
        transport.banner_timeout = self.timeout
        transport.auth_timeout = self.timeout
        transport.connect(username=self.username, password=self.password)
        return transport, paramiko.SFTPClient.from_transport(transport)

    def test(self):
        transport, sftp = self._connect()
        try:
            sftp.listdir(".")
            return True
        finally:
            sftp.close()
            transport.close()

    def download(self, remote_path: str, local_root: Path, log=None) -> Path:
        local_root.mkdir(parents=True, exist_ok=True)
        transport, sftp = self._connect()
        try:
            remote_path = remote_path.rstrip("/") or "/"
            st = sftp.stat(remote_path)
            if stat.S_ISDIR(st.st_mode):
                name = PurePosixPath(remote_path).name or "remote"
                dest = local_root / name
                dest.mkdir(parents=True, exist_ok=True)
                self._download_dir(sftp, remote_path, dest, log)
                return dest
            else:
                dest = local_root / PurePosixPath(remote_path).name
                if log:
                    log(f"SFTP 下载：{remote_path}")
                sftp.get(remote_path, str(dest))
                return dest
        finally:
            sftp.close()
            transport.close()

    def _download_dir(self, sftp, remote_dir: str, local_dir: Path, log=None):
        for attr in sftp.listdir_attr(remote_dir):
            rp = posixpath.join(remote_dir, attr.filename)
            lp = local_dir / attr.filename
            if stat.S_ISDIR(attr.st_mode):
                lp.mkdir(parents=True, exist_ok=True)
                self._download_dir(sftp, rp, lp, log)
            elif stat.S_ISREG(attr.st_mode):
                if log:
                    log(f"SFTP 下载：{rp}")
                lp.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(rp, str(lp))
