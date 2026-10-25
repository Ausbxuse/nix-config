import base64
from pathlib import Path
import tempfile
import unittest
from media import read_video
from bridge import BridgeError

class MediaTests(unittest.TestCase):
    def test_source_files_are_plain_text_and_stay_within_the_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            agent = {"cwd": str(project)}
            for name in ("default.nix", "main.py", "index.html", "icon.svg", "Makefile", ".gitignore", "metrics.tsv", "events.jsonl", "events.ndjson"):
                (project / name).write_text("<script>alert('source only')</script>")
                value = read_video(agent, name)
                self.assertEqual(value["type"], "text/plain; charset=utf-8")
                self.assertEqual(base64.b64decode(value["data"]), b"<script>alert('source only')</script>")
            (root / "private.nix").write_text("outside project")
            (project / "escape.nix").symlink_to(root / "private.nix")
            for name in ("../private.nix", str(root / "private.nix"), "escape.nix"):
                with self.assertRaises(BridgeError):
                    read_video(agent, name)

    def test_ranges_and_project_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (project / "clip.mp4").write_bytes(b"0123456789")
            (root / "private.mp4").write_bytes(b"secret")
            (project / "escape.mp4").symlink_to(root / "private.mp4")
            agent = {"cwd": str(project)}
            value = read_video(agent, "clip.mp4", 2, 5)
            self.assertEqual(base64.b64decode(value["data"]), b"2345")
            self.assertEqual(value["size"], 10)
            for path in ("../private.mp4", str(root / "private.mp4"), "escape.mp4", "missing.mp4"):
                with self.assertRaises(BridgeError):
                    read_video(agent, path)
            with self.assertRaises(BridgeError):
                read_video(agent, "clip.mp4", 20)

    def test_large_reads_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "clip.webm").write_bytes(b"x" * 900000)
            value = read_video({"cwd": directory}, "clip.webm")
            self.assertEqual(len(base64.b64decode(value["data"])), 512 * 1024)

    def test_empty_files_have_a_real_empty_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'empty.csv').touch()
            value = read_video({'cwd':directory}, 'empty.csv', 0, 65535)
            self.assertEqual((value['data'],value['size'],value['end']), ('',0,-1))
            with self.assertRaises(BridgeError):read_video({'cwd':directory}, 'empty.csv', 1)


    def test_http_range_response(self):
        import threading
        from unittest.mock import Mock
        from urllib.request import Request, urlopen
        from server import Handler, ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "clip.mp4").write_bytes(b"0123456789")
            (Path(directory) / "trace.png").write_bytes(b"p" * 700000)
            (Path(directory) / "index.html").write_text("<script>alert('source only')</script>")
            (Path(directory) / 'empty.csv').touch()
            (Path(directory) / 'trace.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
            with (Path(directory) / 'large.png').open('wb') as large:
                large.truncate(17 * 1024 * 1024)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.allowed_hosts = {"127.0.0.1"}
            server.hub = Mock()
            server.hub.client_allowed.return_value = True
            server.hub.video.side_effect = lambda agent, path, start=0, end=None: read_video({"cwd": directory}, path, start, end)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}/api/media?agent_id=fixture&path=clip.mp4"
                with urlopen(Request(url, headers={"Range": "bytes=3-6"})) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.headers["Content-Range"], "bytes 3-6/10")
                    self.assertEqual(response.read(), b"3456")
                with urlopen(Request(url.replace('clip.mp4', 'empty.csv'), headers={'Range':'bytes=0-65535'})) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIsNone(response.headers.get('Content-Range'))
                    self.assertEqual(response.read(), b'')
                image_url = f"http://127.0.0.1:{server.server_port}/api/file?agent_id=fixture&path=trace.png"
                with urlopen(image_url) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Content-Type"], "image/png")
                    self.assertEqual(response.read(), b"p" * 700000)
                source_url = f"http://127.0.0.1:{server.server_port}/api/file?agent_id=fixture&path=index.html"
                with urlopen(source_url) as response:
                    self.assertEqual(response.headers["Content-Type"], "text/plain; charset=utf-8")
                    self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                    self.assertIn("sandbox", response.headers["Content-Security-Policy"])
                    self.assertEqual(response.read(), b"<script>alert('source only')</script>")

                base = f'http://127.0.0.1:{server.server_port}/api/file?agent_id=fixture&path='
                with urlopen(base + 'trace.svg') as response:
                    self.assertEqual(response.headers['Content-Type'], 'text/plain; charset=utf-8')
                with urlopen(base + 'trace.svg&preview=1') as response:
                    self.assertEqual(response.headers['Content-Type'], 'image/svg+xml')
                    self.assertIn("sandbox; default-src 'none'; img-src data:", response.headers['Content-Security-Policy'])
                    self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
                with urlopen(base + 'trace.png&preview=1') as response:
                    self.assertEqual(response.headers['Content-Type'], 'image/png')
                    self.assertEqual(len(response.read()), 700000)
                from urllib.error import HTTPError
                for name, status in [('index.html', 409), ('large.png', 413), ('../outside.png', 409)]:
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base + name + '&preview=1')
                    self.assertEqual(error.exception.code, status)
                    error.exception.close()

            finally:
                server.shutdown()
                server.server_close()
                thread.join()
