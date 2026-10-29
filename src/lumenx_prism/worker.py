"""Independent queue consumer: python -m lumenx_prism.worker --env-file .env."""
import argparse
import logging
import os
import signal
import socket
import threading
from uuid import uuid4

from dotenv import load_dotenv

from .extraction import EntityExtractionError, parse_entity_output
from .history import MongoHistory
from .jobs import Jobs
from .llm_adapter import LLMAdapter
from .model_configs import ConfigError, ModelConfigs

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, jobs, configs, adapter_factory=LLMAdapter, lease_seconds=60, media=None):
        self.jobs, self.configs, self.adapter_factory = jobs, configs, adapter_factory
        self.lease_seconds = lease_seconds
        self.worker_id = f'{socket.gethostname()}:{os.getpid()}:{uuid4()}'
        self.image_worker = None
        if media is not None:
            from .image_jobs import ImageWorker
            self.image_worker = ImageWorker(jobs, configs, media, self.worker_id, lease_seconds)

    def run_once(self):
        self.jobs.recover_expired()
        self.jobs.mirror_attempts()
        job = self.jobs.claim(self.worker_id, self.lease_seconds)
        if not job:
            return self.image_worker.run_once() if self.image_worker else False
        done, lost = threading.Event(), threading.Event()

        def heartbeat():
            while not done.wait(self.lease_seconds / 3):
                try:
                    if self.jobs.heartbeat(job, self.lease_seconds):
                        continue
                except Exception:
                    pass
                lost.set()
                return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        adapter = None
        result, error_code, actual_model, usage = None, None, None, None
        try:
            config = self.configs.get(job['model_config']['id'])
            self.configs.validate_entity_model(config)
            if config['revision'] != job['model_config']['revision']:
                raise ConfigError('model_config_changed')
            adapter = self.adapter_factory(config, timeout=120, max_retries=0)
            if lost.is_set() or not self.jobs.invoking(job):
                lost.set()
                return True
            try:
                content = adapter.chat(messages=[{'role': 'user', 'content': job['effective_prompt']}])
            except Exception:
                raise EntityExtractionError('upstream_error') from None
            result = parse_entity_output(content).model_dump()
        except (ConfigError, EntityExtractionError) as exc:
            error_code = exc.code
        except Exception:
            error_code = 'worker_execution_error'
        finally:
            if adapter is not None:
                actual_model, usage = adapter.actual_model, adapter.usage
            # Retry only persistence, never inference. Ownership still fences late results.
            try:
                if not lost.is_set():
                    for attempt in range(3):
                        try:
                            self.jobs.finish(job, result=result, error_code=error_code,
                                             actual_model=actual_model, usage=usage)
                            break
                        except Exception:
                            if attempt == 2:
                                logger.error('Result persistence failed for job %s', job['_id'])
                            else:
                                done.wait(1)
            finally:
                done.set()
                thread.join()
                if adapter is not None and adapter._client is not None:
                    adapter._client.close()
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--poll-seconds', type=float, default=1)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error('--poll-seconds must be positive')
    load_dotenv(args.env_file)
    logging.basicConfig(level=logging.INFO)
    repo = MongoHistory(os.environ['PRISM_MONGODB_URI'], os.getenv('PRISM_MONGODB_DATABASE', 'lumenx_prism'))
    jobs = Jobs(repo.client['lumenx_prism'])
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    from .media import MediaStore
    worker = Worker(jobs, ModelConfigs(repo.client['lumenx_prism']), media=MediaStore(repo.client['lumenx_prism']))
    try:
        jobs.ensure_indexes()
        logger.info('Worker started: %s', worker.worker_id)
        while not stop.is_set():
            try:
                if worker.run_once():
                    continue
            except Exception:
                logger.error('Queue unavailable; waiting before retry')
            stop.wait(args.poll_seconds)
        jobs.mirror_attempts()
    finally:
        repo.close()


if __name__ == '__main__':
    main()
