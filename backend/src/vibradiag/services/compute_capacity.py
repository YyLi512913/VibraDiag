"""One admission gate owned by the leased API; all M1/M2 numerical children share two slots."""
from threading import BoundedSemaphore

capacity = BoundedSemaphore(2)
