import os

from django.conf import settings
from xhtml2pdf.files import pisaFileObject


def link_callback(uri, rel):
    if uri.startswith('data:'):
        return uri

    if uri.startswith(settings.STATIC_URL):
        path = os.path.join(settings.STATIC_ROOT, uri.replace(settings.STATIC_URL, ''))
    elif uri.startswith(settings.MEDIA_URL):
        path = os.path.join(settings.MEDIA_ROOT, uri.replace(settings.MEDIA_URL, ''))
    else:
        font_dir = os.path.join(settings.BASE_DIR, 'static/fonts')
        path = os.path.join(font_dir, os.path.basename(uri))
    pisaFileObject.getNamedFile = lambda self: path
    if os.path.isfile(path):
        return path

    return uri
