import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
worker_class = "sync"
workers = 1
threads = 2
timeout = 300
graceful_timeout = 60
keepalive = 5
max_requests = 50
max_requests_jitter = 10
loglevel = "info"
accesslog = "-"
errorlog = "-"
preload_app = False
