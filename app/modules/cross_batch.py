from app.modules.publication import *  # noqa: F401,F403
from app.modules.publication import assess_publication, cross_batch_check

check = cross_batch_check
evaluate = assess_publication
