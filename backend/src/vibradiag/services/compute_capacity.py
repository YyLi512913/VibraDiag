"""One process-wide budget shared by M1 analysis and future M2 tool execution."""
from threading import BoundedSemaphore

capacity = BoundedSemaphore(2)
