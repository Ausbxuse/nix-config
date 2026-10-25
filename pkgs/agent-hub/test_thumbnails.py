import base64
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zlib

from bridge import BridgeError
from thumbnails import Thumbnails


WEBP = b'RIFF\x10\0\0\0WEBPVP8 test'

def pdf():
    drawing = b'0.3 0.5 0.7 rg 0 0 300 400 re f\n'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Contents 4 0 R >>',
        f'<< /Length {len(drawing)} >>\nstream\n'.encode() + drawing + b'endstream']
    content = b'%PDF-1.4\n'; offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(content)); content += f'{index} 0 obj\n'.encode() + value + b'\nendobj\n'
    start = len(content)
    content += b'xref\n0 5\n0000000000 65535 f \n' + b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return content + f'trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()


def png(width=1200, height=750, seed=0):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    raw = b''.join(b'\0' + bytes((x // 4 + y + seed) % 256 for x in range(width * 3)) for y in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class ThumbnailTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('AGENT_HUB_PDFTOPPM') or shutil.which('pdftoppm'), 'PDF integration requires packaged renderer')
    def test_pdf_first_page_is_cached_and_does_not_modify_the_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, 'paper.pdf'); original = pdf(); source.write_bytes(original)
            thumb = Thumbnails()
            value = thumb.render({'cwd':directory}, 'paper.pdf')
            self.assertEqual(value['type'], 'image/jpeg')
            content = base64.b64decode(value['data'])
            self.assertTrue(content.startswith(b'\xff\xd8') and content.endswith(b'\xff\xd9'))
            self.assertLess(len(content), 1024 * 1024)
            with patch.object(thumb, 'generate', side_effect=AssertionError('Repeated PDF decode')):
                self.assertEqual(thumb.render({'cwd':directory}, 'paper.pdf'), value)
            self.assertEqual(source.read_bytes(), original)

    def test_source_edits_replacements_and_cache_bounds_keep_the_original_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            thumb = Thumbnails(limit=2, byte_limit=60)
            with patch.object(thumb, 'generate', return_value=WEBP) as generate:
                source = root / 'plot.png'; source.write_bytes(b'original plot')
                one = thumb.render({'cwd': directory}, 'plot.png')
                self.assertEqual(thumb.render({'cwd': directory}, 'plot.png'), one)
                self.assertEqual(generate.call_count, 1)
                self.assertEqual(source.read_bytes(), b'original plot')
                source.write_bytes(b'revised plot')
                two = thumb.render({'cwd': directory}, 'plot.png')
                self.assertNotEqual(one['etag'], two['etag'])
                replacement = root / 'replace.png'; replacement.write_bytes(source.read_bytes()); replacement.replace(source)
                three = thumb.render({'cwd': directory}, 'plot.png')
                self.assertNotEqual(two['etag'], three['etag'])
                self.assertLessEqual(len(thumb.cache), 2)
                self.assertLessEqual(sum(len(v['data']) for v in thumb.cache.values()), 60)
                source.unlink()
                with self.assertRaises(BridgeError):thumb.render({'cwd':directory}, 'plot.png')

    def test_project_escape_special_files_and_unsupported_formats_never_reach_decoder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / 'project'; project.mkdir()
            (root / 'outside.png').write_bytes(b'private')
            (project / 'escape.png').symlink_to(root / 'outside.png')
            os.mkfifo(project / 'pipe.png')
            (project / 'page.svg').write_text('<svg/>')
            with (project / 'huge.png').open('wb') as file:file.truncate(65*1024*1024)
            thumb = Thumbnails()
            with patch.object(thumb, 'generate') as generate:
                for name in ('../outside.png', str(root / 'outside.png'), 'escape.png', 'pipe.png', 'page.svg', 'huge.png', None):
                    with self.subTest(name=name), self.assertRaises(BridgeError):thumb.render({'cwd':str(project)}, name)
                generate.assert_not_called()

    def test_source_changing_during_generation_never_publishes_stale_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, 'plot.png'); source.write_bytes(b'first')
            thumb = Thumbnails()
            def mutate(*args):source.write_bytes(b'updated'); return WEBP
            with patch.object(thumb, 'generate', side_effect=mutate), self.assertRaisesRegex(BridgeError, 'changed'):
                thumb.render({'cwd':directory}, 'plot.png')
            self.assertFalse(thumb.cache)

    def test_decode_budget_and_process_failure_do_not_cache_a_result(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'plot.png').write_bytes(b'file')
            thumb = Thumbnails()
            with patch.dict(os.environ, {'AGENT_HUB_FFMPEG':'ffmpeg','AGENT_HUB_FFPROBE':'ffprobe','AGENT_HUB_PRLIMIT':'prlimit'}):
                with patch('thumbnails.subprocess.run', side_effect=subprocess.TimeoutExpired('ffprobe', 8)) as process:
                    with self.assertRaisesRegex(BridgeError, 'too costly'):thumb.render({'cwd':directory},'plot.png')
                    args = process.call_args
                    self.assertLessEqual(args.kwargs['timeout'], 8)
                    self.assertIn('--as=805306368', args.args[0])
                    self.assertIn('png_pipe', args.args[0])
                    self.assertTrue(args.kwargs['pass_fds'])
                self.assertFalse(thumb.cache)
                with patch('thumbnails.subprocess.run', return_value=Mock(stdout=b'{"streams":[{"width":10000,"height":10000}]}')) as process:
                    with self.assertRaisesRegex(BridgeError, 'too large'):thumb.render({'cwd':directory},'plot.png')
                    self.assertEqual(process.call_count, 1)

    def test_http_cache_revalidates_source_and_keeps_original_endpoint(self):
        from server import Handler, ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, 'plot.png'); source.write_bytes(b'original')
            thumb = Thumbnails()
            server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
            server.allowed_hosts = {'127.0.0.1'}; server.hub=Mock()
            server.hub.client_allowed.return_value = True
            server.hub.thumbnail.side_effect = lambda agent,path:thumb.render({'cwd':directory},path)
            from media import read_video
            server.hub.video.side_effect = lambda agent,path,*args:read_video({'cwd':directory},path,*args)
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            url=f'http://127.0.0.1:{server.server_port}/api/thumbnail?agent_id=fixture&path=plot.png'
            try:
                with patch.object(thumb,'generate',return_value=WEBP):
                    with urlopen(url) as response:
                        self.assertEqual(response.headers['Content-Type'],'image/webp')
                        self.assertIn('must-revalidate',response.headers['Cache-Control'])
                        self.assertEqual(response.read(),WEBP); etag=response.headers['ETag']
                    with self.assertRaises(HTTPError) as error:urlopen(Request(url,headers={'If-None-Match':etag}))
                    self.assertEqual(error.exception.code,304); error.exception.close()
                    source.write_bytes(b'revised original')
                    with urlopen(Request(url,headers={'If-None-Match':etag})) as response:
                        self.assertEqual(response.status,200); self.assertNotEqual(response.headers['ETag'],etag)
                    with urlopen(url.replace('/api/thumbnail?','/api/file?')) as response:
                        self.assertEqual(response.read(),b'revised original')
            finally:server.shutdown(); server.server_close(); thread.join()

    @unittest.skipUnless(os.environ.get('AGENT_HUB_FFMPEG') or shutil.which('ffmpeg'), 'FFmpeg integration requires packaged tools')
    def test_real_image_thumbnail_and_video_poster_are_small_decodable_webp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'plot.png'; source.write_bytes(png())
            ffmpeg = os.environ.get('AGENT_HUB_FFMPEG') or shutil.which('ffmpeg')
            ffprobe = os.environ.get('AGENT_HUB_FFPROBE') or shutil.which('ffprobe')
            subprocess.run([ffmpeg,'-nostdin','-v','error','-threads','1','-i',str(source),'-frames:v','1',
                            '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(root/'clip.mp4')],check=True,timeout=10)
            thumb = Thumbnails()
            for name in ('plot.png','clip.mp4'):
                value=thumb.render({'cwd':directory},name)
                self.assertEqual(value['poster'],name.endswith('.mp4'))
                target=root/'thumbnail.webp'; target.write_bytes(base64.b64decode(value['data']))
                result=subprocess.run([ffprobe,'-v','error','-show_entries','stream=width,height','-of','json',str(target)],capture_output=True,check=True,timeout=4)
                size=json.loads(result.stdout)['streams'][0]
                self.assertLessEqual(size['width'],640); self.assertLessEqual(size['height'],400)
                self.assertGreater(size['width'],0); self.assertLess(value['size'],1024*1024)
                self.assertEqual(source.read_bytes(),png())


if __name__ == '__main__':unittest.main()
