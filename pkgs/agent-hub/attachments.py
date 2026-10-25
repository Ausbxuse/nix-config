"""Private image uploads, scoped to their recipient and retained for chat history."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
import warnings

from bridge import BridgeError

MAX_BYTES = 3 * 1024 * 1024
MAX_IMAGES = 4
QUOTA = 512 * 1024 * 1024
IMAGE_HEADER = 'Attached images (open these files to view them):'


def compact(text):
    return ' '.join(text.split())


class Attachments:
    def __init__(self, hub):
        self.hub = hub
        self.root = hub.state_dir / 'images'
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.lock = threading.RLock()
        with hub.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS image_uploads(
                    id TEXT PRIMARY KEY, target TEXT, cwd TEXT, name TEXT,
                    type TEXT, width INTEGER, height INTEGER, size INTEGER, created REAL);
                CREATE TABLE IF NOT EXISTS image_messages(
                    digest TEXT PRIMARY KEY, text TEXT, images TEXT);
                CREATE TABLE IF NOT EXISTS image_message_previews(
                    digest TEXT PRIMARY KEY, target TEXT, cwd TEXT, wire TEXT);
                CREATE INDEX IF NOT EXISTS image_preview_target ON image_message_previews(target,cwd);
            """)
            # Older sends already have durable captions and upload ownership.
            # Recover their wire representation once, without consulting a browser.
            for row in db.execute('SELECT * FROM image_messages WHERE digest NOT IN (SELECT digest FROM image_message_previews)').fetchall():
                images = json.loads(row['images'])
                uploads = [db.execute('SELECT * FROM image_uploads WHERE id=?', (image['id'],)).fetchone() for image in images]
                if not uploads or any(upload is None for upload in uploads):
                    continue
                target, cwd = uploads[0]['target'], uploads[0]['cwd']
                if any((upload['target'], upload['cwd']) != (target, cwd) for upload in uploads):
                    continue
                wire = row['text'] + '\n\n' + IMAGE_HEADER + '\n' + '\n'.join(str(self.path(upload['id'], upload['type'])) for upload in uploads)
                if hashlib.sha256(wire.encode()).hexdigest() == row['digest']:
                    db.execute('INSERT INTO image_message_previews VALUES(?,?,?,?)', (row['digest'], target, cwd, compact(wire)))

    @staticmethod
    def normalize(data):
        from PIL import Image, ImageOps, UnidentifiedImageError
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
            raise BridgeError('Use an image smaller than 3 MB after resizing.')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as source:
                    if source.format not in ('PNG', 'JPEG', 'WEBP', 'GIF'):
                        raise BridgeError('Choose a PNG, JPEG, WebP, or GIF image.')
                    if source.width * source.height > 24_000_000:
                        raise BridgeError('This image is too large. Resize it before attaching.')
                    image = ImageOps.exif_transpose(source)
                    image.thumbnail((2048, 2048))
                    alpha = image.mode in ('RGBA', 'LA') or 'transparency' in image.info
                    image = image.convert('RGBA' if alpha else 'RGB')
                    image.info.clear()
                    # Four encoded images must still fit a normal transcript
                    # record. Keep the history reader's record limit intact.
                    while True:
                        output = io.BytesIO()
                        image.save(output, format='PNG' if alpha else 'JPEG', **({} if alpha else {'quality':90}))
                        if output.tell() <= 2 * 1024 * 1024:
                            break
                        image.thumbnail((max(1, int(image.width * .8)), max(1, int(image.height * .8))))
                    return output.getvalue(), 'image/png' if alpha else 'image/jpeg', image.size
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise BridgeError('That file could not be read as an image.') from None

    def upload(self, target, cwd, name, data):
        # Serialize decoding to bound peak memory, as well as quota reservations.
        with self.lock:
            data, kind, (width, height) = self.normalize(data)
            identity = uuid.uuid4().hex
            name = Path(str(name).replace('\\', '/')).name
            name = ''.join(c for c in name if c.isprintable())[:120] or 'Image'
            with self.hub.db() as db:
                used = db.execute('SELECT COALESCE(SUM(size),0) FROM image_uploads').fetchone()[0]
                if used + len(data) > QUOTA:
                    raise BridgeError('Image storage is full on this host.')
                path = self.path(identity, kind)
                with path.open('xb') as stream:
                    os.chmod(path, 0o600)
                    stream.write(data)
                db.execute('INSERT INTO image_uploads VALUES(?,?,?,?,?,?,?,?,?)',
                           (identity, target, cwd, name, kind, width, height, len(data), time.time()))
            return self.public(self.row(identity))

    def path(self, identity, kind):
        return self.root / (identity + ('.png' if kind == 'image/png' else '.jpg'))

    def row(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[a-f0-9]{32}', identity):
            raise BridgeError('Invalid image attachment.')
        with self.hub.db() as db:
            row = db.execute('SELECT * FROM image_uploads WHERE id=?', (identity,)).fetchone()
        if not row:
            raise BridgeError('Image attachment is unavailable. Attach it again.')
        return dict(row)

    def public(self, row):
        return {**{key:row[key] for key in ('id', 'name', 'width', 'height', 'type', 'size')},
                'host': self.hub.mesh.node_id if self.hub.mesh else 'local'}

    def read(self, identity):
        row = self.row(identity)
        try:
            data = self.path(identity, row['type']).read_bytes()
        except OSError:
            raise BridgeError('Image attachment is unavailable.') from None
        return {'type': row['type'], 'data': base64.b64encode(data).decode()}

    def resolve(self, identities, target, cwd=''):
        if not isinstance(identities, list) or not 1 <= len(identities) <= MAX_IMAGES or any(not isinstance(i, str) for i in identities) or len(set(identities)) != len(identities):
            raise BridgeError('Attach up to four images per message.')
        rows = [self.row(identity) for identity in identities]
        if any(row['target'] != target or row['cwd'] != cwd for row in rows):
            raise BridgeError('These images belong to another conversation. Attach them here again.')
        if any(not self.path(row['id'], row['type']).is_file() for row in rows):
            raise BridgeError('Image attachment is unavailable. Attach it again.')
        return rows

    def prepare(self, message, identities, target, cwd=''):
        rows = self.resolve(identities, target, cwd)
        if not isinstance(message, str) or len(message) > 12000 or '\x00' in message:
            raise BridgeError('Enter a message up to 12,000 characters.')
        message = message.strip() or 'Please look at the attached images.'
        paths = [str(self.path(row['id'], row['type'])) for row in rows]
        # Explicit local references work in Claude and remain useful after a
        # Codex compaction. The browser shows the original caption and thumbnails.
        wire = message + '\n\n' + IMAGE_HEADER + '\n' + '\n'.join(paths)
        if len(wire) > 12000:
            raise BridgeError('Shorten the message to leave room for its image references.')
        images = [self.public(row) for row in rows]
        digest = hashlib.sha256(wire.encode()).hexdigest()
        with self.hub.db() as db:
            db.execute('INSERT OR REPLACE INTO image_messages VALUES(?,?,?)',
                       (digest, message, json.dumps(images)))
            db.execute('INSERT OR REPLACE INTO image_message_previews VALUES(?,?,?,?)',
                       (digest, target, cwd, compact(wire)))
        return wire, paths

    def decorate(self, messages, *, agent=None):
        if not messages:
            return messages
        with self.hub.db() as db:
            candidates = None
            for message in messages:
                if message.get('role', 'user') != 'user':
                    continue
                wire = message.get('text', '')
                row = db.execute('SELECT text,images FROM image_messages WHERE digest=?',
                                 (hashlib.sha256(wire.encode()).hexdigest(),)).fetchone()
                if row:
                    message.update(text=row['text'], attachments=json.loads(row['images']))
                elif agent and message.get('backend') == 'terminal':
                    # The CLI clips queue previews before their file paths. Only
                    # restore a unique saved wire prefix for this pane/project;
                    # captions alone cannot distinguish different photographs.
                    visible = compact(wire)
                    suffix = next((end for end in ('…', '...') if visible.endswith(end)), None)
                    if not suffix or IMAGE_HEADER not in visible:
                        continue
                    prefix = visible[:-len(suffix)].rstrip()
                    if candidates is None:
                        candidates = db.execute('''SELECT p.wire,m.text,m.images FROM image_message_previews p
                            JOIN image_messages m ON m.digest=p.digest WHERE p.target=? AND p.cwd=?''',
                            (agent['id'], agent.get('cwd', ''))).fetchall()
                    matches = [item for item in candidates if item['wire'].startswith(prefix)]
                    if len(matches) == 1:
                        item = matches[0]
                        message.update(text=item['text'], attachments=json.loads(item['images']), attachment_match='preview')
        return messages
