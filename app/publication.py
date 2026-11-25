from app.modules.publication import *  # noqa: F401,F403
from app.modules.publication import assess_publication, snapshot_from_data

# Allow ``from app.publication import ...`` as well as the layered module paths.
build_snapshot = snapshot_from_data
