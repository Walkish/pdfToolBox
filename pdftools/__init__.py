"""PDF toolbox: compression, merging, and image conversion primitives.

Registering the HEIF opener here rather than in ``images.py`` is deliberate:
``validate.py`` opens uploads too and does not import ``images``, so putting the
registration in the decoder module would leave validation unable to read the
very files it is meant to accept, depending on which module got imported first.
One registration at package import serves both.
"""

from pillow_heif import register_heif_opener

register_heif_opener()
