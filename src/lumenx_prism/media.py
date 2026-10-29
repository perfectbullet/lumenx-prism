"""Prism-owned image files and immutable metadata; never caller supplied disk paths."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4
import hashlib
import io
import os
import warnings

from PIL import Image
from .model_configs import ConfigError

MAX_BYTES = 32 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 32_000_000


class MediaStore:
    def __init__(self, database, root=None):
        self.collection = database['ai_media']
        self.root = Path(root or os.getenv('PRISM_MEDIA_ROOT', 'data/media')).resolve()

    @staticmethod
    def public(row):
        return {k: row[k] for k in ('id', 'kind', 'mime_type', 'size_bytes', 'sha256', 'width', 'height', 'created_at')}

    def put(self, content, *, job_id=None, artifact_key=None):
        if not content or len(content) > MAX_BYTES:
            raise ConfigError('media_size_invalid', 413)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as im:
                    fmt, width, height = im.format, im.width, im.height
                    if fmt not in ('PNG', 'JPEG', 'WEBP') or getattr(im, 'n_frames', 1) != 1:
                        raise ValueError()
                    im.verify()
                with Image.open(io.BytesIO(content)) as im:
                    im.load()
        except Exception:
            raise ConfigError('invalid_image_file', 422) from None
        digest = hashlib.sha256(content).hexdigest()
        # Stable per-job output IDs make result recovery independent of API process lifetime.
        if artifact_key:
            from uuid import uuid5, NAMESPACE_URL
            media_id = str(uuid5(NAMESPACE_URL, f'prism:{job_id}:{artifact_key}:{digest}'))
        else:
            media_id = str(uuid4())
        ext, mime = {'PNG':('png','image/png'),'JPEG':('jpg','image/jpeg'),'WEBP':('webp','image/webp')}[fmt]
        relative = Path('outputs' if job_id else 'inputs') / media_id / ('image.'+ext)
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(str(uuid4())+'.tmp')
        try:
            temporary.write_bytes(content)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        row = dict(_id=media_id, id=media_id, kind='image', mime_type=mime, size_bytes=len(content),
                   sha256=digest, width=width, height=height, relative_path=str(relative), job_id=job_id,
                   created_at=datetime.now(timezone.utc))
        self.collection.update_one({'_id':media_id}, {'$setOnInsert':row}, upsert=True)
        return self.public(self.collection.find_one({'_id':media_id}))

    def get(self, media_id):
        try: media_id = str(UUID(str(media_id)))
        except ValueError: raise ConfigError('media_not_found', 404) from None
        row = self.collection.find_one({'_id':media_id})
        if row is None: raise ConfigError('media_not_found', 404)
        path = (self.root / row['relative_path']).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ConfigError('media_file_unavailable', 404)
        return row, path

    def read_verified(self, media_id, sha256=None):
        row, path = self.get(media_id)
        if path.stat().st_size > MAX_BYTES: raise ConfigError('media_size_invalid', 413)
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != (sha256 or row['sha256']):
            raise ConfigError('media_content_changed', 409)
        return content

    def snapshot(self, ids):
        return [self.public(self.get(str(media_id))[0]) for media_id in ids]
